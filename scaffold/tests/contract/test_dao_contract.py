"""契约③：M3 对象域数据访问层（DAO）契约测试。

运行（离线可跑，不需要容器、不需要网络）：
  cd /data/cy/shujuku/scaffold
  uv run --with "psycopg[binary,pool]==3.2.3" python tests/contract/test_dao_contract.py
  # 本机已有 psycopg 时直接 python3 亦可（缺依赖会自动跳过对应检查并说明）

本测试把四条**架构约束**从"文档里的约定"变成"可执行断言"：

  1) 域目录自洽：7 域表数 ＋ 跨域支撑表数 = DDL 实际表数；
  2) 目录 ↔ DDL 双向对齐：DDL 里每张表都在目录中登记，反之亦然；
  3) 越域/越界取数被拒：GE 域仓储不能查 LO 域的表，没登记的表一律拒绝，
     非法标识符不得拼进 SQL（防注入）；
  4) M6 不直连存储：modules/M6-api/app.py 里不得出现 psycopg / ConnectionPool；
  5) SQL 参数类型回归：可选筛选项必须显式 cast。

第 5 条是**骨架期真实踩坑的回归保护**：`WHERE (%(x)s IS NULL OR ...)` 在参数传 NULL 时
PostgreSQL 推断不出类型，抛 AmbiguousParameter，导致 /v1/objects/wim_axle 与
/v1/metrics/wim_hourly **恒定返回 500**。语法编译查不出、报文契约测试不碰 HTTP，
只有真发一次请求才暴露——所以这里用静态断言把它钉死。
"""
from __future__ import annotations

import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "modules" / "M3-rpdao") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules" / "M3-rpdao"))

try:
    import psycopg  # noqa: F401
except ImportError:  # rpdao.pool 需要它；缺了就装上最小桩（本测试不触库）
    import types
    pg = types.ModuleType("psycopg")
    pg.OperationalError = type("OperationalError", (Exception,), {})
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = type("dict_row", (), {})
    pg.rows = rows
    pool = types.ModuleType("psycopg_pool")

    class ConnectionPool:  # 只被构造，不建连接
        def __init__(self, *a, **k): pass
        def open(self): pass
        def close(self): pass
    pool.ConnectionPool = ConnectionPool
    sys.modules.update({"psycopg": pg, "psycopg.rows": rows, "psycopg_pool": pool})
    print("⚠ 未检测到 psycopg，已用桩替代（本测试不触库，不影响结论）\n")

from rpdao import (  # noqa: E402
    ALL_TABLES, CROSS_TABLES, DOMAINS, ContractViolation, Dao, NotFound,
    UnknownDomain, UnknownTable, domain_of, selfcheck,
)
from rpdao.pool import quote_ident  # noqa: E402
from rpdao import repo as repo_mod  # noqa: E402

# 契约② 真源就在本仓库内（scaffold/sql/），不依赖 output/——
# output/ 是交付物目录、已被 .gitignore 排除，读它会让本测试在干净检出上静默跳过。
# 历史：本行原为 ROOT.parent/"output"/"路面性能数据库-DDL-v0.2.sql"（重构前的旧位置），
#       v0.3 修正为真源路径（契约变更工单 #2）。
DDL_PATH = ROOT / "sql" / "10_ddl_v0.5.sql"
API_APP = ROOT / "modules" / "M6-api" / "app.py"


def ddl_tables() -> list[str]:
    if not DDL_PATH.exists():
        return []
    return re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)",
                      DDL_PATH.read_text(encoding="utf-8"))


