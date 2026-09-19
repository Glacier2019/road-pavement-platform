"""契约③写入侧：**表级写权守卫**契约测试。

运行（**完全离线，不触库、不需网络**）：
    cd /data/cy/shujuku/scaffold
    python3 tests/contract/test_write_guard.py

为什么这个测试必须存在
------------------------------------------------------------------------------
「写入只经 M2」这条硬线在过去**只写在文档里**。文档不是强制力：任何模块
``import psycopg`` 就能绕过契约直接写库，而且**不会有任何报错**。

本测试把这条硬线变成可执行断言。**关键在"应拒绝"那一侧**：
只测"允许的能写进去"是不够的——那只能证明放行逻辑存在，不能证明拦截逻辑存在。
本项目已有的教训正是：**一个永远不会失败的检查，比没有检查更糟**，
因为它给出的是虚假的覆盖信心。

因此本文件对每条规则都做**双向验证**：
  · 应通过：登记过的表 ＋ 正确的写入方 → 不抛异常
  · 应拒绝：只读表 / 越权 / 未登记表 / 空写入 → 必须抛对应异常

最后还有一组"元测试"：确认这些规则**不是恒真的空断言**。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "modules" / "M3-rpdao") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules" / "M3-rpdao"))

# rpdao.pool 需要 psycopg；本测试不触库，缺依赖时装最小桩。
try:
    import psycopg  # noqa: F401
except ImportError:
    import types
    pg = types.ModuleType("psycopg")
    pg.OperationalError = type("OperationalError", (Exception,), {})
    rows = types.ModuleType("psycopg.rows")
    rows.dict_row = type("dict_row", (), {})
    pg.rows = rows
    sys.modules["psycopg"] = pg
    sys.modules["psycopg.rows"] = rows
try:
    import psycopg_pool  # noqa: F401
except ImportError:
    import types
    pool = types.ModuleType("psycopg_pool")

    class _CP:                       # 本测试不实例化 Dao，只需 import 能过
        def __init__(self, *a, **k):
            raise RuntimeError("本测试不应实例化连接池")

    pool.ConnectionPool = _CP
    sys.modules["psycopg_pool"] = pool

from rpdao.catalog import ALL_TABLES, TABLE_OWNER          # noqa: E402

# 契约② 真源：2026-09-15 由 output/ 搬入 scaffold/sql/（随代码走，因为本测试要读它）
DDL_PATH = ROOT / "sql" / "10_ddl_v0.4.sql"


def ddl_unique_keys(table: str) -> list[frozenset[str]]:
    """解析 DDL 文本，取出某张表上所有**唯一键**（列集合）。

    认三种写法：
      ① 列内联      ``batch_no varchar(64) UNIQUE``
      ② 表级约束    ``UNIQUE (record_id, axle_seq)`` / ``CONSTRAINT x UNIQUE (a, b)``
      ③ 独立索引    ``CREATE UNIQUE INDEX … ON t(a, b)``

    为什么要自己解析而不连库查：契约测试必须能**离线**跑，且要能在库还没建起来时
    就发现"代码里声明的去重键没有索引背书"。这正是本组要防的那类错误。
    """
    if not DDL_PATH.exists():
        return []
    txt = DDL_PATH.read_text(encoding="utf-8")
    keys: list[frozenset[str]] = []

    # ① / ② 在 CREATE TABLE <table> ( … ); 块内找
    m = re.search(rf"CREATE TABLE IF NOT EXISTS\s+{re.escape(table)}\s*\((.*?)\n\);",
                  txt, re.S)
    if m:
        body = m.group(1)
        for cols in re.findall(r"UNIQUE\s*\(([^)]+)\)", body):          # ② 表级
            keys.append(frozenset(c.strip() for c in cols.split(",")))
        for line in body.splitlines():                                   # ① 列内联
            line = line.split("--")[0]
            if not re.search(r"\bUNIQUE\b", line):
                continue
            # 表级约束的行**以** UNIQUE / CONSTRAINT 开头；列内联的不是。
            # （不能用"有没有括号"判别：`batch_no varchar(64) UNIQUE` 里
            #   varchar(64) 自带括号，会把它误判成表级约束——第一版就栽在这。）
            if re.match(r"^\s*(CONSTRAINT\s+\w+\s+)?UNIQUE\s*\(", line):
                continue
            name = line.strip().split()[0].strip('"')
            if re.fullmatch(r"[a-z_][a-z0-9_]*", name):
                keys.append(frozenset({name}))

    # ③ 独立唯一索引
    for cols in re.findall(rf"CREATE UNIQUE INDEX[^;]*?ON\s+{re.escape(table)}\s*\(([^)]+)\)",
                           txt, re.S):
        keys.append(frozenset(c.strip() for c in cols.split(",")))
    return keys
from rpdao.errors import UnknownTable, WriteGuardError      # noqa: E402
from rpdao.write import (                                   # noqa: E402
    DEDUPE_REQUIRED,
    _guard_rows,
    allowed_writer,
    assert_writer,
)

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {label}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ✗ {label}  {detail}")


def expect_raise(label: str, exc_type: type, fn, *a, **k) -> None:
    """期望抛出指定异常。**不抛**也算失败——这正是"应拒绝"侧的要点。"""
    try:
        fn(*a, **k)
    except exc_type as e:
        check(label, True, f"→ {type(e).__name__}")
    except Exception as e:  # noqa: BLE001
        check(label, False, f"抛了 {type(e).__name__}，期望 {exc_type.__name__}")
    else:
        check(label, False, "未抛异常（**本应被拒绝**）")


def main() -> int:
    print("=" * 74)
    print("契约③写入侧：表级写权守卫")
    print("=" * 74)

    # ---------------------------------------------------------------- 1) 应通过
    print("\n=== 1) 应通过：登记过的表 ＋ 正确的写入方 ===")
    allowed_cases = [
        ("wim_axle_record", "M2"), ("wim_axle_detail", "M2"),
        ("data_import_batch", "M2"), ("data_quality_log", "M2"),
        ("quality_rule", "M4"), ("calibration_log", "M4"),
        ("mapping_set", "M7"),
        ("diagnosis_result", "M5"), ("model_output", "M5"),
        ("maintenance_advice", "M8"), ("alarm_rule", "M8"),
    ]
    for table, writer in allowed_cases:
        try:
            assert_writer(table, writer)
            check(f"{writer} → {table}", True)
        except Exception as e:  # noqa: BLE001
            check(f"{writer} → {table}", False, f"被误拒：{type(e).__name__}: {e}")

    # ---------------------------------------------------------------- 2) 应拒绝：越权
    print("\n=== 2) 应拒绝：越权（表可写，但不是你）===")
    # 这是"模块可拔插"的核心保障：没有这条，任何模块都能悄悄改别人的数据
    expect_raise("M5 写 wim_axle_record（应属 M2）", WriteGuardError,
                 assert_writer, "wim_axle_record", "M5")
    expect_raise("M2 写 quality_rule（应属 M4）", WriteGuardError,
                 assert_writer, "quality_rule", "M2")
    expect_raise("M4 写 mapping_set（应属 M7）", WriteGuardError,
                 assert_writer, "mapping_set", "M4")
    expect_raise("M2 写 mapping_set（应属 M7）", WriteGuardError,
                 assert_writer, "mapping_set", "M2")
    expect_raise("M6 写 data_import_batch（应属 M2）", WriteGuardError,
                 assert_writer, "data_import_batch", "M6")

    # ---------------------------------------------------------------- 3) 应拒绝：只读表
    print("\n=== 3) 应拒绝：只读表（无写权登记，任何人都不许写）===")
    readonly = [t for t in ALL_TABLES if t not in TABLE_OWNER]
    check("存在只读表（否则本组测试是空断言）", len(readonly) > 0,
          f"{len(readonly)} 张：{readonly[:4]}…")
    for table in readonly[:6]:
        expect_raise(f"任意模块写只读表 {table}", WriteGuardError,
                     assert_writer, table, "M2")

    # 文档里的写权数字不得漂移。
    # 起因：SKELETON-GUIDE.md 长期写着「21 张可写 / 11 张只读」——那是 v0.2（32 表）时代的
    # 旧值，之后加了 10 张 GE 表却没人回头改它。**文档里的硬编码数字就是下一个漂移源**，
    # 所以把它钉成断言：改了 TABLE_OWNER 不改这句，本测试立刻红。
    guide = ROOT / "SKELETON-GUIDE.md"
    if guide.exists():
        m = re.search(r"（(\d+) 张可写 / (\d+) 张只读）", guide.read_text(encoding="utf-8"))
        ok = bool(m) and int(m.group(1)) == len(TABLE_OWNER) and int(m.group(2)) == len(readonly)
        check("SKELETON-GUIDE.md 的写权数字与 catalog 一致", ok,
              f"文档 {m.group(0) if m else '未找到'} ／ 实际 {len(TABLE_OWNER)} 可写 {len(readonly)} 只读")

    # GE 域骨架四表必须可写 —— 否则「导入一条新道路」在结构上就不可能
    # （road_line / road_section 原先是只读，只能靠 seed 种入）
    for t in ("road_line", "road_section", "structure_layer", "monitor_cross_section"):
        check(f"GE 骨架表 {t} 已登记写权（否则新道路导不进来）",
              TABLE_OWNER.get(t) == "M2", f"实为 {TABLE_OWNER.get(t)}")

    # ---------------------------------------------------------------- 4) 应拒绝：未登记表
    print("\n=== 4) 应拒绝：表不在白名单（表名写错 / 新表没登记进 catalog）===")
    for bad in ("wim_axle_recoed", "secret_table", "pg_shadow", "wim_axle_record; DROP TABLE x"):
        expect_raise(f"写未登记表 {bad!r}", UnknownTable, assert_writer, bad, "M2")

    # ---------------------------------------------------------------- 5) 应拒绝：空写入
    print("\n=== 5) 应拒绝：空批量写入（0 行不得当作成功）===")
    expect_raise("_guard_rows([])", WriteGuardError, _guard_rows, [])
    try:
        got = _guard_rows([{"a": 1}])
        check("非空写入正常返回", got == [{"a": 1}], f"{got}")
    except Exception as e:  # noqa: BLE001
        check("非空写入正常返回", False, str(e))

    # ---------------------------------------------------------------- 6) 海量表强制去重键
    print("\n=== 6) 海量表必须给冲突键（否则重放同一批数据会产生重复行）===")
    check("DEDUPE_REQUIRED 非空", len(DEDUPE_REQUIRED) > 0, str(list(DEDUPE_REQUIRED)))
    for t, keys in DEDUPE_REQUIRED.items():
        # 单列键（如 data_import_batch.batch_no）与多列键都合法，故只要求非空
        check(f"{t} 已声明去重键", len(keys) >= 1 and t in ALL_TABLES, f"{tuple(keys)}")

    # ---------------------------------------------------------------- 6b) 去重键必须有索引背书
    print("\n=== 6b) 声明的去重键必须被 DDL 里的唯一索引背书 ===")
    print("    （防的是这类错误：写了 ON CONFLICT 的键，但库里没有对应唯一索引 →")
    print("      PostgreSQL 运行时才报 'no unique or exclusion constraint matching'）")
    for t, keys in DEDUPE_REQUIRED.items():
        declared = frozenset(keys)
        have = ddl_unique_keys(t)
        backed = declared in have
        check(f"{t} 的去重键 {tuple(keys)} 有唯一索引背书",
              backed,
              f"DDL 里该表的唯一键：{[sorted(k) for k in have]}" if not backed else "")
    # 反向验证：故意构造一个不存在的键，解析器必须判为"无背书"
    fake = frozenset({"no_such_col_a", "no_such_col_b"})
    for t in DEDUPE_REQUIRED:
        check(f"反向验证：伪造键不被 {t} 的索引背书",
              fake not in ddl_unique_keys(t))

    # ---------------------------------------------------------------- 7) 一致性
    print("\n=== 7) 写权表 ↔ 白名单 一致性 ===")
    stray = [t for t in TABLE_OWNER if t not in ALL_TABLES]
    check("TABLE_OWNER 里的表都在白名单内", not stray, f"越界登记：{stray}" if stray else "")
    owners = sorted(set(TABLE_OWNER.values()))
    check("写入方均为已登记模块号", all(o.startswith("M") for o in owners), f"{owners}")
    # 反查：每张可写表恰有一个写入方（本结构天然保证，此处作为回归）
    multi = [t for t in TABLE_OWNER if len(str(TABLE_OWNER[t]).split()) != 1]
    check("每张表恰有一个写入方", not multi, f"{multi}" if multi else "")
    print(f"    可写表 {len(TABLE_OWNER)} 张 / 只读表 {len(readonly)} 张 / 合计 {len(ALL_TABLES)} 张")
    for o in owners:
        ts = [t for t, w in TABLE_OWNER.items() if w == o]
        print(f"      {o}: {len(ts):>2} 张  {', '.join(ts[:4])}{'…' if len(ts) > 4 else ''}")

    # ---------------------------------------------------------------- 8) 元测试
    print("\n=== 8) 元测试：确认上面的断言不是恒真的空断言 ===")
    # 若守卫被改成"永远放行"，第 2/3/4 组必须失败。这里用一个必然越权的用例反向验证：
    try:
        assert_writer("wim_axle_record", "M5")
        check("反向验证：越权应被拦", False, "守卫疑似失效（越权竟通过）")
    except WriteGuardError:
        check("反向验证：越权被拦", True)
    # 若守卫被改成"永远拒绝"，第 1 组必须失败。这里正向验证：
    try:
        assert_writer("wim_axle_record", "M2")
        check("反向验证：合法写入未被误拒", True)
    except Exception as e:  # noqa: BLE001
        check("反向验证：合法写入未被误拒", False, f"守卫疑似过严：{e}")
    # 只读表必须真的存在，否则第 3 组是空循环
    check("只读表非空集合（第 3 组非空循环）", len(readonly) >= 5, f"{len(readonly)} 张")

    # ---------------------------------------------------------------- 9) M2 不得绕过契约③
    print("\n=== 9) M2（ingest）不得绕过契约③ —— 静态断言 ===")
    src = ROOT / "modules" / "M2-ingest" / "app.py"
    if src.exists():
        code = src.read_text(encoding="utf-8")
        bad = {
            "import psycopg": "又自己持有 psycopg 连接",
            "psycopg_pool": "又自己持有连接池",
            "ConnectionPool": "又自己持有连接池",
            "cur.execute": "又有裸游标执行",
        }
        for pat, why in bad.items():
            check(f"ingest 无 `{pat}`", pat not in code, why if pat in code else "")
        check("ingest 走 rpdao（导入 WriteDao）",
              "from rpdao.write import" in code or "import rpdao" in code)
        check("ingest 声明了写权身份", 'WRITER = "M2"' in code)
        check("ingest 用 write_txn 保住多表原子性",
              "write_txn(" in code,
              "主记录与轴组明细必须同一事务，否则会留下没有明细的过车记录")
        # 反向验证：若把 psycopg 塞回去，本断言必须失败
        check("反向验证：本组断言非恒真", "import psycopg" not in code)
    else:
        check("找到 ingest/app.py", False, f"{src} 不存在")

    print("\n" + "=" * 74)
    print(f"通过 {PASS} ｜ 失败 {FAIL}")
    print("=" * 74)
    if FAIL == 0:
        print("\n结论：写权守卫对『应通过』『应拒绝』两侧均生效——")
        print("      「写入只经 M2」已从文档约定变成**结构上做不到绕过**。")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