def ddl_columns(table: str) -> set[str]:
    """离线从 DDL 里取某张表的列名（不触库，干净检出上也能跑）。"""
    if not DDL_PATH.exists():
        return set()
    body = re.search(
        r"CREATE TABLE IF NOT EXISTS\s+" + re.escape(table) + r"\s*\((.*?)\n\);",
        DDL_PATH.read_text(encoding="utf-8"), re.S)
    if not body:
        return set()
    cols: set[str] = set()
    for line in body.group(1).splitlines():
        m = re.match(r"\s*([a-z_][a-z0-9_]*)\s+[a-zA-Z]", line)
        if m and m.group(1).upper() not in (
                "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "CONSTRAINT"):
            cols.add(m.group(1))
    return cols


def bad_anchor_columns(anchor: dict[str, tuple[str, str]]) -> list[str]:
    """检查声明的**锚定列**是否真实存在于它声明的那张表里。

    为什么单拎成一个函数：这条检查**真的抓到过 bug**。
    `profile_ground_point` 与 `geometry_point` 锚在 `station_sequence.id` 上，
    而不是 `section_id`（它们是逐桩数据，桩号才是它们的父）。按 `section_id` 写会抛
    UndefinedColumn，一眼可见；可若改成"猜不到就当 0"的写法，表现就是**静默的 0**，
    也就是几何等级虚低 —— 那才难查。

    比对的是**声明出来的数据**（`SEGMENT_ANCHOR`），不是解析出来的 SQL。
    第一版比的是手写 SQL，结果被**子查询里的** `WHERE section_id =`（那是
    `station_sequence` 的列）假报了一次 —— 检查器自身的假报比漏报更消耗信任，
    所以把锚定列改成声明式，检查也就变成了单纯的数据比对。
    """
    bad: list[str] = []
    for seg, (table, col) in anchor.items():
        cols = ddl_columns(table)
        if not cols:
            bad.append(f"{seg}: DDL 里没有表 {table}")
        elif col not in cols:
            bad.append(f"{seg}: 表 {table} 没有列 {col}")
    return bad


def main() -> int:
    fails: list[str] = []

    # ------------------------------------------------------ 1) 域目录自洽
    print("=== 1) 域目录自检 ===")
    sc = selfcheck()
    for k in ("domains", "domain_physical_tables", "cross_tables", "physical_total"):
        print(f"  {k:<26}{sc[k]}")
    if sc["ok"]:
        print(f"  ✓ 自洽：{sc['domain_physical_tables']} 域内 ＋ {sc['cross_tables']} 跨域 "
              f"= {sc['physical_total']}，与 DDL 口径一致")
    else:
        print(f"  ✗ 不自洽：{sc}")
        fails.append(f"域目录不自洽 {sc}")

    print("\n  各域表数（报告口径 GE5/SU4/RE2/LO5/WE1/TE3/DE5 = 25 逻辑视图）：")
    for code, d in DOMAINS.items():
        n_logical = len(d.tables) + len(d.logical)
        print(f"    {code} {d.name:<6} 已建物理表 {len(d.tables)}  逻辑视图合计 {n_logical}  "
              f"落库 {d.storage}")

    # ------------------------------------------------------ 2) 目录 ↔ DDL 对齐
    print("\n=== 2) 目录 ↔ DDL 文件 双向对齐 ===")
    ddl = ddl_tables()
    if not ddl:
        print(f"  ⚠ 未找到 DDL 文件 {DDL_PATH}，跳过（不影响其它检查）")
    else:
        cat, dd = set(ALL_TABLES), set(ddl)
        only_ddl, only_cat = sorted(dd - cat), sorted(cat - dd)
        print(f"  DDL 声明 {len(dd)} 张 / 目录登记 {len(cat)} 张")
        if only_ddl:
            print(f"  ✗ DDL 有、目录没登记：{only_ddl}")
            fails.append(f"目录漏登记：{only_ddl}")
        if only_cat:
            print(f"  ✗ 目录有、DDL 里没有：{only_cat}")
            fails.append(f"目录多于 DDL：{only_cat}")
        if not only_ddl and not only_cat:
            print("  ✓ 双向一致")

    # ------------------------------------------------------ 3) 越域/越界被拒
    print("\n=== 3) 越域 / 越界 / 非法标识符 一律拒绝 ===")
    dao = Dao("postgresql://unused", app_name="dao-contract-test")  # 不 open，不会触库

    cases = [
        ("GE 域仓储查 LO 域的表", lambda: dao.ge.list_objects("wim_axle_record"), ContractViolation),
        ("LO 域仓储查不存在的表", lambda: dao.lo.list_objects("no_such_table"), UnknownTable),
        ("取未登记的对象域", lambda: dao.domain("XX"), UnknownDomain),
        ("非法标识符（大写/引号注入）", lambda: quote_ident('a"; DROP TABLE x --'), ContractViolation),
        ("非法标识符（数字开头）", lambda: quote_ident("1abc"), ContractViolation),
    ]
    for label, fn, want in cases:
        try:
            fn()
        except want as exc:
            print(f"  ✓ {label:<28}→ {type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001
            print(f"  ✗ {label:<28}→ 抛了 {type(exc).__name__}，期望 {want.__name__}")
            fails.append(f"{label} 异常类型不符")
        else:
            print(f"  ✗ {label:<28}→ 未抛异常（应被拒绝）")
            fails.append(f"{label} 未被拒绝")

    print("  域仓储构造：", end="")
    made = []
    for code in DOMAINS:
        r = dao.domain(code)
        made.append(f"{code}={type(r).__name__}")
    print("，".join(made))

    # ------------------------------------------------------ 4) M6 不直连存储
    print("\n=== 4) M6（api）不得直连存储 —— 这是 M3 落地的意义所在 ===")
    # 用 AST 判定而非正则：正则会把注释/文档字符串里的词误判成代码
    # （本次自测就踩到过——"骨架期本文件自己持有 ConnectionPool" 是句注释）。
    tree = ast.parse(API_APP.read_text(encoding="utf-8"))
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            problems += [f"import {a.name}" for a in node.names if a.name.split(".")[0] == "psycopg"]
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "psycopg":
                problems.append(f"from {node.module} import ...")
        elif isinstance(node, ast.Name) and node.id == "ConnectionPool":
            problems.append("用 ConnectionPool")
        elif isinstance(node, ast.Attribute) and node.attr == "execute":
            problems.append("直接调 .execute(")
        elif isinstance(node, ast.Name) and node.id in ("cursor",):
            problems.append("持有 cursor")
    if problems:
        for q in sorted(set(problems)):
            print(f"  ✗ {q} → M6 又回去直连库了")
            fails.append(f"api/app.py 直连存储：{q}")
    else:
        print("  ✓ 无 psycopg import、无 ConnectionPool、无 .execute(、无 cursor")
        print("  ✓ 结论：M6 只能经 rpdao 取数，**结构上**无法直连库")

    # ------------------------------------------------------ 5) SQL 参数类型回归
    print("\n=== 5) 可选筛选项必须显式 cast（骨架期恒定 500 的根因）===")
    checked = 0
    for name in dir(repo_mod):
        cls = getattr(repo_mod, name)
        if not (isinstance(cls, type) and hasattr(cls, "PASSAGES_SQL")):
            continue
        for attr in dir(cls):
            if not attr.endswith("_SQL"):
                continue
            sql = getattr(cls, attr)
            if not isinstance(sql, str):
                continue
            # 形如 "(%(x)s IS NULL" 的占位符，必须在同一段 SQL 里带 ::类型
            # 注意：cast 是夹在占位符与 IS NULL 之间的（%(x)s::timestamptz IS NULL），
            # 故 cast 组必须可选——否则一处都扫不到，检查会静默失效（本次自测踩过）。
            for m in re.finditer(r"%\((\w+)\)s(?:::(\w+))?\s+IS\s+NULL", sql):
                param, cast = m.group(1), m.group(2)
                checked += 1
                if not cast:
                    print(f"  ✗ {cls.__name__}.{attr} 的 %({param})s 未加 cast"
                          f"（参数传 NULL 时会抛 AmbiguousParameter → 接口 500）")
                    fails.append(f"{cls.__name__}.{attr} 缺 cast：{param}")
    if checked and not any("缺 cast" in f for f in fails):
        print(f"  ✓ {checked} 处 IS NULL 占位符全部带显式 cast")
    elif not checked:
        print("  ⚠ 未扫到 IS NULL 占位符（SQL 结构可能变了，请复查本检查是否还有效）")

    # ------------------------------------------- 6) GE 域仓储（以路段为根的树）
    print("\n=== 6) GE 域仓储：几何是按路段组织的树，不是平铺对象 ===")

    def ok(label: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label}" + (f"　{detail}" if detail else ""))
            fails.append(label)

    ok("GeRepository 已注册进 _BUILDERS",
       repo_mod._BUILDERS.get("GE") is repo_mod.GeRepository)
    ok("GeRepository 已导出（from rpdao import GeRepository）",
       getattr(__import__("rpdao"), "GeRepository", None) is repo_mod.GeRepository)
    ok("dao.ge 解析成 GeRepository（域仓储属性）",
       isinstance(Dao("postgresql://x/y", app_name="t").ge, repo_mod.GeRepository))
    ok("dao.GE 与 dao.ge 同源（domain() 大小写不敏感）",
       type(Dao("postgresql://x/y", app_name="t").domain("GE")) is repo_mod.GeRepository)

    # ★ 等级规则 M3/M2 各写一遍（M3 不许 import M2），用测试把两遍钉在一起
    sys.path.insert(0, str(ROOT / "modules" / "M2-ingest"))
    from adapters import base as m2base                       # noqa: PLC0415
    ok("★ 交叉核对：GeRepository.LEVEL_RULES == M2 base._LEVEL_RULES",
       tuple(repo_mod.GeRepository.LEVEL_RULES) == tuple(m2base._LEVEL_RULES),
       f"M3={repo_mod.GeRepository.LEVEL_RULES} M2={m2base._LEVEL_RULES}")

    # 只对"规则数据"还不够：两边可以一个写成 any、一个写成 all，上面那条照样全绿，
    # 而同一个路段在两处会得到**不同的等级**。所以再拿同一批用例对拍判定逻辑本身。
    _modes = {lv: m for lv, _segs, m in repo_mod.GeRepository.LEVEL_RULES}
    ok("★★ L3 的组合方式是 all（只给地面线不得算作'有纵断面设计'）",
       _modes.get("L3") == "all", f"实为 {_modes.get('L3')}：{_modes}")
    ok("★★ L2 的组合方式仍是 any（刻意保留：交点链已足以定出平面线形）",
       _modes.get("L2") == "any", f"实为 {_modes.get('L2')}")

    _cases = [
        ((), set(), "any", False),
        (("a",), {"a"}, "any", True),
        (("a",), set(), "any", False),
        (("a", "b"), {"a"}, "any", True),
        (("a", "b"), {"a"}, "all", False),
        (("a", "b"), {"b"}, "all", False),
        (("a", "b"), {"a", "b"}, "all", True),
        ((), set(), "all", True),
    ]
    _diff = [c for c in _cases
             if repo_mod.GeRepository.level_hit(c[0], c[1], c[2])
             != m2base.level_hit(c[0], c[1], c[2])]
    ok("★★ 交叉核对：M3 与 M2 的等级判定逻辑逐个用例一致（不只是规则数据）",
       not _diff, f"不一致 {_diff}")

    # 元测试：把 L3 改回 any 时，上面那条"必须是 all"的检查会失败吗？
    # 不验这一下，就无法排除"检查写得永远为真"。
    _l3req = next(segs for lv, segs, _m in repo_mod.GeRepository.LEVEL_RULES if lv == "L3")
    ok("★★ 元测试：L3 若改回 any，'只给地面线'就会判为命中（说明该检查非摆设）",
       repo_mod.GeRepository.level_hit(_l3req, {"profile_ground_point"}, "any") is True
       and repo_mod.GeRepository.level_hit(_l3req, {"profile_ground_point"}, "all") is False)
    m3_segs = set(repo_mod.GeRepository.SEGMENT_ANCHOR) | set(
        repo_mod.GeRepository.SEGMENTS_NOT_BUILT)
    ok("★ 交叉核对：M3 覆盖的段集合 == M2 声明的 SEGMENTS（不多不少）",
       m3_segs == set(m2base.SEGMENTS),
       f"M3 独有 {m3_segs - set(m2base.SEGMENTS)}；M2 独有 {set(m2base.SEGMENTS) - m3_segs}")

    # ★★ 抓过 bug 的检查：声明的锚定列必须真实存在于它声明的那张表里
    anchor = repo_mod.GeRepository.SEGMENT_ANCHOR
    bad = bad_anchor_columns(anchor)
    ok("★★ 每个段的锚定列都真实存在于它声明的表里", not bad, str(bad))
    # 元测试：喂一份故意写错的锚定，检查必须报出来 —— 否则它是个摆设
    probe = bad_anchor_columns({"probe": ("profile_ground_point", "section_id")})
    ok("元测试：故意写错锚定列时该检查**确实会报**（不是摆设）",
       len(probe) == 1 and "没有列 section_id" in probe[0], str(probe))
    ok("元测试：表名不存在时也会报",
       len(bad_anchor_columns({"p": ("无此表", "id")})) == 1)
    ok("元测试：正确的锚定列不会被误报（阈值不过紧）",
       not bad_anchor_columns({"p": ("profile_ground_point", "station_id")}))
    ok("锚定列只有两种取值（section_id / station_id），与 STATION_ANCHOR 声明一致",
       {c for _, c in anchor.values()} == {"section_id", repo_mod.GeRepository.STATION_ANCHOR})
    ok("SEGMENT_ANCHOR 的表全部属于 GE 域（没有指向别的域的表）",
       {t for t, _ in anchor.values()} <= set(DOMAINS["GE"].tables))
    ok("count_sql 由声明生成，且逐桩表走 station_sequence 中转",
       "IN (SELECT id FROM station_sequence" in repo_mod.GeRepository.count_sql("geometry_point")
       and "IN (SELECT id" not in repo_mod.GeRepository.count_sql("alignment_pi"))
    try:
        repo_mod.GeRepository.count_sql("no_such_segment")
    except KeyError:
        print("  ✓ 未声明的段 → KeyError（不会被当成 0 行）")
    else:
        print("  ✗ 未声明的段未抛 KeyError")
        fails.append("未声明的段未抛 KeyError")

    ok("GE 不提供「无 section 的平铺查」（路段一多就会静默串台）",
       not any(hasattr(repo_mod.GeRepository, m)
               for m in ("all_pis", "all_elements", "all_stations", "all_sections_flat")))
    ok("SEGMENTS_NOT_BUILT 只声明了 v0.4 的横断面（与契约② 批次一致）",
       repo_mod.GeRepository.SEGMENTS_NOT_BUILT == ("cross_section",))

    print("\n结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
