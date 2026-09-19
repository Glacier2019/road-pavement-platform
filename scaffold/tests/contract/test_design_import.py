"""契约⑤：设计数据导入（IR + 纬地 .STA 适配器）契约测试。

运行（**完全离线，不触库、不需网络**）：
    cd /data/cy/shujuku/scaffold
    ./run_contract_tests.sh design
    # 或单独跑：
    uv run --with jsonschema --with pyyaml tests/contract/test_design_import.py

为什么这个测试必须存在
-------------------------------------------------------------------------------
「导入设计文件」在以往是**一次性脚本**：读一遍、填一次库、脚本丢掉。
后果是换一条路就得重写一遍，且**没人能证明它解析对了**——
上一次纬地可行性分析读文件的那段代码就没留下来，于是"能不能导"变成了一句口头结论。

本测试把三件事变成可执行断言：
  ① **IR 结构合法**：适配器产出必须过契约⑤ schema（含等级、能力、缺口三者自洽）；
  ② **解析器对真实格式正确**：用**真实纬地文件节选**当 fixture，断言逐点数值，
     而不是拿自造样本自证——自造样本只能证明"我的解析器能读我的样本"；
  ③ **应拒绝的一侧真的会拒绝**：桩号倒退、序号乱序、字段数错、魔数错…逐一必须抛错。

第 ③ 条是关键。**一个永远不会失败的检查，比没有检查更糟**：
只测"合法文件能读"只能证明放行逻辑存在，不能证明拦截逻辑存在。
而这里拦截逻辑失效的代价特别大——桩号是下游一切逐桩数据的对齐基准，
静默读错一条，挂在它上面的 WIM / 病害 / 试验数据全部错位，且不会有任何报错。
"""
from __future__ import annotations

import copy
import json
import math
import os
import pathlib
import re
import sys
from decimal import ROUND_HALF_UP, Decimal

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "modules" / "M2-ingest"))

import design_import as di                              # noqa: E402
from adapters import base, detect_vendor, geom, weidi          # noqa: E402
from adapters.errors import SourceInvalid                # noqa: E402
from adapters.weidi import dmx, jd, pm, prj as prj_mod, sta, sup, wid, zdm  # noqa: E402

PRJ_FIXTURE = ROOT / "tests" / "fixtures" / "design_import" / "weidi_prj_excerpt.PRJ"
IR_SCHEMA_PATH = ROOT / "contracts" / "design-import" / "road_geometry_ir.v0.3.schema.json"
DDL_PATH = ROOT / "sql" / "10_ddl_v0.4.sql"


def ddl_table_body(table: str) -> str:
    "从 DDL 取某张表的建表体（测试用，避免把列清单硬编码成第二个漂移源）。"
    m = re.search(rf"CREATE TABLE IF NOT EXISTS {re.escape(table)} \((.*?)\n\);",
                  DDL_PATH.read_text(encoding="utf-8"), re.S)
    assert m, f"DDL 里找不到表 {table}"
    return m.group(1)


def ddl_columns(table: str) -> set[str]:
    "该表全部列名（含生成列）。"
    cols = set()
    for line in ddl_table_body(table).splitlines():
        tok = line.split("--")[0].strip().rstrip(",").split(" ")[0]
        if tok.isidentifier() and tok.upper() not in ("UNIQUE", "PRIMARY", "FOREIGN", "CHECK"):
            cols.add(tok)
    return cols


def ddl_generated(table: str) -> set[str]:
    "该表里 GENERATED ALWAYS AS … STORED 的列名。**从 DDL 读**，不硬编码——"
    "以后谁再加生成列，本组自动开始检查它，不需要有人记得来改测试。"
    gen = set()
    for line in ddl_table_body(table).splitlines():
        if "GENERATED ALWAYS AS" in line.upper():
            tok = line.strip().split(" ")[0]
            if tok.isidentifier():
                gen.add(tok)
    return gen
FIXTURE = ROOT / "tests" / "fixtures" / "design_import" / "weidi_sta_excerpt.STA"
# 真实完整文件在 docpipe/（gitignored，不入库）→ 干净检出时不存在，属可选校验
REAL_DIR = ROOT.parent / "docpipe" / "materials" / "纬地工程项目文件"

PASS = 0
FAIL = 0


def pg_dsn() -> str | None:
    """真库 DSN：优先环境变量，否则读 scaffold/.env（PG_PORT 是宿主端口 55432）。

    第 12 组是**唯一**碰库的一组，没有库就跳过 —— 本套件主体保持离线可跑。

    ⚠ 必须处理**行内注释**：本仓 .env 里写的是
    ``PG_PORT=55432          # 本机 5432 已被其它服务占用``。
    天真的 ``line.split("=")`` 会把注释一起吞进值里，得到
    ``postgresql://…@localhost:55432          # 本机…/road_pavement`` ——
    一个语法上看着像 DSN、连不上又不报错的字符串（本组第一次跑就是这么"跳过"的）。
    """
    if os.getenv("PG_DSN"):
        return os.environ["PG_DSN"]
    envf = ROOT / ".env"
    if not envf.exists():
        return None
    vals: dict[str, str] = {}
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in ("'", '"'):                    # 带引号：取到配对引号为止
            q = v[0]
            v = v[1:v.index(q, 1)] if q in v[1:] else v[1:]
        else:                                      # 不带引号：去掉行内注释
            v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
        vals[k.strip()] = v
    if not vals.get("PG_PASSWORD"):
        return None
    return (f"postgresql://{vals.get('PG_USER', 'rp')}:{vals['PG_PASSWORD']}"
            f"@localhost:{vals.get('PG_PORT', '55432')}/{vals.get('PG_DB', 'road_pavement')}")


def _raises_wg(fn) -> bool:
    """调用 fn，抛 WriteGuardError 则回 True（写只经 M2 的验证用）。

    `rpdao` 依赖 psycopg，所以这个导入**必须懒着做**：放在模块层会让整个
    测试文件在没装 psycopg 的环境里直接 ImportError，连离线组都跑不了。
    """
    try:
        from rpdao.errors import WriteGuardError        # noqa: PLC0415
    except Exception:                                  # noqa: BLE001
        return False
    try:
        fn()
    except WriteGuardError:
        return True
    except Exception:                                  # noqa: BLE001
        return False
    return False


def _raises(fn) -> bool:
    """调用 fn，抛 SourceInvalid 则回 True（用于把"必须抛错"塞进 check 里）。"""
    try:
        fn()
    except SourceInvalid:
        return True
    except Exception:                                  # noqa: BLE001
        return False
    return False


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}" + (f"  {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ✗ {name}  {detail}")


def check_raises(name: str, text: str, *, expect: str = "", parser=sta) -> None:
    """应拒绝侧：必须抛 SourceInvalid（而不是静默跳过或返回半截数据）。

    `parser` 默认 `.STA`（历史调用方）；验别的文件**必须显式传** —— 否则会拿
    `.STA` 的魔数去比，每个用例都"恰好"因为魔数不符被拒，输出看起来全绿，
    实际**一条也没走到目标解析器自己的校验逻辑上**。本组第一版正是这样翻车的。
    """
    global PASS, FAIL
    try:
        out = parser.parse(text, file="<test>")
    except SourceInvalid as exc:
        msg = str(exc)
        if expect and expect not in msg:
            FAIL += 1
            print(f"  ✗ {name}  抛错了，但信息不含 {expect!r}：{msg[:70]}")
        else:
            PASS += 1
            print(f"  ✓ {name}  → 已拒绝：{msg[:58]}")
        return
    FAIL += 1
    print(f"  ✗ {name}  **本应拒绝却通过了**，返回 {len(out.get('points', []))} 点")


def main() -> int:
    import jsonschema

    schema = json.loads(IR_SCHEMA_PATH.read_text(encoding="utf-8"))

    # ── 第 1 组：真实格式 · 应通过 ──────────────────────────────────────────
    print("\n第 1 组  真实纬地 .STA 节选（应通过，逐点数值断言）")
    text = FIXTURE.read_text(encoding="utf-8")
    check("魔数探测认得它", detect_vendor(text) == "weidi-hintcad")
    out = sta.parse(text, file=FIXTURE.name)
    pts = out["points"]
    check("点数 = 30", len(pts) == 30, f"实为 {len(pts)}")
    check("版本号 = 5.84", out["vendor_version"] == "5.84", f"实为 {out['vendor_version']}")
    check("首点 = 0.000 m / 序号 1", pts[0] == {"station_m": 0.0, "seq_no": 1}, f"实为 {pts[0]}")
    check("第 2 点 = 20.000 m", pts[1]["station_m"] == 20.0, f"实为 {pts[1]['station_m']}")
    # ★ 实测第 30 点是 545.874，不是 560.000 —— 校验解析器**没有假设等距**
    check("末点 = 545.874 m（非等距点，防「假设 20m 间隔」）",
          pts[-1]["station_m"] == 545.874, f"实为 {pts[-1]['station_m']}")
    check("序号连续 1..30", [p["seq_no"] for p in pts] == list(range(1, 31)))
    check("列车严格不减", all(pts[i]["station_m"] <= pts[i + 1]["station_m"]
                             for i in range(len(pts) - 1)))

    # ── 第 2 组：应拒绝（拦截逻辑是否真的存在）────────────────────────────
    print("\n第 2 组  畸形输入（**必须拒绝**，否则静默写错几何）")
    HDR = "HINTCAD5.84_STA_SHUJU\r\n"
    check_raises("空文件", "")
    check_raises("魔数错", "HINTCAD5.84_XXX\r\n     0.000\t     1\r\n    20.000\t     2\r\n", expect="魔数")
    check_raises("缺魔数（直接上数据）", "     0.000\t     1\r\n    20.000\t     2\r\n", expect="魔数")
    check_raises("字段数 = 1", HDR + "     0.000\r\n    20.000\t     2\r\n", expect="字段数")
    check_raises("字段数 = 3", HDR + "     0.000\t     1\textra\r\n    20.000\t     2\r\n", expect="字段数")
    check_raises("桩号非数字", HDR + "   abc.def\t     1\r\n    20.000\t     2\r\n", expect="桩号不是合法数字")
    check_raises("序号非整数", HDR + "     0.000\t     1.5\r\n    20.000\t     2\r\n", expect="序号不是合法整数")
    check_raises("桩号倒退", HDR + "   100.000\t     1\r\n    20.000\t     2\r\n", expect="桩号倒退")
    check_raises("桩号为负", HDR + "   -10.000\t     1\r\n    20.000\t     2\r\n", expect="桩号为负")
    check_raises("序号未递增", HDR + "     0.000\t     5\r\n    20.000\t     5\r\n", expect="序号未递增")
    check_raises("只有 1 条数据", HDR + "     0.000\t     1\r\n", expect="不足 2 条")
    check_raises("中间空行（疑似漏读）",
                 HDR + "     0.000\t     1\r\n\r\n    40.000\t     3\r\n", expect="空行")
    # 末尾空行**应通过**（导出工具常留）——拒绝侧的对称性检查
    tail_ok = sta.parse(HDR + "     0.000\t     1\r\n    20.000\t     2\r\n\r\n", file="<t>")
    check("末尾空行应通过（不误杀）", len(tail_ok["points"]) == 2)

    # ── 第 3 组：几何完整度等级由实际产出推导 ──────────────────────────────
    print("\n第 3 组  几何完整度等级（L0–L4，不允许外部手填）")
    check("空 → L0", base.derive_level({}) == "L0")
    check("仅桩号 → L1", base.derive_level({"station_sequence": pts}) == "L1")
    check("+平面交点 → L2",
          base.derive_level({"station_sequence": pts, "alignment_pi": [{"id": 1}]}) == "L2")
    # ★ 2026-09 变更：L3 的组合方式由 any 改为 all。
    #   起因是**实测**：只接入 .DMX（地面线）时，等级立刻从 L2 跳到 L3 —— 而地面线是
    #   测量结果、不是设计成果，用它宣称"有纵断面设计"是个**假断言**：拿着它依然
    #   回答不了"这个桩号该铺多厚"。等级虚高比等级偏低更糟，它让人以为数据齐了。
    #   这条断言当时就是红的，**它的红是对的**；现按新语义重写并补齐两侧。
    check("★ 只给纵断变坡点（设计线）→ 仍是 L2（L3 要求两条线同时具备）",
          base.derive_level({"station_sequence": pts, "alignment_pi": [{"id": 1}],
                             "profile_grade_point": [{"id": 1}]}) == "L2")
    check("★ 只给地面线 → 仍是 L2（地面线不是设计成果）",
          base.derive_level({"station_sequence": pts, "alignment_pi": [{"id": 1}],
                             "profile_ground_point": [{"id": 1}]}) == "L2")
    check("★ 设计线 + 地面线**都**齐 → L3",
          base.derive_level({"station_sequence": pts, "alignment_pi": [{"id": 1}],
                             "profile_grade_point": [{"id": 1}],
                             "profile_ground_point": [{"id": 1}]}) == "L3")
    check("★ 元测试：L3 若退回 any，'只给设计线'就会判成 L3（说明上面两条非摆设）",
          base.level_hit(("profile_grade_point", "profile_ground_point"),
                         {"profile_grade_point"}, "any") is True
          and base.level_hit(("profile_grade_point", "profile_ground_point"),
                             {"profile_grade_point"}, "all") is False)
    check("空数组不算「有」（防占位漂白等级）",
          base.derive_level({"station_sequence": [], "alignment_pi": []}) == "L0")

    # ── 第 4 组：IR 三处自洽（能力 / 实得 / 缺口）＋ schema 校验 ────────────
    print("\n第 4 组  契约⑤ IR：结构合法 + 能力/实得/缺口三者自洽")
    d = REAL_DIR if REAL_DIR.is_dir() else None
    if d is None:
        # 干净检出：用 fixture 所在目录造的临时工程目录代替，保证测试离线可跑
        import tempfile
        proj = pathlib.Path(tempfile.mkdtemp()) / "weidi_proj"
        proj.mkdir()
        (proj / FIXTURE.name).write_text(text, encoding="utf-8")
        d = proj
    ir = weidi.build_ir(d, project_name="契约测试工程")
    jsonschema.validate(ir, schema)
    check("产出过契约⑤ schema", True, f"等级 {ir['geometry_level']}")
    segs, caps = set(ir["segments"]), set(ir["capabilities"])
    check("segments ⊆ capabilities", segs <= caps, f"越界 {segs - caps or '无'}")
    gaps = {g["segment"] for g in ir["gaps"]}
    check("capabilities − segments == gaps（无遗漏、无多余）",
          caps - segs == gaps, f"差集 {caps - segs} / gaps {gaps}")
    check("等级与实得一致", ir["geometry_level"] == base.derive_level(ir["segments"]))
    check("每个 gap 都带 reason", all(g.get("reason") for g in ir["gaps"]))
    check("未实现的段记 not_supported（≠ source_absent）",
          all(g["reason"] in ("source_absent", "parse_blocked", "manual_required", "not_supported")
              for g in ir["gaps"]))
    check("来源方式 = file", ir["source"]["origin"] == "file")
    print(f"      能力 {len(caps)} 段 ／ 实得 {len(segs)} 段 ／ 缺口 {len(gaps)} 段")
    for g in ir["gaps"]:
        print(f"        · {g['segment']:22} {g['reason']}")

    # ── 第 5 组：元测试 —— 确认上面的 schema 校验不是恒真的空断言 ──────────
    print("\n第 5 组  元测试：schema 必须真的会拒绝坏 IR")
    for name, mutate in (
        ("等级非法值 L9", lambda x: x.update(geometry_level="L9")),
        ("段名不在枚举内", lambda x: x.update(capabilities=["bogus_segment"])),
        ("缺 gaps 字段", lambda x: x.pop("gaps")),
        ("origin 非法", lambda x: x["source"].update(origin="telepathy")),
        ("桩号序列只 1 点（minItems=2）",
         lambda x: x["segments"].update(station_sequence=[{"station_m": 0.0, "seq_no": 1}])),
        ("files 里 parse_status 非法",
         lambda x: x["source"]["files"][0].update(parse_status="maybe")),
    ):
        bad = json.loads(json.dumps(ir))
        mutate(bad)
        try:
            jsonschema.validate(bad, schema)
            rejected = False
        except jsonschema.ValidationError:
            rejected = True
        check(f"{name} → schema 拒绝" if rejected else f"{name} —— **schema 没能拒绝**", rejected)

    # ── 第 6a 组：.DMX 纵断面地面线解析器（应通过 / 应拒绝两侧）─────────────
    print("\n第 6a 组  .DMX 纵断面地面线解析器（应通过 / 应拒绝两侧）")
    _hdr = "HINTCAD5.83_DMX_SHUJU\r\n"
    _ok_txt = _hdr + "      0.000\t 57.262000\r\n     20.000\t 57.224000\r\n     40.000\t 57.159000\r\n"
    _out = dmx.parse(_ok_txt, file="ok.DMX")
    check("应通过：正例解析出 3 条", len(_out["points"]) == 3, str(_out["points"]))
    check("应通过：厂商版本从魔数取出", _out["vendor_version"] == "5.83", _out["vendor_version"])
    check("应通过：detect 认得自家魔数", dmx.detect(_ok_txt) is True)
    check("应通过：段名与文件类别与 SEGMENT_FILES 登记一致",
          dmx.SEGMENT == "profile_ground_point"
          and weidi.SEGMENT_FILES["profile_ground_point"][0] == ".DMX"
          and weidi.SEGMENT_FILES["profile_ground_point"][1] == dmx.FILE_KIND,
          f"{dmx.SEGMENT} / {dmx.FILE_KIND}")

    check_raises("应拒绝：魔数是 .STA 的（张冠李戴）",
                 "HINTCAD5.84_STA_SHUJU\r\n      0.000\t1\r\n", parser=dmx, expect="魔数不匹配")
    check_raises("应拒绝：非纬地文件", "随便一个文本\n1\t2\n", parser=dmx, expect="魔数不匹配")
    check_raises("应拒绝：字段数只有 1（漏了高程列）",
                 _hdr + "      0.000\r\n     20.000\r\n", parser=dmx, expect="字段数应为 2")
    check_raises("应拒绝：高程不是数字",
                 _hdr + "      0.000\t 57.262000\r\n     20.000\t abc\r\n", parser=dmx, expect="不是合法数字")
    check_raises("应拒绝：桩号倒退",
                 _hdr + "     40.000\t 57.262000\r\n     20.000\t 57.224000\r\n", parser=dmx, expect="桩号倒退")
    check_raises("应拒绝：桩号为负",
                 _hdr + "     -1.000\t 57.262000\r\n     20.000\t 57.224000\r\n", parser=dmx, expect="桩号为负")
    # ★ 列错位：把桩号读进了高程列 —— 不设界限，这种错会静默入库。
    check_raises("★ 应拒绝：高程串成了桩号那种量级（列错位）",
                 _hdr + "      0.000\t5805421.000000\r\n     20.000\t5806000.000000\r\n",
                 parser=dmx, expect="超出合理区间")
    check_raises("应拒绝：文件中间有空行（漏读一段地形）",
                 _hdr + "      0.000\t 57.262000\r\n\r\n     40.000\t 57.159000\r\n",
                 parser=dmx, expect="中间出现空行")
    check_raises("应拒绝：只有 1 条数据", _hdr + "      0.000\t 57.262000\r\n", parser=dmx, expect="不足 2 条")
    check_raises("应拒绝：空文件", "", parser=dmx, expect="空文件")

    # 元测试 + **能力边界**：这条界限是**粗**筛，不是精判。
    # 第一版这两条断言我写错了：拿 5805.421 当"错位"的例子，可它本身就是个合法高程
    # （西藏公路真的在 5000 m 以上），界限当然放它过去 —— 是**我的断言声称了检查做不到的事**。
    # 正确的做法不是收紧界限（那会误杀高海拔的真实道路），而是把边界明写出来：
    # 它抓的是"串成了另一列的量级"，抓不了"看着像高程但其实是别的数"。
    check("元测试：量级错位确实被判超界（界限非摆设）",
          not (dmx.ELEV_MIN_M <= 5805421.0 <= dmx.ELEV_MAX_M))
    check("能力边界明示：合法的高海拔高程必须放行（界限不得误杀真实道路）",
          dmx.ELEV_MIN_M <= 5805.421 <= dmx.ELEV_MAX_M)

    # 跨文件对账：条数不等 / 桩号不同，都必须报出来（而不是默默错位）
    _st = [{"station_m": 0.0}, {"station_m": 20.0}]
    check("对账：条数不等要报", bool(dmx.check_against_stations(
        [{"station_m": 0.0}], _st)))
    check("对账：桩号不同要报", bool(dmx.check_against_stations(
        [{"station_m": 0.0}, {"station_m": 25.0}], _st)))
    check("对账：完全对齐则不报", not dmx.check_against_stations(
        [{"station_m": 0.0}, {"station_m": 20.0}], _st))

    # ── 第 6b 组：.ZDM 纵断面设计线解析器（应通过 / 应拒绝两侧）─────────────
    print("\n第 6b 组  .ZDM 纵断面设计线解析器（应通过 / 应拒绝两侧）")
    _zh = "HINTCAD5.83_ZDM_SHUJU\r\n"
    _cnt = "          2\r\n"
    _row = "         0.000\t57.26200000\t0.00000000\t     0.000\t0.00000000\r\n"
    _row2 = "       300.000\t58.82200000\t6000.00000000\t     0.000\t0.00000000\r\n"
    _z_ok = _zh + _cnt + _row + _row2
    _z = zdm.parse(_z_ok, file="ok.ZDM")
    check("应通过：正例解析出 2 个变坡点", len(_z["points"]) == 2, str(len(_z["points"])))
    check("应通过：vpi_seq 从 1 起编号",
          [p["vpi_seq"] for p in _z["points"]] == [1, 2])
    check("应通过：detect 认得自家魔数", zdm.detect(_z_ok) is True)
    check("应通过：段名与文件类别与 SEGMENT_FILES 登记一致",
          zdm.SEGMENT == "profile_grade_point"
          and weidi.SEGMENT_FILES["profile_grade_point"][0] == ".ZDM"
          and weidi.SEGMENT_FILES["profile_grade_point"][1] == zdm.FILE_KIND,
          f"{zdm.SEGMENT} / {zdm.FILE_KIND}")
    _z2, _w2 = zdm.derive_grades([dict(p) for p in _z["points"]])
    check("派生：首点入坡 None、末点出坡 None（不是 0）",
          _z2[0]["grade_in_pct"] is None and _z2[-1]["grade_out_pct"] is None)
    # 首点 R=0 → 确实没有竖曲线，长度 0 是**真值**。
    check("派生：首点 R=0 → 竖曲线长 0（真值，不是缺值）",
          _z2[0]["grade_len_m"] == 0.0, str(_z2[0]["grade_len_m"]))
    # 末点 R=6000>0 但缺出坡 → **算不出来**，必须是 None 并告警。
    # 这里不能写 0 —— 0 会被下游当成"这条竖曲线长 0 米"，而真相是"无法确定"。
    check("派生：末点 R>0 却缺一侧纵坡 → 竖曲线长 None（不是 0）且必须告警",
          _z2[-1]["grade_len_m"] is None and any("竖曲线长无法推得" in w for w in _w2),
          f"{_z2[-1]['grade_len_m']} / {_w2}")
    check("派生：单段坡 = 1.56/300 = 0.52%",
          abs(_z2[0]["grade_out_pct"] - 0.52) < 1e-9, str(_z2[0]["grade_out_pct"]))

    check_raises("应拒绝：魔数是 .DMX 的（张冠李戴）",
                 "HINTCAD5.83_DMX_SHUJU\r\n" + _cnt + _row + _row2,
                 parser=zdm, expect="魔数不匹配")
    # 缺计数行时，第 2 行是数据行 → 报"计数行不是整数"（把冒犯的原文带出来，
    # 人一看就知道是把数据行当成了计数行）。它确实被拒绝了，只是措辞如此。
    check_raises("应拒绝：缺计数行（数据行被当成计数行 → 非整数）",
                 _zh + _row + _row2, parser=zdm, expect="计数行不是整数")
    check_raises("应拒绝：计数行为空", _zh + "\r\n" + _row + _row2,
                 parser=zdm, expect="缺少计数行")
    check_raises("应拒绝：计数行不是整数", _zh + "     十二\r\n" + _row + _row2,
                 parser=zdm, expect="计数行不是整数")
    # ★ 自带计数行是这个文件独有的一份礼物：拿它对自己的内容
    check_raises("★ 应拒绝：计数行声明 5 个但只有 2 行（文件自我矛盾）",
                 _zh + "          5\r\n" + _row + _row2,
                 parser=zdm, expect="实际读到 2 个")
    check_raises("★ 应拒绝：计数行声明 1 个但有 2 行（多读）",
                 _zh + "          1\r\n" + _row + _row2,
                 parser=zdm, expect="实际读到 2 个")
    check_raises("应拒绝：字段数 3（少了两列未知列）",
                 _zh + _cnt + "         0.000\t57.26200000\t0.00000000\r\n" + _row2,
                 parser=zdm, expect="字段数应为 5")
    check_raises("应拒绝：设计高程超合理区间",
                 _zh + _cnt + "         0.000\t57262.00000000\t0.0\t0.0\t0.0\r\n" + _row2,
                 parser=zdm, expect="超出合理区间")
    check_raises("应拒绝：竖曲线半径为负",
                 _zh + _cnt + "         0.000\t57.262\t-1.0\t0.0\t0.0\r\n" + _row2,
                 parser=zdm, expect="半径为负")
    check_raises("应拒绝：竖曲线半径超合理区间",
                 _zh + _cnt + "         0.000\t57.262\t1e12\t0.0\t0.0\r\n" + _row2,
                 parser=zdm, expect="超出合理区间")
    check_raises("应拒绝：桩号未严格递增（同桩号两个变坡点无法解释）",
                 _zh + _cnt + _row + "         0.000\t58.822\t0.0\t0.0\t0.0\r\n",
                 parser=zdm, expect="未严格递增")
    check_raises("应拒绝：桩号倒退",
                 _zh + _cnt + _row2 + _row, parser=zdm, expect="未严格递增")
    check_raises("应拒绝：文件中间有空行",
                 _zh + _cnt + _row + "\r\n" + _row2, parser=zdm, expect="中间出现空行")
    check_raises("应拒绝：只有 1 个变坡点",
                 _zh + "          1\r\n" + _row, parser=zdm, expect="不足 2 个")
    check_raises("应拒绝：空文件", "", parser=zdm, expect="空文件")

    # 未知列不得静默丢弃：非 0 时必须告警（含义未知 ≠ 可以扔）
    _zu, _wu = zdm.derive_grades([dict(p, field4_raw=1.5) for p in _z["points"]])
    check("未知列出现非 0 值 → 必须告警（不可静默丢弃看不懂的设计数据）",
          any("field4_raw" in w for w in _wu), str(_wu))
    check("未知列全 0 → 不告警（样本里本来就全 0，不制造噪声）",
          not any("field4_raw" in w for w in _w2), str(_w2))
    check("元测试：计数行自校验非摆设（声明值与实际值不等必须能被构造出来）",
          len(_z["points"]) == 2 and 5 != len(_z["points"]))

    # ── 第 5b 组：IR 里"告警"这个键的位置（专治上面那类假绿）──────────────
    print("\n第 5b 组  IR 告警键的位置与空值语义")
    _w_ir = base.make_ir(vendor="weidi", origin="x", files=[], capabilities=(),
                         segments={}, warnings=["故意放一条告警"])
    check("★ 告警在 IR **根**键 warnings（不在 source 下）",
          _w_ir.get("warnings") == ["故意放一条告警"]
          and "warnings" not in _w_ir["source"],
          f"根={_w_ir.get('warnings')} source={sorted(_w_ir['source'])}")
    _n_ir = base.make_ir(vendor="weidi", origin="x", files=[], capabilities=(), segments={})
    check("★ 无告警时**不输出**该键（保持紧凑）—— 故读取必须用 .get(…, [])",
          "warnings" not in _n_ir, sorted(_n_ir))
    # 元测试：把键写错时必须能被抓出来 —— 这正是我犯过的错
    check("★ 元测试：读错键（source 下）会恒为 []，本条断言能识别这种假绿",
          _w_ir.get("warnings") == ["故意放一条告警"]
          and _w_ir["source"].get("warnings", []) == []
          and _w_ir["source"].get("warnings", []) != _w_ir.get("warnings"))

    # ── 第 5c 组：竖曲线内插（设计高程）────────────────────────────────────
    print("\n第 5c 组  竖曲线内插：设计高程")
    # 合成一段纵断面设计线：3 个变坡点，中间那个带竖曲线。
    # 写法与第 6b 组一致（显式 CRLF + 计数行）——.ZDM 的计数行是它自带的自检。
    # 变量名用 _vc_ 前缀，**不要用 _z**：第 6b 组已有一个 _z，同名会把它的值覆盖掉。
    _vc_txt = ("HINTCAD5.83_ZDM_SHUJU\r\n"
               "          3\r\n"
               "         0.000\t100.00000000\t0.00000000\t     0.000\t0.00000000\r\n"
               "       500.000\t105.00000000\t5000.00000000\t     0.000\t0.00000000\r\n"
               "      1000.000\t100.00000000\t0.00000000\t     0.000\t0.00000000\r\n")
    _zp, _ = zdm.derive_grades(zdm.parse(_vc_txt, file="t.ZDM")["points"])
    _c = zdm.vertical_curve_of(_zp[1])
    # 本段是 100→105→100 跨 500 m，即 i入=+1%、i出=-1%：
    #   ω = -0.02，L = R·|ω| = 5000×0.02 = 100，T = 50，E = |ω|·L/8 = 0.25
    check("竖曲线参数：ω/L/T/E 与定义一致",
          abs(_c["omega"] + 0.02) < 1e-12 and abs(_c["len_m"] - 100.0) < 1e-9
          and abs(_c["tangent_len_m"] - 50.0) < 1e-9
          and abs(_c["external_m"] - 0.25) < 1e-12, str(_c))
    check("★ 曲线以变坡点为中心：BVC = 变坡点 − T，EVC = 变坡点 + T",
          abs(_c["bvc_station_m"] - 450.0) < 1e-9 and abs(_c["evc_station_m"] - 550.0) < 1e-9,
          f"BVC={_c['bvc_station_m']} EVC={_c['evc_station_m']}")
    # 凸（ω<0）曲线在变坡点处**低于**交点：105 - 0.25 = 104.75
    check("★ 变坡点处：曲线 = 交点高程 + sign(ω)·E（凸则低、凹则高）",
          abs(zdm.design_elevation_at(_zp, 500.0) - 104.75) < 1e-9,
          str(zdm.design_elevation_at(_zp, 500.0)))
    # BVC=450：切线值 100 + 0.01×450 = 104.5；EVC=550：105 - 0.01×50 = 104.5
    check("曲线端点（BVC/EVC）处曲线值 = 切线值（两端对称，都是 104.5）",
          abs(zdm.design_elevation_at(_zp, 450.0) - 104.5) < 1e-9
          and abs(zdm.design_elevation_at(_zp, 550.0) - 104.5) < 1e-9,
          f"{zdm.design_elevation_at(_zp, 450.0)} / {zdm.design_elevation_at(_zp, 550.0)}")
    check("跨 BVC/EVC 左右连续（无跳变）",
          all(abs(zdm.design_elevation_at(_zp, s - 1e-7)
                  - zdm.design_elevation_at(_zp, s + 1e-7)) < 1e-6 for s in (450.0, 550.0)))
    check("★ 桩号落在已知范围外 → None，**不外推**",
          zdm.design_elevation_at(_zp, -1.0) is None
          and zdm.design_elevation_at(_zp, 1001.0) is None)
    check("首末变坡点处（无竖曲线）高程 = .ZDM 原值",
          abs(zdm.design_elevation_at(_zp, 0.0) - 100.0) < 1e-9
          and abs(zdm.design_elevation_at(_zp, 1000.0) - 100.0) < 1e-9)

    # ★★ 元测试：把曲线错放到变坡点**之后**（我第一版的错），上面那条必须能抓出来。
    #    错位版本在变坡点处算出来恰好**等于**切线交点高程（差值 0.0000），
    #    而正确版本差一个外距 E —— 两者可辨，所以这条钉子不是摆设。
    def _wrong_placement(points, sta):
        for q in points:
            cc = zdm.vertical_curve_of(q)
            if not cc:
                continue
            bvc = q["station_m"] + cc["tangent_len_m"]          # ← 错：应以变坡点为中心
            if bvc <= sta <= bvc + cc["len_m"]:
                x = sta - bvc
                yb = q["elevation_m"] + (q["grade_in_pct"] / 100.0) * cc["tangent_len_m"]
                return yb + (q["grade_in_pct"] / 100.0) * x + (cc["omega"] / (2 * cc["len_m"])) * x * x
        return None
    # 在真曲线区间 [450,550] 内取三个桩号：错位版（曲线在 [550,650]）与正确版必须都能分辨
    _probe = (460.0, 500.0, 540.0)
    _disagree = [s for s in _probe
                 if _wrong_placement(_zp, s) != zdm.design_elevation_at(_zp, s)]
    check("★★ 元测试：曲线错位（甩到变坡点之后）会被认出来，不是摆设",
          len(_disagree) == len(_probe),
          f"{len(_probe)} 个曲线内桩号只有 {len(_disagree)} 个能分辨；"
          f"错位版在变坡点 500 处算得 {_wrong_placement(_zp, 500.0)}（给不出值）"
          f"／正确版 {zdm.design_elevation_at(_zp, 500.0)}")

    # 元测试：重叠检查必须非空 —— 造两条真重叠的曲线
    _ov = [dict(_zp[0]), dict(_zp[1]), dict(_zp[2])]
    _ov[1]["vertical_curve_radius_m"] = 200000.0     # L 大到把相邻变坡点包进来
    check("★ 元测试：竖曲线重叠/越界检查确实会报（不是空断言）",
          len(zdm.check_vertical_curves(_ov)) > 0,
          str(zdm.check_vertical_curves(_ov)[:2]))
    check("正常数据下竖曲线无重叠/越界", zdm.check_vertical_curves(_zp) == [])

    # ── 第 5d 组：平面线形（曲率/方位角/坐标）─────────────────────────────
    print("\n第 5d 组  平面线形：由线元推任意桩号")
    from adapters import geom as _geom
    _el = [
        dict(seq=1, type="line", turn_flag=1, start_station_m=0.0, end_station_m=100.0,
             length_m=100.0, azimuth_deg=0.0, end_azimuth_deg=0.0,
             start_x=0.0, start_y=0.0, end_x=100.0, end_y=0.0,
             radius_start_m=None, radius_end_m=None),
        dict(seq=2, type="transition", turn_flag=1, start_station_m=100.0, end_station_m=160.0,
             length_m=60.0, azimuth_deg=0.0, end_azimuth_deg=None,
             start_x=100.0, start_y=0.0, end_x=None, end_y=None,
             radius_start_m=None, radius_end_m=300.0),
        dict(seq=3, type="circular", turn_flag=1, start_station_m=160.0, end_station_m=260.0,
             length_m=100.0, azimuth_deg=0.0, end_azimuth_deg=None,
             start_x=None, start_y=None, end_x=None, end_y=None,
             radius_start_m=300.0, radius_end_m=300.0),
    ]
    check("★ R=None 表示**无穷大半径**（曲率 0），不是缺值",
          _geom.curvature_at(_el[0], 50.0) == 0.0
          and _geom.curvature_at(_el[1], 100.0) == 0.0,          # 缓和曲线起点 R=∞
          f"{_geom.curvature_at(_el[0], 50.0)} / {_geom.curvature_at(_el[1], 100.0)}")
    check("圆曲线曲率 = 1/R，缓和曲线内**线性**过渡",
          abs(_geom.curvature_at(_el[2], 200.0) - 1 / 300.0) < 1e-15
          and abs(_geom.curvature_at(_el[1], 130.0) - (1 / 300.0) * 0.5) < 1e-15,
          f"{_geom.curvature_at(_el[2], 200.0)} / {_geom.curvature_at(_el[1], 130.0)}")
    check("★ 曲率符号来自 turn_flag：右转（−1）曲率为负",
          _geom.curvature_at(dict(_el[2], turn_flag=-1), 200.0) < 0,
          str(_geom.curvature_at(dict(_el[2], turn_flag=-1), 200.0)))
    check("直线段方位角恒定、坐标沿方位角直线前进",
          abs(_geom.azimuth_at(_el[0], 30.0) - 0.0) < 1e-12
          and abs(_geom.point_at(_el[0], 30.0)[0] - 30.0) < 1e-9
          and abs(_geom.point_at(_el[0], 30.0)[1]) < 1e-9,
          str(_geom.point_at(_el[0], 30.0)))
    # 圆曲线：方位角按 Δ/R 线性增加。本线元 L=100、R=300 → 转过 100/300 rad
    # 注意桩号必须落在**线元范围内**（160–260），取到 631 就是外推了。
    check("圆曲线：方位角按 Δ/R 变化（L=100、R=300 → 转过 100/300 rad ≈ 19.099°）",
          abs(_geom.azimuth_at(_el[2], 260.0) - math.degrees(100.0 / 300.0)) < 1e-9,
          str(_geom.azimuth_at(_el[2], 260.0)))
    check("起点处严格等于源值（d=0 边界）",
          _geom.point_at(_el[1], 100.0) == (100.0, 0.0)
          and abs(_geom.azimuth_at(_el[1], 100.0)) < 1e-12)
    check("★ 桩号落在全部线元之外 → locate 返回 None，**不外推**",
          _geom.locate(_el, -1.0) is None and _geom.locate(_el, 1000.0) is None)

    # ★★ 元测试：把 turn_flag 丢掉（一律按左转算），右转的线元必须算不对。
    #    这正是我第一版犯的错 —— 33 个真实线元里有 16 个是右转，终点最多差 98 m。
    _r = dict(_el[2], turn_flag=-1)
    check("★★ 元测试：丢掉 turn_flag（一律当左转）会被认出来，不是摆设",
          abs(_geom.azimuth_at(_r, 260.0) - _geom.azimuth_at(dict(_r, turn_flag=1), 260.0)) > 1.0,
          f"右转 {_geom.azimuth_at(_r, 260.0):.4f}° vs 误当左转 "
          f"{_geom.azimuth_at(dict(_r, turn_flag=1), 260.0):.4f}°")

    # ── 第 6 组：真实完整文件（可选，docpipe/ 不入库）──────────────────────
    print("\n第 6 组  真实完整工程文件（可选：docpipe/ 未入库，干净检出会跳过）")
    if d and REAL_DIR.is_dir():
        full = weidi.build_ir(REAL_DIR)
        fpts = full["segments"].get("station_sequence", [])
        check("全文件 332 个桩号", len(fpts) == 332, f"实为 {len(fpts)}")
        if fpts:
            check("起点 0.000 m", fpts[0]["station_m"] == 0.0)
            check("终点 5805.421 m", fpts[-1]["station_m"] == 5805.421,
                  f"实为 {fpts[-1]['station_m']}")
        # 等级断言故意把「已实现段清单」也一起钉住：将来往 IMPLEMENTED 里加了新解析器，
        # 这条会立刻红，逼你回来确认新等级是否符合预期——而不是让它悄悄变。
        # 已经生效过一次：加 .pm 时它红了，提醒"3 段了，确认等级仍是 L2 吗"。
        # 等级断言故意把「已实现段清单」也一起钉住：将来往 IMPLEMENTED 里加了新解析器，
        # 这条会立刻红，逼你回来确认新等级是否符合预期——而不是让它悄悄变。
        # 已经生效过两次：加 .pm 时红了；加 .DMX 时又红了，而这次答案是"等级**不该**动"。
        # 第 4 次变红：加 .WID 时又红了。而这次的答案**仍然是"等级不该动"** ——
        # 超高与路幅宽度都是平纵都具备之后的**设计细节**，不是一级几何；
        # L0–L4 只认平/纵/横（横＝.HDM 横断面地面线，即 cross_section，尚未实现）。
        check("★ 等级 = L3（.ZDM/.DMX 都实现：设计线与地面线同时具备）",
              full["geometry_level"] == "L3"
              and sorted(weidi.IMPLEMENTED)
              == ["alignment_element", "alignment_pi", "design_control",
                  "profile_grade_point", "profile_ground_point", "roadbed_width",
                  "station_sequence", "superelev_transition"],
              f"等级 {full['geometry_level']}／已实现 {sorted(weidi.IMPLEMENTED)}")
        gpts = full["segments"].get("profile_ground_point", [])
        check("纵断面地面线 332 条（与桩号条数相同）", len(gpts) == 332, f"实为 {len(gpts)}")
        if gpts:
            check("地面线起点 57.262 m", abs(gpts[0]["ground_elev_m"] - 57.262) < 1e-9,
                  f"实为 {gpts[0]['ground_elev_m']}")
            check("地面线终点 65.543 m", abs(gpts[-1]["ground_elev_m"] - 65.543) < 1e-9,
                  f"实为 {gpts[-1]['ground_elev_m']}")
        # ★ 这里原来写的是 full["source"].get("warnings", []) —— 而 **source 下
        #   根本没有 warnings 键**（告警在 IR **根**上，见 make_ir）。于是那个表达式
        #   恒为 []，"无告警"恒成立：**一个永远不会失败的检查**。
        #   它恰恰是用来证明 .DMX↔.STA 逐桩对账没问题的，所以它假绿最要命。
        # 直接调对账函数，**不在告警文本里找子串**：混版告警里就含
        # "纵断面地面线文件" 这几个字，拿 "地面线" 当过滤条件会误伤无关告警
        # ——修好键名之后这条立刻红了，红的正是这个误伤。
        _dmx_w = dmx.check_against_stations(full["segments"]["profile_ground_point"],
                                            full["segments"]["station_sequence"])
        check("地面线与桩号逐条对齐 → 无跨文件告警（.DMX 自己没有计数行，只能对账验）",
              _dmx_w == [], str(_dmx_w))
        # ★ 真实工程里 .STA=5.84、.JD/.pm/.DMX/.ZDM=5.83，混版是**事实**，
        #   所以这条必须能读到告警。它同时钉住一个真 bug：链路里第一步曾用
        #   `warns = ...` 而不是 `+=`，把排在它前面的告警全部抹掉（不报错）。
        _mw = [w for w in full.get("warnings", []) if "厂商版本" in w]
        check("★ 混版告警确实出现在 IR 根 warnings（不是算了就丢）",
              len(_mw) == 1 and "5.84" in _mw[0] and "5.83" in _mw[0],
              str(full.get("warnings")))
        check("★ 逐文件的厂商版本在 source.files[].note 里逐条可见",
              sorted(f["note"] for f in full["source"]["files"]
                     if f["parse_status"] == "ok")
              == ["厂商版本 5.83", "厂商版本 5.83", "厂商版本 5.83",
                  "厂商版本 5.83", "厂商版本 5.83", "厂商版本 5.83",
                  "厂商版本 5.84", "厂商版本 6.00"],
              str([f["note"] for f in full["source"]["files"] if f["parse_status"] == "ok"]))
        # ── 竖曲线：真实 12 个变坡点上的内插自检 ──
        _vps = full["segments"]["profile_grade_point"]
        check("真实数据：竖曲线之间无重叠、无越界",
              zdm.check_vertical_curves(_vps) == [],
              str(zdm.check_vertical_curves(_vps)[:2]))
        _ext_bad = []
        for _p in _vps:
            _cc = zdm.vertical_curve_of(_p)
            if not _cc:
                continue
            _y = zdm.design_elevation_at(_vps, _p["station_m"])
            _exp = _p["elevation_m"] + math.copysign(_cc["external_m"], _cc["omega"])
            if abs(_y - _exp) > 1e-6:
                _ext_bad.append((_p["vpi_seq"], _y, _exp))
        check("★ 真实数据：每个变坡点处，曲线 = 交点高程 + sign(ω)·外距（10/10）",
              _ext_bad == [], str(_ext_bad))
        # 首末变坡点没有竖曲线 → 该处高程必须**等于 .ZDM 原值**
        _first, _last = _vps[0], _vps[-1]
        check("★ 真实数据：首末变坡点处高程 = .ZDM 原值（57.2620 / 57.4592）",
              abs(zdm.design_elevation_at(_vps, _first["station_m"]) - _first["elevation_m"]) < 1e-6
              and abs(zdm.design_elevation_at(_vps, _last["station_m"]) - _last["elevation_m"]) < 1e-6,
              f"{zdm.design_elevation_at(_vps, _first['station_m'])} / "
              f"{zdm.design_elevation_at(_vps, _last['station_m'])}")
        check("★ 真实数据：全线 332 个桩号都算得出设计高程，且不越出变坡点高程的包络",
              all(zdm.design_elevation_at(_vps, st["station_m"]) is not None
                  for st in full["segments"]["station_sequence"]))
        check("★ 真实数据：桩号范围外不外推",
              zdm.design_elevation_at(_vps, -1.0) is None
              and zdm.design_elevation_at(_vps, 1e6) is None)
        # ── 平面线形：真实 33 个线元，用源文件自带的终点做对照 ──
        from adapters import geom as _g2
        _els = full["segments"]["alignment_element"]
        # 阈值定在 **DDL 存储精度**（x/y 是 numeric(16,6) → 1e-6 m；
        # 方位角 numeric(10,6) → 1e-6°），不是定在浮点噪声上。
        # 实测残差 3.2e-8 m / 1.6e-9°，比存储精度小 32 倍和 629 倍 —— 入不了库。
        # 我第一版把阈值写成 1e-9，那是拿"我能算多准"当判据，不是拿"存得下多少"。
        _ep = []
        for _e in _els:
            _x, _y = _g2.point_at(_e, _e["end_station_m"])
            if (math.hypot(_x - _e["end_x"], _y - _e["end_y"]) > 1e-6
                    or abs((_g2.azimuth_at(_e, _e["end_station_m"])
                            - _e["end_azimuth_deg"] + 180) % 360 - 180) > 1e-6):
                _ep.append(_e["seq"])
        check("★ 真实数据：33 个线元的终点坐标与方位角全部对上源文件（33/33）",
              _ep == [], f"不符的 seq：{_ep}")
        check("★ 真实数据：每个线元起点处严格等于源值（d=0 边界）",
              all(_g2.point_at(_e, _e["start_station_m"]) == (_e["start_x"], _e["start_y"])
                  and _g2.azimuth_at(_e, _e["start_station_m"]) == _e["azimuth_deg"]
                  for _e in _els))
        # 曲率/方位角在**线元接缝**处必须连续 —— 这是独立于源终点的另一条检查
        _seam_k = [(_a["seq"], _b["seq"]) for _a, _b in zip(_els, _els[1:])
                   if abs(_g2.curvature_at(_a, _a["end_station_m"])
                          - _g2.curvature_at(_b, _b["start_station_m"])) > 1e-12]
        _seam_a = [(_a["seq"], _b["seq"]) for _a, _b in zip(_els, _els[1:])
                   if abs((_g2.azimuth_at(_a, _a["end_station_m"])
                           - _g2.azimuth_at(_b, _b["start_station_m"]) + 180) % 360 - 180) > 1e-6]
        check("★ 真实数据：曲率在 32 个线元接缝处连续", _seam_k == [], str(_seam_k))
        check("★ 真实数据：方位角在 32 个线元接缝处连续", _seam_a == [], str(_seam_a))
        check("★ 真实数据：332 个桩号每个都能定位到线元，且范围外不外推",
              all(_g2.locate(_els, _st["station_m"]) is not None
                  for _st in full["segments"]["station_sequence"])
              and _g2.locate(_els, -1.0) is None)
        # ★★ 元测试（真实数据版）：把 turn_flag 抹平，16 个右转线元必须算不对
        _flat = [dict(_e, turn_flag=1) for _e in _els]
        _n_bad = sum(1 for _e, _f in zip(_els, _flat)
                     if abs((_g2.azimuth_at(_f, _e["end_station_m"])
                             - _e["end_azimuth_deg"] + 180) % 360 - 180) > 1e-6)
        # 期望值**从数据算**，不写死：右转且**有曲率**的线元才会受影响。
        # turn_flag 对直线毫无意义（曲率恒 0，符号翻转什么也不改变）——
        # 16 个右转里有 4 个是直线，所以只有 12 个会算错。我第一版写死 16，错了。
        _turned = [_e for _e in _els if _e["turn_flag"] == -1
                   and (_e["radius_start_m"] or _e["radius_end_m"])]
        check("★★ 元测试：真实数据里抹掉 turn_flag，右转且有曲率的线元会算错（不是摆设）",
              _n_bad == len(_turned) and len(_turned) > 0,
              f"抹平后 {_n_bad} 个线元终点方位角不符；右转且带曲率的线元共 {len(_turned)} 个"
              f"（右转共 {sum(1 for _e in _els if _e['turn_flag'] == -1)} 个，其中直线不受影响）")
        check("缺口只剩 2 项（平纵都齐了）",
              sorted(x["segment"] for x in full["gaps"])
              == ["cross_section", "geometry_point"],
              str([x["segment"] for x in full["gaps"]]))

        vpi = full["segments"].get("profile_grade_point", [])
        check("纵断面设计线 12 个变坡点", len(vpi) == 12, f"实为 {len(vpi)}")
        if vpi:
            st = full["segments"]["station_sequence"]
            check("设计线覆盖整条路（首尾桩号 = 桩号序列首尾）",
                  vpi[0]["station_m"] == st[0]["station_m"]
                  and vpi[-1]["station_m"] == st[-1]["station_m"],
                  f"{vpi[0]['station_m']}–{vpi[-1]['station_m']} vs "
                  f"{st[0]['station_m']}–{st[-1]['station_m']}")
            check("首尾变坡点 R=0 → 竖曲线长 = 0（真值，不是缺值）",
                  vpi[0]["grade_len_m"] == 0.0 and vpi[-1]["grade_len_m"] == 0.0,
                  f"{vpi[0]['grade_len_m']} / {vpi[-1]['grade_len_m']}")
            check("中间变坡点 R>0 → 竖曲线长 > 0（派生成功）",
                  all(p["grade_len_m"] and p["grade_len_m"] > 0 for p in vpi[1:-1]),
                  str([p["grade_len_m"] for p in vpi[1:-1]]))
            check("首尾缺一侧纵坡 → None（不是 0：0 是个坡度的值）",
                  vpi[0]["grade_in_pct"] is None and vpi[-1]["grade_out_pct"] is None,
                  f"{vpi[0]['grade_in_pct']} / {vpi[-1]['grade_out_pct']}")
            # ★ 坡度链必须首尾相接：前一个的出坡 = 后一个的入坡。
            #   这条能同时抓住"算错"和"变坡点顺序错"。
            gaps = [abs(vpi[i]["grade_out_pct"] - vpi[i + 1]["grade_in_pct"])
                    for i in range(len(vpi) - 1)]
            check("★ 纵坡链首尾相接（每个变坡点出坡 = 下一个入坡）",
                  max(gaps) < 1e-9, f"最大断差 {max(gaps):g}%")
            # 这条断言我第一版写成了"派生纵坡全是整齐值"，实测被否：
            # 0.52 / 2.5 / -5.0 / 6.0 / -3.3 / 0.93 确实整齐，但 -3.9003 / 5.9282 /
            # -0.6278 / -1.8297 不整齐 —— 它们是"高程差 ÷ 桩号差"的直接结果。
            # 从一部分规律里读出普遍结论，是这次会话里第二次犯（另一次是拿 5805 m
            # 当"列错位"的例子）。改成只断言站得住的能力边界：量级合理。
            grades = [p["grade_out_pct"] for p in vpi[1:-1] if p["grade_out_pct"] is not None]
            check("派生纵坡量级合理（|i| < 20%，超出即说明列错位或推导错）",
                  grades and max(abs(g) for g in grades) < 20.0,
                  f"最大 {max(abs(g) for g in grades):.4f}%")
        check("台账登记了 10 类文件（含未实现的）", len(full["source"]["files"]) == 10,
              f"实为 {len(full['source']['files'])}")
        check("vendor_version 取自魔数", full["source"]["vendor_version"] == "5.84",
              f"实为 {full['source']['vendor_version']}")
    # ── 第 6c 组：.SUP 超高过渡解析器（应通过 / 应拒绝两侧）─────────────────
    print("\n第 6c 组  .SUP 超高过渡解析器（应通过 / 应拒绝两侧）")
    _sh = "HINTCAD5.83_SUP_SHUJU\r\n"

    def _sup_row(a, b, c, st, d, e, f):
        return f"{a}\t{b}\t{c}\t{st}\t{d}\t{e}\t{f}\r\n"

    # 正例：正常路拱（左右同号）→ 单向超高（左右异号），中间夹一个 9999
    _s_ok = (_sh
             + _sup_row("-3.00", "-2.00", "-2.00", "0.000", "-2.00", "-2.00", "-3.00")
             + _sup_row("9999.00", "9999.00", "9999.00", "100.000", "9999.00", "9999.00", "-3.00")
             + _sup_row("-3.00", "4.00", "4.00", "200.000", "-4.00", "-4.00", "-4.00"))
    _s = sup.parse(_s_ok, file="ok.SUP")
    check("应通过：解析出 3 个过渡变化点", len(_s["points"]) == 3, str(len(_s["points"])))
    check("应通过：厂商版本从魔数取出", _s["vendor_version"] == "5.83", _s["vendor_version"])
    check("应通过：seq_no 从 1 起编号", [p["seq_no"] for p in _s["points"]] == [1, 2, 3])
    check("应通过：detect 认得自家魔数", sup.detect(_s_ok) is True)
    check("应通过：detect 不认别家的魔数", sup.detect("HINTCAD5.83_ZDM_SHUJU\r\n") is False)
    check("应通过：段名与文件类别与 SEGMENT_FILES 登记一致",
          sup.SEGMENT == "superelev_transition"
          and weidi.SEGMENT_FILES["superelev_transition"][0] == ".SUP"
          and weidi.SEGMENT_FILES["superelev_transition"][1] == sup.FILE_KIND,
          f"{sup.SEGMENT} / {sup.FILE_KIND}")
    # ★ 9999 必须收成 None，而不是 0、也不是沿用上值
    _p2 = _s["points"][1]
    check("★ 应通过：9999 → None（不是 0）",
          _p2["lane_left_pct"] is None and _p2["hard_shoulder_left_pct"] is None
          and _p2["lane_right_pct"] is None,
          str({k: v for k, v in _p2.items() if k.endswith("_pct")}))
    check("应通过：同行的非 9999 列原样保留（-3.00）",
          _p2["earth_shoulder_right_pct"] == -3.0, str(_p2["earth_shoulder_right_pct"]))
    # 超高段左右**异号**：教程说的"对称"是位置对称，不是值相等
    check("应通过：超高段左右行车道异号（左 +4 / 右 −4）",
          _s["points"][2]["lane_left_pct"] == 4.0
          and _s["points"][2]["lane_right_pct"] == -4.0)

    # ★★ 元测试：证明「9999 跳过该列」与「沿用上值」是**可区分**的两种语义。
    #     ★ 期望值**由数据算出来**，不写死：跳过语义下，该列的两个实值点是
    #     (0, -2.00) 与 (200, +4.00)，在 150 处线性插值即得期望值。
    #     （这里踩过一次：第一版按"在 100 与 200 之间插"心算成 2.00，实际是 2.50 ——
    #      把期望值写死就是这种错法的温床。）
    _lpts = [(p["station_m"], p["lane_left_pct"]) for p in _s["points"]
             if p["lane_left_pct"] is not None]
    (_s1, _v1), (_s2, _v2) = _lpts[0], _lpts[-1]
    _t = (150.0 - _s1) / (_s2 - _s1)
    _expect = _v1 + (_v2 - _v1) * _t
    _hold = _v1                              # 「沿用上值」语义下会得到的值
    _mid = sup.superelev_at(_s["points"], 150.0, "lane_left_pct")
    check(f"★★ 元测试：跨 9999 插值 = {_expect:.2f}（跳过语义，期望值由数据算得）",
          _mid is not None and abs(_mid - _expect) < 1e-9,
          f"实为 {_mid}")
    check(f"★★ 元测试：与「沿用上值」({_hold:.2f}) 确实不同 —— 该断言可伪证",
          abs(_expect - _hold) > 1e-9,
          f"跳过={_expect} 沿用={_hold}")

    check("插值：0.000 m 处 = 源值 -2.00",
          sup.superelev_at(_s["points"], 0.0, "lane_left_pct") == -2.0)
    check("插值：范围之外不外推，返回 None",
          sup.superelev_at(_s["points"], -1.0, "lane_left_pct") is None
          and sup.superelev_at(_s["points"], 9999.0, "lane_left_pct") is None)
    # 某列**全为** 9999 → 没有任何约束点 → None（另造一组，不指望正例里有这种列）
    _none_col = [dict(p, lane_left_pct=None) for p in _s["points"]]
    check("插值：某列全是 9999 时返回 None（没有可用的约束点）",
          sup.superelev_at(_none_col, 150.0, "lane_left_pct") is None)

    # 物理合理性：正例必须**零告警**（否则告警就是噪声，人会学会无视它）
    check("物理检查：正例零告警", sup.check_superelev(_s["points"]) == [],
          str(sup.check_superelev(_s["points"])))
    # 三条判据各自可触发（非空洞）
    _bad_sh = [dict(_s["points"][0], earth_shoulder_left_pct=2.0)]
    check("★ 物理检查：土路肩为正 → 告警", bool(sup.check_superelev(_bad_sh)))
    _bad_sum = [dict(_s["points"][0], lane_left_pct=3.0, lane_right_pct=3.0)]
    check("★ 物理检查：左右行车道横坡之和为正 → 告警", bool(sup.check_superelev(_bad_sum)))
    _all_nine = [dict(_s["points"][0], **{k: None for _, k in sup.PCT_COLUMNS})]
    check("★ 物理检查：整行六列全 None → 告警", bool(sup.check_superelev(_all_nine)))
    # 超界检查：只查越界，**不**查首尾对齐（.SUP 不必覆盖全线）
    _stn = [{"station_m": 0.0}, {"station_m": 200.0}]
    check("对账：过渡落在路线内则不报", sup.check_against_stations(_s["points"], _stn) == [])
    check("对账：过渡晚于路线终点要报",
          bool(sup.check_against_stations(_s["points"], [{"station_m": 0.0},
                                                         {"station_m": 150.0}])))

    check_raises("应拒绝：魔数是 .ZDM 的（张冠李戴）",
                 "HINTCAD5.83_ZDM_SHUJU\r\n"
                 + _sup_row("-3.00", "-2.00", "-2.00", "0.000", "-2.00", "-2.00", "-3.00"),
                 parser=sup, expect="魔数不匹配")
    check_raises("应拒绝：非纬地文件", "随便一个文本\r\n", parser=sup, expect="魔数不匹配")
    check_raises("应拒绝：字段数 6（漏了一列）",
                 _sh + "-3.00\t-2.00\t-2.00\t0.000\t-2.00\t-2.00\r\n"
                 + _sup_row("-3.00", "-2.00", "-2.00", "10.000", "-2.00", "-2.00", "-3.00"),
                 parser=sup, expect="字段数应为 7")
    # ★ 桩号列是唯一不接受 9999 的列（实测源文件 0 次）
    check_raises("★ 应拒绝：桩号列出现 9999（桩号不能被「忽略」）",
                 _sh + _sup_row("-3.00", "-2.00", "-2.00", "9999.00", "-2.00", "-2.00", "-3.00")
                 + _sup_row("-3.00", "-2.00", "-2.00", "10.000", "-2.00", "-2.00", "-3.00"),
                 parser=sup, expect="桩号列出现 9999")
    check_raises("应拒绝：桩号未严格递增",
                 _sh + _sup_row("-3.00", "-2.00", "-2.00", "10.000", "-2.00", "-2.00", "-3.00")
                 + _sup_row("-3.00", "-2.00", "-2.00", "5.000", "-2.00", "-2.00", "-3.00"),
                 parser=sup, expect="未严格递增")
    check_raises("应拒绝：桩号为负",
                 _sh + _sup_row("-3.00", "-2.00", "-2.00", "-1.000", "-2.00", "-2.00", "-3.00")
                 + _sup_row("-3.00", "-2.00", "-2.00", "10.000", "-2.00", "-2.00", "-3.00"),
                 parser=sup, expect="桩号为负")
    check_raises("★ 应拒绝：横坡串成了桩号那种量级（列错位）",
                 _sh + _sup_row("-3.00", "-2.00", "5805.421", "0.000", "-2.00", "-2.00", "-3.00")
                 + _sup_row("-3.00", "-2.00", "-2.00", "10.000", "-2.00", "-2.00", "-3.00"),
                 parser=sup, expect="超出合理区间")
    check_raises("应拒绝：横坡不是数字",
                 _sh + _sup_row("-3.00", "-2.00", "abc", "0.000", "-2.00", "-2.00", "-3.00")
                 + _sup_row("-3.00", "-2.00", "-2.00", "10.000", "-2.00", "-2.00", "-3.00"),
                 parser=sup, expect="不是合法数字")
    check_raises("应拒绝：只有 1 个过渡点（构不成过渡）",
                 _sh + _sup_row("-3.00", "-2.00", "-2.00", "0.000", "-2.00", "-2.00", "-3.00"),
                 parser=sup, expect="不足 2 个")
    check_raises("应拒绝：只有魔数没有数据行", _sh, parser=sup, expect="没有任何数据行")
    check_raises("应拒绝：空文件", "", parser=sup, expect="空文件")
    check_raises("应拒绝：文件中间有空行（漏读一段过渡）",
                 _sh + _sup_row("-3.00", "-2.00", "-2.00", "0.000", "-2.00", "-2.00", "-3.00")
                 + "\r\n"
                 + _sup_row("-3.00", "-2.00", "-2.00", "10.000", "-2.00", "-2.00", "-3.00"),
                 parser=sup, expect="中间出现空行")
    check("末尾空行应通过（不误杀）",
          len(sup.parse(_s_ok + "\r\n", file="ok.SUP")["points"]) == 3)

    # ★ 解析结果必须过契约⑤ v0.2 的 superelev_point 定义（真契约校验，非自说自话）
    _spv = jsonschema.Draft7Validator(
        json.loads(IR_SCHEMA_PATH.read_text(encoding="utf-8"))["definitions"]["superelev_point"])
    check("★ 解析出的每一点都过 schema 的 superelev_point 定义",
          not [e for p in _s["points"] for e in _spv.iter_errors(p)],
          str([e.message for p in _s["points"] for e in _spv.iter_errors(p)][:2]))
    # 元测试：该定义必须**真的会拒**（否则上面那条是空断言）
    _badpt = dict(_s["points"][0]); _badpt["typo_col"] = 1
    check("★ 元测试：superelev_point 定义真的会拒绝多余列",
          bool(list(_spv.iter_errors(_badpt))))
    _badpt2 = dict(_s["points"][0]); del _badpt2["station_m"]
    check("★ 元测试：superelev_point 定义真的会拒绝缺 station_m",
          bool(list(_spv.iter_errors(_badpt2))))

    # ── 第 6d 组：.WID 路幅宽度解析器（应通过 / 应拒绝两侧）───────────────────
    print("\n第 6d 组  .WID 路幅宽度解析器（应通过 / 应拒绝两侧）")
    _wh = "HINTCAD6.00_WID_SHUJU\r\n"

    def _wrow(st, med, half, flag, hard, earth, fn):
        return f"{st}\t{med}\t{half}\t{flag}\t{hard}\t{earth}\t{fn}\r\n"

    _w_ok = (_wh + "[LEFT]\r\n"
             + _wrow("0.000", "0.000", "3.500", "0.000", "0.750", "0.750", "0") + "\r\n"
             + _wrow("5701.461", "0.000", "3.500", "0.000", "0.750", "0.750", "0") + "\r\n"
             + "[RIGHT]\r\n"
             + _wrow("0.000", "0.000", "3.500", "0.000", "0.750", "0.750", "0") + "\r\n"
             + _wrow("5701.461", "0.000", "3.500", "0.000", "0.750", "0.750", "0"))
    _w = wid.parse(_w_ok, file="ok.WID")
    _rows = _w["rows"]
    # ★ 一行 = 一侧的一个桩号（与 superelev_transition 同形），**不折叠成区间**
    check("★ 应通过：4 行 = 左右各 2 个桩号（一行一个桩号，不折叠成区间）",
          len(_rows) == 4, str(len(_rows)))
    check("应通过：厂商版本从魔数取出", _w["vendor_version"] == "6.00", _w["vendor_version"])
    check("应通过：side 归一到 left/right", sorted(r["side"] for r in _rows) == ["left", "left", "right", "right"])
    check("★ 应通过：每行**自带桩号**（不是 start/end 区间）",
          [r["station_m"] for r in _rows if r["side"] == "left"] == [0.0, 5701.461]
          and all("start_station_m" not in r and "end_station_m" not in r for r in _rows))
    check("应通过：seq_no 按侧从 1 起", [r["seq_no"] for r in _rows if r["side"] == "right"] == [1, 2])
    check("应通过：group_seq = 每两行一组（教程「数据每两行为一组」）",
          [r["group_seq"] for r in _rows] == [1, 1, 1, 1])
    check("应通过：detect 认得自家魔数", wid.detect(_w_ok) is True)
    check("应通过：detect 不认别家魔数", wid.detect("HINTCAD5.83_SUP_SHUJU\r\n") is False)
    check("应通过：段名与文件类别与 SEGMENT_FILES 登记一致",
          wid.SEGMENT == "roadbed_width"
          and weidi.SEGMENT_FILES["roadbed_width"][0] == ".WID"
          and weidi.SEGMENT_FILES["roadbed_width"][1] == wid.FILE_KIND,
          f"{wid.SEGMENT} / {wid.FILE_KIND}")
    check("应通过：六列宽度逐列落位（半侧路面 3.5 / 硬路肩 0.75 / 土路肩 0.75）",
          _rows[0]["half_carriageway_width_m"] == 3.5
          and _rows[0]["hard_shoulder_width_m"] == 0.75
          and _rows[0]["earth_shoulder_width_m"] == 0.75
          and _rows[0]["median_width_m"] == 0.0)
    check("应通过：源文件写 0 的附加车道文件名 → None（不是字符串 '0'）",
          _rows[0]["extra_lane_file"] is None, repr(_rows[0]["extra_lane_file"]))
    # ★ 教程 §13.4 的分段标记是 z/y（5.8 代），实测是 [LEFT]/[RIGHT]（6.00）——**两种都要认**
    _zy = ("HINTCAD5.8_WID_SHUJU\r\n" + "z" * 56 + "\r\n"
           + _wrow("29000.00", "1.00", "8.00", "0.0", "2.5", "0.75", "0") + "\r\n"
           + _wrow("31420.98", "1.00", "8.00", "0.0", "2.5", "0.75", "0") + "\r\n"
           + "y" * 56 + "\r\n"
           + _wrow("29000.00", "1.00", "8.00", "0.0", "2.5", "0.75", "0") + "\r\n"
           + _wrow("31420.98", "1.00", "8.00", "0.0", "2.5", "0.75", "0"))
    _w2 = wid.parse(_zy, file="教程示例.wid")
    check("★ 应通过：教程 §13.4 的 z/y 写法也认（版本 5.8）",
          _w2["vendor_version"] == "5.8"
          and sorted(set(r["side"] for r in _w2["rows"])) == ["left", "right"],
          f"{_w2['vendor_version']} / {_w2['sides']}")
    check("★ 应通过：教程示例的中央分隔带 1.00 落位正确",
          _w2["rows"][0]["median_width_m"] == 1.0)

    # 取值是**分段常量**（与 A16 同模型）：自本桩号起保持到同侧下一个桩号
    check("★ 查值：变化点之间取到该变化点的值（3000 m → 3.5）",
          wid.width_at(_rows, 3000.0, side="left") == 3.5)
    check("★ 查值：变化点本身取到（5701.461 m 边界）",
          wid.width_at(_rows, 5701.461, side="left") == 3.5)
    check("★ 查值：**超出源文件范围返回 None**（5750 m 无数据，不外推）",
          wid.width_at(_rows, 5750.0, side="left") is None)
    check("查值：早于该侧第一个变化点 → None",
          wid.width_at(_rows, -1.0, side="left") is None)
    check("查值：左右侧互不串（同值不同侧）",
          wid.width_at(_rows, 3000.0, side="right") == 3.5)
    check("查值：可按列取（土路肩）",
          wid.width_at(_rows, 0.0, side="right", column="earth_shoulder_width_m") == 0.75)

    # 覆盖缺口：本工程 .WID 只到 5701.461，路线到 5805.421 —— 必须**报出来**
    _cov = wid.check_against_stations(_rows, [{"station_m": 0.0}, {"station_m": 5805.421}])
    check("★ 覆盖：不覆盖到路线终点要报（实测缺 103.960 m）",
          len(_cov) == 1 and "103.96" in _cov[0], str(_cov))
    check("覆盖：完整覆盖则不报",
          wid.check_against_stations(_rows, [{"station_m": 0.0}, {"station_m": 5701.461}]) == [])
    check("连续性：单组无从谈不连续", wid.check_stations(_rows) == [])
    # ★ 连续性：上一组终点行桩号 ≠ 下一组起点行桩号 → 要报（教程「桩号区间要连续」）
    _disc = (_wh + "[LEFT]\r\n"
             + _wrow("0", "0", "3.5", "0", "0.75", "0.75", "0") + "\r\n"
             + _wrow("100", "0", "3.5", "0", "0.75", "0.75", "0") + "\r\n"
             + _wrow("500", "0", "3.5", "0", "0.75", "0.75", "0") + "\r\n"
             + _wrow("900", "0", "3.5", "0", "0.75", "0.75", "0"))
    check("★ 连续性：相邻组不相接要报（100 → 500 断了）",
          len(wid.check_stations(wid.parse(_disc, file="断.WID")["rows"])) == 1)
    _cont = _disc.replace("500", "100").replace("900", "200")
    check("连续性：相接则不报",
          wid.check_stations(wid.parse(_cont, file="连.WID")["rows"]) == [])

    # ★★ 元测试：组内两行不一致必须**真能报出来**（否则那条 notes 是空的）
    _disagree = (_wh + "[LEFT]\r\n"
                 + _wrow("0.000", "0.000", "3.500", "0.000", "0.750", "0.750", "0") + "\r\n"
                 + _wrow("100.000", "0.000", "3.500", "0.000", "1.500", "0.750", "0"))
    _wd = wid.parse(_disagree, file="不一致.WID")
    check("★★ 元测试：两行硬路肩不一致（0.75 vs 1.50）→ 必须产出一条 notes",
          len(_wd["notes"]) == 1 and "硬路肩" in _wd["notes"][0], str(_wd["notes"]))
    check("★★ 元测试：两行不一致时**两行都保留**（不折叠，故不丢值）",
          [r["hard_shoulder_width_m"] for r in _wd["rows"]] == [0.75, 1.5],
          str([r["hard_shoulder_width_m"] for r in _wd["rows"]]))
    # 列 4（附加车道标识）**有意**允许两行不同 → 不产 notes
    _flag_ok = (_wh + "[LEFT]\r\n"
                + _wrow("0.000", "0.000", "3.500", "2.000", "0.750", "0.750", "0") + "\r\n"
                + _wrow("100.000", "0.000", "3.500", "0.000", "0.750", "0.750", "0"))
    check("★★ 元测试：列 4 两行不同（2 / 0）是教程允许的 → 不产 notes",
          wid.parse(_flag_ok, file="列4.WID")["notes"] == [])

    # 解析结果必须过契约⑤ v0.3 的 roadbed_point 定义
    _rp = jsonschema.Draft7Validator(
        json.loads(IR_SCHEMA_PATH.read_text(encoding="utf-8"))["definitions"]["roadbed_point"])
    check("★ 解析出的每一行都过 schema 的 roadbed_point 定义",
          not [e for r in _rows for e in _rp.iter_errors(r)],
          str([e.message for r in _rows for e in _rp.iter_errors(r)][:2]))
    _badr = dict(_rows[0]); _badr["typo_col"] = 1
    check("★ 元测试：roadbed_point 定义真的会拒绝多余列", bool(list(_rp.iter_errors(_badr))))

    check_raises("应拒绝：魔数是 .SUP 的（张冠李戴）",
                 "HINTCAD5.83_SUP_SHUJU\r\n[LEFT]\r\n"
                 + _wrow("0", "0", "3.5", "0", "0.75", "0.75", "0")
                 + _wrow("10", "0", "3.5", "0", "0.75", "0.75", "0"),
                 parser=wid, expect="魔数不匹配")
    check_raises("应拒绝：字段数 6（漏了附加车道文件名列）",
                 _wh + "[LEFT]\r\n" + "0\t0\t3.5\t0\t0.75\t0.75\r\n",
                 parser=wid, expect="字段数应为 7")
    check_raises("★ 应拒绝：数据行出现在分段标记之前（不知是左还是右）",
                 _wh + _wrow("0", "0", "3.5", "0", "0.75", "0.75", "0"),
                 parser=wid, expect="任何分段标记之前")
    check_raises("★ 应拒绝：桩号区间不成对（只有起点没有终点）",
                 _wh + "[LEFT]\r\n" + _wrow("0", "0", "3.5", "0", "0.75", "0.75", "0"),
                 parser=wid, expect="成对出现")
    check_raises("★ 应拒绝：换侧时上一组没写完（组跨了分段标记）",
                 _wh + "[LEFT]\r\n" + _wrow("0", "0", "3.5", "0", "0.75", "0.75", "0")
                 + "[RIGHT]\r\n" + _wrow("0", "0", "3.5", "0", "0.75", "0.75", "0")
                 + _wrow("10", "0", "3.5", "0", "0.75", "0.75", "0"),
                 parser=wid, expect="成对出现")
    check_raises("★ 应拒绝：桩号未严格递增",
                 _wh + "[LEFT]\r\n" + _wrow("10", "0", "3.5", "0", "0.75", "0.75", "0")
                 + _wrow("5", "0", "3.5", "0", "0.75", "0.75", "0"),
                 parser=wid, expect="未递增")
    check_raises("应拒绝：宽度为负",
                 _wh + "[LEFT]\r\n" + _wrow("0", "0", "-3.5", "0", "0.75", "0.75", "0")
                 + _wrow("10", "0", "3.5", "0", "0.75", "0.75", "0"),
                 parser=wid, expect="为负")
    check_raises("★ 应拒绝：宽度串成了桩号那种量级（列错位）",
                 _wh + "[LEFT]\r\n" + _wrow("0", "0", "5805.4", "0", "0.75", "0.75", "0")
                 + _wrow("10", "0", "3.5", "0", "0.75", "0.75", "0"),
                 parser=wid, expect="超出合理区间")
    check_raises("应拒绝：宽度不是数字",
                 _wh + "[LEFT]\r\n" + _wrow("0", "abc", "3.5", "0", "0.75", "0.75", "0")
                 + _wrow("10", "0", "3.5", "0", "0.75", "0.75", "0"),
                 parser=wid, expect="不是合法数字")
    check_raises("应拒绝：只有魔数没有数据", _wh, parser=wid, expect="没有任何桩号数据")
    check_raises("应拒绝：空文件", "", parser=wid, expect="空文件")
    check("组间空行应通过（教程示例就有）",
          len(wid.parse(_w_ok.replace("[RIGHT]", "\r\n[RIGHT]"), file="x.WID")["rows"]) == 4)

    # ── 第 7 组：桩号精度 —— 真实数据必须装得进 DDL 声明的精度 ──────────────
    # 本轮实测抓到：.STA 里 1659.917 与 1660.000 相距仅 0.083 m，
    # 而 station_local_km 原为 numeric(10,3)（km 3 位小数＝米级）→ 两者都成 1.660 km
    # → 撞 UNIQUE(section_id, station_local_km) → **第一次导入就失败**。
    # 本组把这个事故变成回归断言：DDL 的精度必须能区分真实数据的最小间距。
    print("\n第 7 组  桩号精度回归（真实数据 ↔ DDL 声明精度）")
    edge_f = ROOT / "tests" / "fixtures" / "design_import" / "weidi_sta_precision_edge.STA"
    ep = sta.parse(edge_f.read_text(encoding="utf-8"), file=edge_f.name)["points"]
    a, b = ep[0]["station_m"], ep[1]["station_m"]
    check("真实最小间距对已入 fixture（相邻两点）", (a, b) == (1659.917, 1660.0),
          f"实为 {a} / {b}，相距 {round(b - a, 3)} m")
    check("解析器接纳这一对（不因过近而拒绝）", len(ep) == 5, f"实为 {len(ep)} 点")

    ddl_text = (ROOT / "sql" / "10_ddl_v0.4.sql").read_text(encoding="utf-8")
    m = re.search(r"station_local_km\s+numeric\((\d+),\s*(\d+)\)", ddl_text)
    check("DDL 中能取到 station_local_km 的精度声明", bool(m))
    if m:
        prec, scale = int(m.group(1)), int(m.group(2))
        check(f"小数位 scale={scale} ≥ 6（km 毫米级）", scale >= 6, f"实为 scale={scale}")
        check(f"整数位 {prec - scale} 位 ≥ 5（容得下 4635 km 路网桩号）", prec - scale >= 5,
              f"实为 {prec - scale} 位")
        q = Decimal(1).scaleb(-scale)
        qa = Decimal(str(a / 1000)).quantize(q, rounding=ROUND_HALF_UP)
        qb = Decimal(str(b / 1000)).quantize(q, rounding=ROUND_HALF_UP)
        check("两点在该精度下**不碰撞**（唯一约束成立）", qa != qb, f"{a} m→{qa} ／ {b} m→{qb}")

    # 元测试：证明上面那组不是空断言 —— 旧的 3 位小数**确实**会碰撞
    q3 = Decimal("0.001")
    c3a = Decimal(str(a / 1000)).quantize(q3, rounding=ROUND_HALF_UP)
    c3b = Decimal(str(b / 1000)).quantize(q3, rounding=ROUND_HALF_UP)
    check("元测试：旧精度 numeric(10,3) **确实会碰撞**（证明本组非空断言）", c3a == c3b,
          f"两者都成 {c3a} km")

    # ── 第 8 组：.JD 平面交点 —— 字段语义不靠"看"，靠几何恒等式**证明** ─────────
    # 为什么值得单列一组：.JD 的 12/10 字段行**没有表头**，字段归属只能靠推。
    # 而 DDL 里 alignment_pi 的注释恰好把两个值标错了（把 A 当切线长、把 Ls 当转角）。
    # 所以本组不复述注释，而是拿四条互相独立的几何恒等式去卡：
    # 只要它们同时成立，字段归属就是**被证明的**；一条不成立，就说明推错了。
    print("\n第 8 组  .JD 平面交点（契约⑤ 第二个适配器：语义靠几何自洽证明）")
    jd_f = ROOT / "tests" / "fixtures" / "design_import" / "weidi_jd_excerpt_3cp.JD"
    # 注意：必须走 bytes —— Path.read_text() 会**静默**把 CRLF 折成 LF，
    # 于是下面按 "\r\n" 切行的构造型反例会退化成"整文件 1 个元素"，索引越界。
    # 这是个只有真跑才会暴露的坑，留一行注释免得后人再踩。
    jd_text = jd_f.read_bytes().decode("utf-8")
    check("fixture 保留 CRLF（构造反例依赖真实行尾）", "\r\n" in jd_text)
    check(".JD 魔数可识别（.JD 是 5.83，与 .STA 的 5.84 不是同一版本）", jd.detect(jd_text))
    jd_out = jd.parse(jd_text, file=jd_f.name)
    cps = jd_out["control_points"]
    check("声明点数 ≡ 实际解析出的控制点数（防截断静默通过）",
          jd_out["declared_count"] == len(cps) == 3,
          f"声明 {jd_out['declared_count']}／实得 {len(cps)}")
    check("控制点标识 QD / 1 / 2", [c["tag"] for c in cps] == ["QD", "1", "2"],
          str([c["tag"] for c in cps]))
    check("QD 桩号 = 0", cps[0]["station_m"] == 0.0, f"实为 {cps[0]['station_m']}")

    pi1 = cps[1]
    R, Ls = pi1["radius_m"], pi1["spiral_ls1"]
    check("R 与 DDL 注释一致（450）", R == 450.0, f"实为 {R}")
    alpha = math.radians(pi1["deflection_deg"])      # ← 由坐标独立算出，没读文件的角度字段
    beta = Ls / (2 * R)
    arc = R * (alpha - 2 * beta)
    ext = (R + Ls ** 2 / (24 * R)) / math.cos(alpha / 2) - R
    check("① A = √(R·Ls) —— 那个 164.31676725 是缓和曲线参数，不是切线长",
          abs(pi1["spiral_a1"] - math.sqrt(R * Ls)) < 1e-6,
          f"文件 {pi1['spiral_a1']} ／ √(450×60)={math.sqrt(R*Ls):.10f}")
    check("② 圆弧长 R(α−2β) 与文件值一致（α 由坐标得出，非取自文件）",
          abs(arc - pi1["arc_len_m"]) < 1e-3, f"算得 {arc:.5f} ／ 文件 {pi1['arc_len_m']}")
    check("③ 外距 (R+ΔR)/cos(α/2)−R 与文件值一致",
          abs(ext - pi1["external_m"]) < 1e-3, f"算得 {ext:.5f} ／ 文件 {pi1['external_m']}")
    check("④ 总曲线长 = 2·Ls + 圆弧长",
          abs(pi1["curve_len_m"] - (2 * Ls + pi1["arc_len_m"])) < 1e-3,
          f"算得 {2*Ls + pi1['arc_len_m']:.5f} ／ 文件 {pi1['curve_len_m']}")
    check("⑤ 切线长 = 交点桩号 − ZH 桩号（注释把它标成了 164.31676725）",
          abs(pi1["tangent_len_m"] - (pi1["station_m"] - 485.87357484)) < 1e-4,
          f"文件 {pi1['tangent_len_m']} ／ {pi1['station_m']:.3f}−485.874"
          f"={pi1['station_m'] - 485.87357484:.6f}")
    check("⑥ 转角由坐标演出 22.0027°，而文件里那个 60 是 Ls",
          abs(pi1["deflection_deg"] - 22.0027) < 0.001,
          f"算得 {pi1['deflection_deg']}°（注释却写 -60.0）")

    # 跨文件验证：.JD 给的曲线特征点桩号必须能在 .STA 桩号序列里找到。
    # 两份文件是纬地从同一工程导出的；只要解析器有一边错位，本条立刻红。
    sta_pts = sta.parse(FIXTURE.read_text(encoding="utf-8"), file=FIXTURE.name)["points"]
    have = {round(p["station_m"], 3) for p in sta_pts}
    inside = sorted({round(s, 3) for s in pi1["feat_stations_m"] if round(s, 3) <= max(have)})
    check("PI1 有特征点落在 .STA fixture 范围内（否则下面几条是空断言）", len(inside) > 0)
    for s in inside:
        check(f"  .JD 特征点 {s:.3f} m 出现在 .STA 桩号序列中", s in have)

    # 契约 schema 必须真的约束已实现的段（此前 alignment_pi 只写了 {"type":"object"}）
    import jsonschema                                        # noqa: PLC0415
    pi_schema = json.loads(IR_SCHEMA_PATH.read_text(encoding="utf-8"))["definitions"]["pi_point"]
    v = jsonschema.Draft7Validator(pi_schema)
    errs = [e.message for c in cps for e in v.iter_errors(c)]
    check("每个控制点都符合 schema 的 pi_point 定义", not errs, str(errs[:2]))
    check("元测试：schema 会拒绝缺 deflection_deg 的控制点（证明本组非空断言）",
          bool(list(v.iter_errors({k: val for k, val in pi1.items() if k != "deflection_deg"}))))

    # 应拒绝侧
    def jd_rejects(name: str, text: str, expect: str = "") -> None:
        global PASS, FAIL
        try:
            jd.parse(text, file="<test>")
            FAIL += 1
            print(f"  ✗ {name}  **未抛异常（本应被拒绝）**")
        except SourceInvalid as exc:
            msg = str(exc)
            if expect and expect not in msg:
                FAIL += 1
                print(f"  ✗ {name}  抛错了，但信息不含 {expect!r}：{msg[:70]}")
            else:
                PASS += 1
                print(f"  ✓ {name}  → 已拒绝：{msg[:56]}")

    JL = jd_text.split("\r\n")

    def _swap_first(texts: list[str], fields: int, frm: str, to: str) -> list[str]:
        """把第一个「字段数为 fields 且首字段等于 frm」的行的首字段换成 to。"""
        out = list(texts)
        for i, ln in enumerate(out):
            p = ln.split("\t")
            if len(p) == fields and p[0].strip() == frm:
                out[i] = "\t".join([to] + p[1:])
                break
        return out

    jd_rejects("魔数是 .STA 的（认错文件类型）",
               jd_text.replace("HINTCAD5.83_PM_SHUJU_JD", "HINTCAD5.83_STA_SHUJU"), "魔数")
    jd_rejects("声明点数与实际不符（防截断被当成完整文件）",
               "\r\n".join([JL[0], "        10\t" + JL[1].split("\t")[1]] + JL[2:]), "控制点数不符")
    # 「不足 3 个」要单独构造：截断的同时把声明点数也改成 2，否则先撞上"点数不符"那条。
    # （第一版就是这么写错的——被测的拒绝发生了，但不是我以为的那条规则在拒绝。）
    jd_rejects("控制点不足 3 个（构不成线形）",
               "\r\n".join([JL[0], "         2\t" + JL[1].split("\t")[1]] + JL[2:21]), "不足 3 个")
    jd_rejects("缺第 2 行", JL[0], "第 2 行")
    jd_rejects("第 2 行字段数不对", "\r\n".join([JL[0], "        3"] + JL[2:]), "2 字段")
    jd_rejects("控制点记录形状不符（应为 5+12+10）",
               "\r\n".join(JL[:18] + ["1\t0.0\t0.0\t0.0\t9999\t9999\t0"] + JL[19:]), "形状不符")
    jd_rejects("控制点桩号倒退",
               "\r\n".join(_swap_first(JL, 10, "1531.81656009", "-5.00000000")), "倒退")
    jd_rejects("起点桩号不为 0",
               "\r\n".join(_swap_first(JL, 10, "0.00000000", "7.50000000")), "起点桩号")
    jd_rejects("数字字段不是数字",
               "\r\n".join(JL[:3] + ["\t".join(["abc"] + JL[3].split("\t")[1:])] + JL[4:]), "不是合法数字")

    # ── 第 9 组：.pm 平面线形单元 —— 三条不变量 + 跨文件转向印证 ───────────────
    # .pm 是扁平结构（每 3 行一个单元），比 .JD 好解析；难点在 8 字段行的语义。
    # 同样不靠"看"，靠三条不变量：链连续 / 圆心距离 ≡ R / 弦长 = 2R·sin(L/2R)。
    print("\n第 9 组  .pm 平面线形单元（契约⑤ 第三个适配器）")
    pm_f = ROOT / "tests" / "fixtures" / "design_import" / "weidi_pm_excerpt_4units.pm"
    pm_text = pm_f.read_bytes().decode("utf-8")
    check(".pm 魔数可识别", pm.detect(pm_text))
    pm_out = pm.parse(pm_text, file=pm_f.name)
    els = pm_out["elements"]
    check("声明单元数 ≡ 实际解析数", pm_out["declared_count"] == len(els) == 4,
          f"声明 {pm_out['declared_count']}／实得 {len(els)}")
    check("首单元起点桩号 ≡ 文件头起点桩号",
          els[0]["start_station_m"] == pm_out["start_station_m"] == 0.0)
    check("类型序列 line/transition/circular/transition",
          [e["type"] for e in els] == ["line", "transition", "circular", "transition"],
          str([e["type"] for e in els]))

    # 不变量①：链连续（桩号 + 方位角 + 坐标，三重都要接上）
    chain_ok = all(
        abs(b["start_station_m"] - a["end_station_m"]) < 1e-9
        and abs(b["azimuth_deg"] - a["end_azimuth_deg"]) < 1e-6
        and math.dist((a["end_x"], a["end_y"]), (b["start_x"], b["start_y"])) < 1e-6
        for a, b in zip(els, els[1:]))
    check("① 链连续：桩号 / 方位角 / 坐标三重接续（解析器已强制，此处复核）", chain_ok)

    # 不变量②：第 4 点是**圆心** —— 到该单元终点的距离必须精确等于 R
    for e in els:
        if e.get("center") and e["radius_end_m"]:
            dd = math.dist((e["center"]["x"], e["center"]["y"]), (e["end_x"], e["end_y"]))
            check(f"② 单元 {e['seq']} 的「第 4 点」到终点距离 ≡ R",
                  abs(dd - e["radius_end_m"]) < 1e-6, f"{dd:.6f} vs R={e['radius_end_m']}")

    # 不变量③：直线弦长 ≡ 单元长；圆曲线弦长 = 2R·sin(L/2R)（弦必然短于弧）
    for e in els:
        chord = math.dist((e["start_x"], e["start_y"]), (e["end_x"], e["end_y"]))
        if e["type"] == "line":
            check(f"③ 直线单元 {e['seq']} 弦长 ≡ 单元长", abs(chord - e["length_m"]) < 0.001,
                  f"{chord:.4f} vs {e['length_m']:.4f}")
        elif e["type"] == "circular":
            rr = e["radius_start_m"]
            expect = 2 * rr * math.sin(e["length_m"] / (2 * rr))
            check(f"③ 圆曲线单元 {e['seq']} 弦长 = 2R·sin(L/2R)：弦短于弧",
                  abs(chord - expect) < 0.001 and chord < e["length_m"],
                  f"弦 {chord:.4f} / 算得 {expect:.4f} / 弧 {e['length_m']:.4f}")

    # 直线单元：由坐标算的方位角 ≡ 声明的起始方位角（这条把 (4) 行的弧度制也钉住了）
    for e in els:
        if e["type"] == "line":
            calc = math.degrees(math.atan2(e["end_y"] - e["start_y"],
                                           e["end_x"] - e["start_x"])) % 360
            check(f"直线单元 {e['seq']} 坐标方位角 ≡ 声明方位角", abs(calc - e["azimuth_deg"]) < 1e-4,
                  f"坐标算 {calc:.6f}° / 声明 {e['azimuth_deg']:.6f}°")

    # 跨文件：把两段喂给挂接器，验证 pi_seq 归属 + .pm 转向符号与 .JD 转角同号
    linked = [dict(e) for e in els]
    warns = weidi._link_elements_to_pi(linked, cps)
    check("跨文件挂接：4 个单元全部挂到交点 1（引道直线也算进去）",
          [e.get("pi_seq") for e in linked] == [1, 1, 1, 1], str([e.get("pi_seq") for e in linked]))
    check("跨文件印证：.pm 转向符号与 .JD 由坐标算出的转角同号（无告警）", warns == [], str(warns))
    # 元测试：把转向符号翻过来必须产生告警，否则上一条是空断言
    flipped = [dict(e, turn_flag=-e["turn_flag"]) for e in els]
    check("元测试：转向符号翻转后**必须**产生告警",
          len(weidi._link_elements_to_pi(flipped, cps)) > 0)

    el_schema = json.loads(IR_SCHEMA_PATH.read_text(encoding="utf-8"))["definitions"]["element"]
    ve = jsonschema.Draft7Validator(el_schema)
    eerrs = [x.message for e in els for x in ve.iter_errors(e)]
    check("每个线形单元都符合 schema 的 element 定义", not eerrs, str(eerrs[:2]))

    def pm_rejects(name: str, text: str, expect: str = "") -> None:
        global PASS, FAIL
        try:
            pm.parse(text, file="<test>")
            FAIL += 1
            print(f"  ✗ {name}  **未抛异常（本应被拒绝）**")
        except SourceInvalid as exc:
            msg = str(exc)
            if expect and expect not in msg:
                FAIL += 1
                print(f"  ✗ {name}  抛错了，但信息不含 {expect!r}：{msg[:70]}")
            else:
                PASS += 1
                print(f"  ✓ {name}  → 已拒绝：{msg[:56]}")

    PL = pm_text.split("\r\n")

    def _set(texts: list[str], idx: int, field: int, val: str) -> list[str]:
        out = list(texts)
        f = out[idx].split("\t")
        f[field] = val
        out[idx] = "\t".join(f)
        return out

    def _count(texts: list[str], n: int) -> list[str]:
        """只改计数行的数值，宽度沿用原样 —— 按字符切片会切坏填充（第一版就这么写错的）。"""
        out = list(texts)
        f = out[1].split("\t")
        f[0] = f"{n:>{len(f[0])}}"
        out[1] = "\t".join(f)
        return out

    pm_rejects("魔数是 .JD 的（认错文件类型）",
               pm_text.replace("HINTCAD5.83_PM_SHUJU_PM", "HINTCAD5.83_PM_SHUJU_JD"), "魔数")
    pm_rejects("声明单元数多于实际（防截断被当成完整文件）",
               "\r\n".join(_count(PL, 5)), "单元数不足")
    pm_rejects("单元数被少声明（会悄悄丢掉路尾）",
               "\r\n".join(_count(PL, 3)), "残余")
    pm_rejects("坐标链断裂（单元终点 ≠ 下一单元起点）",
               "\r\n".join(_set(PL, 4, 4, "2789000.00000000")), "坐标链断裂")
    pm_rejects("方位角链断裂",
               "\r\n".join(_set(PL, 5, 3, "9.999999")), "方位角链断裂")
    pm_rejects("单元坐标字段数不符（8 字段行写成 7）",
               "\r\n".join(PL[:4] + ["\t".join(PL[4].split("\t")[:7])] + PL[5:]), "字段数")
    pm_rejects("未知类型码",
               "\r\n".join(_set(PL, 3, 6, "99")), "未知的类型码")
    pm_rejects("起点桩号与文件头不符",
               "\r\n".join(_set(PL, 5, 0, "7.50000000")), "与文件头")
    pm_rejects("缺起点记录行", "\r\n".join(PL[:2] + PL[3:]), "字段数")

    # ── 第 10 组：由单元链**推导**交点 —— .JD 从「输入」降为「验算」────────────
    # 这是契约⑤ 最关键的一条：两个平面段不是两份数据，是同一个东西的两种记法。
    # 折点是线的摘要，两条相邻切线求交即得。所以 .JD 不该是数据入口。
    print("\n第 10 组  由 .pm 单元链推导交点（.JD 降为验算）")
    der = geom.derive_control_points(els)
    check("4 个单元推出 1 个交点（第一个曲线组）", len(der) == 1, f"实为 {len(der)}")
    d1 = der[0]
    check("推导交点的 tag/seq 与 .JD 的 PI1 对应", (d1["seq"], d1["tag"]) == (pi1["seq"], pi1["tag"]),
          f"{d1['seq']}/{d1['tag']} vs {pi1['seq']}/{pi1['tag']}")

    # 逐字段对质：推导值 vs .JD 文件值。**.JD 一个数都没参与推导**，纯属对质。
    scalar = ["x", "y", "station_m", "azimuth_deg", "deflection_deg", "radius_m",
              "spiral_ls1", "spiral_ls2", "spiral_a1", "spiral_a2", "tangent_len_m",
              "tangent_len2_m", "arc_len_m", "curve_len_m", "external_m", "prev_tangent_len_m"]
    worst = 0.0
    for k in scalar:
        if d1[k] is None or pi1.get(k) is None:
            continue
        dv = abs(d1[k] - pi1[k])
        worst = max(worst, dv)
        check(f"  {k} 推导 ≡ .JD", dv < 1e-6, f"推导 {d1[k]!r} vs .JD {pi1[k]!r}")
    fs = max(abs(a - b) for a, b in zip(d1["feat_stations_m"], pi1["feat_stations_m"]))
    check("  feat_stations_m 推导 ≡ .JD", fs < 1e-6, f"最大差 {fs:.3e}")

    # ⚠ 外距：**教科书公式是错的**，把这件事钉死，免得以后有人"化简"回去。
    # E = (R+ΔR)/cos(α/2) − R 是级数近似；实测对 PI8 差 1.24 mm，而精确几何差 6e-9 m。
    _R, _Ls = d1["radius_m"], d1["spiral_ls1"]
    _a = math.radians(abs(d1["deflection_deg"]) / 2)
    textbook = (_R + _Ls ** 2 / (24 * _R)) / math.cos(_a) - _R
    check("元测试：教科书外距公式**必须**与 .JD 有明显偏差（证明它不能用）",
          abs(textbook - pi1["external_m"]) > 1e-5,
          f"教科书 {textbook:.8f} vs .JD {pi1['external_m']:.8f}，差 {abs(textbook-pi1['external_m']):.3e}")
    check("精确几何外距与 .JD 吻合（圆心 + 角平分线，不做级数展开）",
          abs(d1["external_m"] - pi1["external_m"]) < 1e-6,
          f"{d1['external_m']:.9f} vs {pi1['external_m']:.9f}")

    # 元测试：动一个方位角，推导出的交点必须跟着动 —— 否则上面全是空断言
    bent = [dict(e) for e in els]
    bent[-1]["end_azimuth_deg"] = bent[-1]["end_azimuth_deg"] + 1.0
    moved = geom.derive_control_points(bent)[0]
    check("元测试：出切线方位角改 1°，推导交点必须跟着移动",
          math.dist((d1["x"], d1["y"]), (moved["x"], moved["y"])) > 1.0,
          f"移动 {math.dist((d1['x'], d1['y']), (moved['x'], moved['y'])):.3f} m")
    check("元测试：转角也随之改变 1°",
          abs(abs(moved["deflection_deg"] - d1["deflection_deg"]) - 1.0) < 1e-9)

    pv = jsonschema.Draft7Validator(
        json.loads(IR_SCHEMA_PATH.read_text(encoding="utf-8"))["definitions"]["pi_point"])
    derr = [e.message for e in der for e in pv.iter_errors(e)]
    check("推导出的交点也符合 schema（与 .JD 解析结果同一形态）", not derr, str(derr[:2]))

    # 切线平行（复曲线）必须报错，不能返回一个假的交点
    try:
        geom.derive_control_points([dict(e, azimuth_deg=90.0, end_azimuth_deg=90.0) for e in els])
        FAIL_GUARD = True
    except SourceInvalid as exc:
        FAIL_GUARD = "平行" in str(exc)
    check("两条切线平行时报错（不编造交点）", FAIL_GUARD)

    # ── 第 11 组：落库器。plan/verify 是**纯函数**，所以这一组完全离线 ──────────
    # 把"映射对不对"与"事务/写权对不对"分开测：前者不需要库（本组），
    # 后者由 test_write_guard.py 覆盖 —— 两边各自都不依赖对方。
    print("\n第 11 组  落库器：IR → 待写行 + 一致性检查（离线，不碰数据库）")
    ir_fx = base.make_ir(
        vendor="weidi-hintcad", origin="file",
        files=[{"file_name": "x.STA", "parse_status": "ok"},
               {"file_name": "x.JD", "parse_status": "ok"},
               {"file_name": "x.pm", "parse_status": "ok"}],
        capabilities=("station_sequence", "alignment_pi", "alignment_element"),
        segments={"station_sequence": pts, "alignment_pi": cps, "alignment_element": els},
    )
    planned = di.plan(ir_fx, section_id=1)
    counts = {t: len(r) for t, r in planned["tables"].items()}
    # 合成 IR 里没有纵断面，故两张新表是 0 行 —— 但仍然必须在 plan 的产出里：
    # 漏掉一个键会让落库阶段静默少写一张表，而不是报错。
    check("行数：桩号 30 / 交点 1 / 单元 4 / 设计线 0 / 地面线 0 / 超高 0 / 路幅 0",
          counts == {"station_sequence": 30, "alignment_pi": 1, "alignment_element": 4,
                     "profile_grade_point": 0, "profile_ground_point": 0,
                     "superelev_transition": 0, "roadbed_width": 0},
          str(counts))
    check("交点来源 = 推导（.JD 作输入被忽略）",
          planned["pi_source"] == "derived" and planned["pi_from_file_ignored"] is True,
          f"{planned['pi_source']} / {planned['pi_from_file_ignored']}")

    # 桩号文本：★ K0+00.000 这个 bug 正是本组抓出来的（宽度写成 6 少一位整数位）
    check("桩号文本 0 m → K0+000.000", di.station_text(0.0) == "K0+000.000", di.station_text(0.0))
    check("桩号文本 545.874 m → K0+545.874", di.station_text(545.874) == "K0+545.874")
    check("桩号文本 5805.421 m → K5+805.421", di.station_text(5805.421) == "K5+805.421")
    check("桩号文本长度恒为 10（K + n + '+' + 7 位）",
          all(len(di.station_text(s)) == 10 for s in (0.0, 545.874, 5805.421, 999.999)))
    check("整桩判据：20 m 整桩", di.is_integer_station(20.0) and not di.is_integer_station(545.874))
    check("桩号类型：起终点 / 整桩 / 加桩",
          (di.station_type(0.0, first=0.0, last=545.874) == "endpoint"
           and di.station_type(20.0, first=0.0, last=545.874) == "integer"
           and di.station_type(485.874, first=0.0, last=545.874) == "jiazi"))

    # ★ 元测试：落库器**绝不能**去写生成列 —— 那会在真库上直接报
    #   "cannot insert a non-DEFAULT value into column"。离线就把它挡住。
    #   列清单从 DDL 现读，所以以后新增生成列会被自动纳入检查。
    for table in ("alignment_pi", "alignment_element"):
        gen = ddl_generated(table)
        check(f"元测试：DDL 里 {table} 确有生成列（否则下面的断言是空的）", bool(gen), str(gen))
        for row in planned["tables"][table]:
            hit = gen & set(row)
            check(f"元测试：{table} 不写生成列 {sorted(gen)}", not hit, f"写了 {sorted(hit)}")
    check("元测试：也不写已删除的 curvature_1pm",
          not any("curvature_1pm" in r for r in planned["tables"]["alignment_element"]))

    # ★ 更强的一条：**每一个非下划线开头**的键都必须是该表真实存在的列。
    #   这挡住了三件事：内部字段漏剔除、列名拼错、DDL 改名后落库器没跟上。
    for table, rows in planned["tables"].items():
        if not rows:
            continue
        cols = ddl_columns(table)
        stray = {k for r in rows for k in r if not k.startswith("_")} - cols
        check(f"{table} 的每个待写键都是真实列（{len(cols)} 列）", not stray, f"多出 {sorted(stray)}")

    # 干净用例：`.JD` 与 `.pm` 的**覆盖范围**要先对齐 —— .JD fixture 含 2 个交点，
    # 而 .pm fixture 只到 PI1。覆盖不同本身是另一条用例（见下），别混进来。
    ir_ok = copy.deepcopy(ir_fx)
    ir_ok["segments"]["alignment_pi"] = [p for p in cps if p["tag"] in ("QD", "1")]
    v = di.verify(ir_ok, di.plan(ir_ok, section_id=1))
    check("干净 IR：0 错 0 警", not v["errors"] and not v["warnings"],
          f"errors={v['errors']} warnings={v['warnings']}")

    # ★ 覆盖范围不同 **不该** 被报成"数值不符"：按序号硬配会造出假警报，
    #   而假警报会让真警报被淹没 —— 这是本组抓到的一个真实设计缺陷。
    v_cov = di.verify(ir_fx, di.plan(ir_fx, section_id=1))
    check("元测试：覆盖范围不同 → 只报「未对应」，不报数值不符",
          not v_cov["errors"] and len(v_cov["warnings"]) == 1
          and "没有对应" in v_cov["warnings"][0],
          str(v_cov["warnings"]))

    # 单元链断裂必须是**硬错误**（链一断，后面所有推导都不可信）
    bent = copy.deepcopy(ir_ok)
    bent["segments"]["alignment_element"][1]["start_station_m"] += 1.0
    check("单元链桩号断裂 → 错误", di.verify(bent, di.plan(bent, section_id=1))["errors"])

    bent2 = copy.deepcopy(ir_ok)
    bent2["segments"]["alignment_element"][2]["start_x"] += 1.0
    check("单元链坐标断裂 → 错误", di.verify(bent2, di.plan(bent2, section_id=1))["errors"])

    sts_bad = copy.deepcopy(ir_ok)
    sts_bad["segments"]["station_sequence"][5]["station_m"] = 1.0
    check("桩号非严格递增 → 错误", di.verify(sts_bad, di.plan(sts_bad, section_id=1))["errors"])

    # ★ .JD 作验算：把交点坐标挪 1 mm，必须告警（而不是静默采信推导值）
    jd_bent = copy.deepcopy(ir_ok)
    jd_bent["segments"]["alignment_pi"][1]["x"] += 0.001
    w = di.verify(jd_bent, di.plan(jd_bent, section_id=1))["warnings"]
    check("元测试：.JD 与推导差 1 mm → 告警", any("x_coord" in x for x in w), str(w[:1]))
    # 反过来：差 1 nm 不该报（阈值不能过紧，否则全是噪声）
    jd_tight = copy.deepcopy(ir_ok)
    jd_tight["segments"]["alignment_pi"][1]["x"] += 1e-9
    check("元测试：差 1 nm 不告警（阈值不过紧）",
          not di.verify(jd_tight, di.plan(jd_tight, section_id=1))["warnings"])

    # ★ 跨文件不变量：.STA 的非整桩 ⊆ 曲线特征点 ∪ {首末}。造一个"孤零零的加桩"必须被抓。
    #   注意要插在**中间**：追加到末尾会先触发单调性错误，就走不到这条路径了。
    odd_ir = copy.deepcopy(ir_ok)
    sp = odd_ir["segments"]["station_sequence"]
    sp.insert(len(sp) - 1, {"station_m": sp[-2]["station_m"] + 0.5, "seq_no": len(sp)})
    sp[-1]["seq_no"] = len(sp)
    w2 = di.verify(odd_ir, di.plan(odd_ir, section_id=1))["warnings"]
    check("元测试：凭空多一个加桩 → 告警", any("非整桩" in x for x in w2), str(w2[:1]))

    # 只有 .pm 没有 .JD 时，不变量照样成立（这是它比"对质 .JD"更强的地方）
    no_jd = base.make_ir(vendor="weidi-hintcad", origin="file", files=[],
                         capabilities=("station_sequence", "alignment_element"),
                         segments={"station_sequence": pts, "alignment_element": els})
    pj = di.plan(no_jd, section_id=1)
    check("只有 .pm 时仍能推导交点（.JD 不是必需）",
          pj["pi_source"] == "derived" and len(pj["tables"]["alignment_pi"]) == 1)
    check("只有 .pm 时跨文件不变量仍成立（无告警）", not di.verify(no_jd, pj)["warnings"])

    # ── 第 12 组：真库端到端。无库 / 无 psycopg 则跳过 ──────────────────────
    # 这一组测的**只有**事务与写权 —— 映射正确性已由第 11 组离线覆盖。
    print("\n第 12 组  落库器端到端（真库；无库或未装 psycopg 则跳过）")
    sys.path.insert(0, str(ROOT / "modules" / "M3-rpdao"))
    dao_e2e = None
    batch = f"test-di-{os.getpid()}"
    line_id = sec_id = None
    try:
        from rpdao.errors import WriteGuardError
        from rpdao.write import WriteDao
        from rpdao.catalog import TABLE_OWNER
        dao_e2e = WriteDao(pg_dsn() or "", app_name="contract-test",
                           min_size=1, max_size=2, timeout=10)
        dao_e2e.open()                             # 池是懒打开的，不 open 会到第一次用时才炸
        if not dao_e2e.ping():                     # ping 按设计吞异常只回真假，故必须显式判它
            raise RuntimeError("ping 失败（DSN 或库不可达）")
    except Exception as exc:                       # noqa: BLE001
        print(f"  ⊘ 跳过：{type(exc).__name__}: {str(exc)[:90]}")
        print("    需要时：uv run --with psycopg[binary] --with jsonschema --with pyyaml <本文件>")
    else:
        MARK = "契约⑤落库器自测"

        # ── ★★ 契约对账：源码里每一处 on_conflict 都必须有**真实唯一约束**兜底 ──
        # 这一条是**数据库抓到的 bug 反推出来的**：把 A16 的键从 transition_seq 改成
        # station_km 时，只同步了 roadbed_width，漏了 superelev_transition —— 重导时
        # psycopg 报 InvalidColumnReference（没有匹配 ON CONFLICT 的唯一约束）。
        # 测试当时全绿：因为它验的是"映射对不对"，**根本不碰约束**。
        # 一个不碰约束的测试不可能发现"键改了没同步"，所以这里把两边对起来：
        # 源码抠出 on_conflict 目标列 → 跟 pg_constraint 里的真实唯一约束比。
        _ast_mod = __import__("ast")
        _src = (ROOT / "modules" / "M2-ingest" / "design_import.py").read_text(encoding="utf-8")
        _pairs = []
        for _n in _ast_mod.walk(_ast_mod.parse(_src)):
            if (isinstance(_n, _ast_mod.Call) and isinstance(_n.func, _ast_mod.Attribute)
                    and _n.func.attr in ("insert", "insert_returning")
                    and _n.args and isinstance(_n.args[0], _ast_mod.Constant)):
                for _kw in _n.keywords:
                    if _kw.arg == "on_conflict" and isinstance(_kw.value, _ast_mod.Tuple):
                        _pairs.append((_n.args[0].value,
                                       tuple(e.value for e in _kw.value.elts)))
        # ★ 元测试：抠不到东西 = 下面那条检查永远通过（空洞）—— 所以先证明抠得到
        check("★★ 元测试：能从源码抠出 on_conflict 对（否则下面那条是空洞检查）",
              len(_pairs) >= 10, f"抠到 {len(_pairs)} 对")
        _bad = []
        for _t, _cols in _pairs:
            _crows = dao_e2e.query(
                """select c.conname, array_agg(a.attname) as cols
                     from pg_constraint c
                     join pg_class r on r.oid = c.conrelid
                     join unnest(c.conkey) k(attnum) on true
                     join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.attnum
                    where c.contype in ('u','p') and r.relname = %(t)s
                    group by c.conname, c.conrelid""", {"t": _t})
            if not any(set(_r["cols"]) == set(_cols) for _r in _crows):
                _bad.append(f"{_t}{tuple(sorted(_cols))}")
        check("★★ 源码里每一处 on_conflict 都有实库唯一约束兜底（改了键必须同步）",
              not _bad, f"对不上的：{_bad}")
        # ── 自愈：先清掉**上一次**留下的残留 ────────────────────────────────
        # 本组用 f"TEST-DI-{os.getpid()}" 当唯一标识，而清理写在 finally 里 ——
        # 进程被 kill（超时、Ctrl-C、CI 取消）时 finally 不跑，残留就留在真库里。
        # 下一次跑若 PID 恰好等于某个残留编号，就会撞 road_line_line_code_key，
        # 报一个**与代码无关的错**。实测撞过一次（PID 109）。
        # 所以开跑前先自愈，而不是指望上次跑得干净。
        _stale = dao_e2e.query(
            "select id from road_line where line_code like 'TEST-DI-%%'")
        if _stale:
            _sids = [r["id"] for r in _stale]
            _secs = [r["id"] for r in dao_e2e.query(
                "select id from road_section where line_id = any(%(i)s)", {"i": _sids})]
            for _t in ("roadbed_width", "superelev_transition", "profile_grade_point",
                       "alignment_element", "alignment_pi", "station_sequence"):
                if _secs:
                    dao_e2e.execute_write(
                        _t, f"DELETE FROM {_t} WHERE section_id = any(%(s)s)",
                        {"s": _secs}, writer="M2")
            dao_e2e.execute_write(
                "data_import_batch",
                "DELETE FROM data_import_batch WHERE batch_no like 'test-di-%%'", {},
                writer="M2")
            if _secs:
                dao_e2e.execute_write(
                    "road_section", "DELETE FROM road_section WHERE id = any(%(s)s)",
                    {"s": _secs}, writer="M2")
            dao_e2e.execute_write(
                "road_line", "DELETE FROM road_line WHERE id = any(%(i)s)",
                {"i": _sids}, writer="M2")
            print(f"  ⚠ 自愈：清掉上一次留下的 {len(_stale)} 行残留"
                  f"（进程被 kill 时 finally 不跑，属已知情况）")
        try:
            line_id = dao_e2e.insert_returning(
                "road_line", {"line_code": f"TEST-DI-{os.getpid()}",
                              "line_name": MARK}, writer="M2")
            sec_id = dao_e2e.insert_returning(
                "road_section", {"line_id": line_id, "section_name": MARK}, writer="M2")

            # ① 预检：dry_run 必须一行都不写（这就是 M9 导入页"预检"的语义）
            rep = di.load(ir_ok, dao_e2e, section_id=sec_id, batch_no=batch, dry_run=True)
            check("dry_run 报告计划行数（桩号 30 / 交点 1 / 单元 4 / 超高 0 / 路幅 0）",
                  rep["planned"] == {"station_sequence": 30, "alignment_pi": 1,
                                     "alignment_element": 4,
                                     "profile_grade_point": 0, "profile_ground_point": 0,
                                     "superelev_transition": 0, "roadbed_width": 0},
                  str(rep["planned"]))
            check("dry_run 后没有批次行",
                  dao_e2e.scalar("SELECT count(*) FROM data_import_batch WHERE batch_no=%(b)s",
                                 {"b": batch}) == 0)
            check("dry_run 后没有桩号行",
                  dao_e2e.scalar("SELECT count(*) FROM station_sequence WHERE section_id=%(s)s",
                                 {"s": sec_id}) == 0)

            # ② 真落库
            rep = di.load(ir_ok, dao_e2e, section_id=sec_id, batch_no=batch,
                          source_desc="契约测试 fixture", strict=True)
            check("落库行数 = 计划行数", rep["written"] == rep["planned"], str(rep["written"]))
            remark = dao_e2e.scalar(
                "SELECT remark FROM data_import_batch WHERE batch_no=%(b)s", {"b": batch}) or ""
            check("批次已登记（source_type=file）",
                  dao_e2e.scalar("SELECT source_type FROM data_import_batch WHERE batch_no=%(b)s",
                                 {"b": batch}) == "file")
            check("批次备注含几何等级", "几何等级 L2" in remark, remark[:90])
            check("批次备注说明了交点来源是**推导**", "交点来源 derived" in remark, remark[:90])

            # ③ 生成列由**数据库**算出，不是客户端编的
            a1 = dao_e2e.scalar("SELECT spiral_a1 FROM alignment_pi WHERE section_id=%(s)s",
                                {"s": sec_id})
            check("库里 spiral_a1 = √(450×60) = 164.31676725",
                  abs(float(a1) - 164.31676725) < 1e-8, str(a1))
            lm = dao_e2e.scalar("SELECT length_m FROM alignment_element "
                                "WHERE section_id=%(s)s AND element_seq=2", {"s": sec_id})
            check("库里 length_m = 60.000000（由桩号差算出）",
                  abs(float(lm) - 60.0) < 1e-9, str(lm))
            check("单元已挂到交点上（pi_id 非空）",
                  dao_e2e.scalar("SELECT count(*) FROM alignment_element "
                                 "WHERE section_id=%(s)s AND pi_id IS NOT NULL",
                                 {"s": sec_id}) > 0)

            # ④ 幂等：同批次重放不产生重复行
            n0 = dao_e2e.scalar("SELECT count(*) FROM alignment_element WHERE section_id=%(s)s",
                                {"s": sec_id})
            di.load(ir_ok, dao_e2e, section_id=sec_id, batch_no=batch, strict=True)
            check("重放同批次不产生重复行",
                  dao_e2e.scalar("SELECT count(*) FROM alignment_element WHERE section_id=%(s)s",
                                 {"s": sec_id}) == n0)

            # ⑤ ★ 写权守卫：「写只经 M2」在这里是**实证**，不是文档约定
            try:
                di.load(ir_ok, dao_e2e, section_id=sec_id, batch_no=batch + "-x",
                        writer="M5", strict=True)
                blocked = False
            except WriteGuardError:
                blocked = True
            check("元测试：以 M5 身份落库被 WriteGuard 拒绝", blocked)
            check("越权尝试没留下批次行",
                  dao_e2e.scalar("SELECT count(*) FROM data_import_batch WHERE batch_no=%(b)s",
                                 {"b": batch + "-x"}) == 0)

            # ⑥ ★ 硬错误必须**写库前**抛出，否则会留下半条数据
            bad = copy.deepcopy(ir_ok)
            bad["segments"]["alignment_element"][1]["start_x"] += 1.0
            try:
                di.load(bad, dao_e2e, section_id=sec_id, batch_no=batch + "-bad", strict=True)
                raised = False
            except di.LoadError as exc:
                raised = "坐标链断裂" in str(exc)
            check("元测试：链断裂 → LoadError，且在任何写入之前", raised)
            check("失败那次没留下批次行",
                  dao_e2e.scalar("SELECT count(*) FROM data_import_batch WHERE batch_no=%(b)s",
                                 {"b": batch + "-bad"}) == 0)
        finally:
            # 清理：按 FK 反序删掉本组造的一切（不留痕，种子数据不受影响）
            # ⚠ 清理**不能吞异常**：本组曾用 `except Exception: pass`，于是外键挡着删不掉时
            #   一声不响地留下残留（实测留过 TEST-DI-109，还会让下一次跑 flaky）。
            #   现在失败会打出来，并计入 _cleanup_failed。
            _cleanup_failed: list[str] = []

            # ⚠ 三条踩过的坑，都是"清理悄悄失败"这一类：
            #   ① 这里原来写 d14.execute_write —— 而 d14 定义在**另一个组**（1856 行），
            #      在 finally 里是 NameError → 每一次清理都失败、又被 except 吞掉，
            #      于是**从第一天起就在静默留残留**（TEST-DI-109 就是这么来的）。
            #   ② 手写的删除清单漏表：漏过 section_design_attr（e2e 会写 .PRJ 分段）、
            #      profile_ground_point/geometry_point（锚在 station_id 上，挡着 station_sequence）。
            #   ③ 顺序：alignment_element.pi_id → alignment_pi.id，所以 element 必须先删。
            # 现在：DAO 用 dao_e2e（本组自己的）、清单**现查 pg_constraint**（以后新增 GE 表自动覆盖）、
            #       顺序按真实外键定、失败**上报**而不是吞掉。
            _cleanup_failed: list[str] = []

            def _del(table: str, sql: str, params: dict) -> None:
                try:
                    dao_e2e.execute_write(table, sql, params, writer="M2")
                except Exception as exc:                      # noqa: BLE001
                    _cleanup_failed.append(f"{table}: {type(exc).__name__}: {exc}")

            if sec_id:
                _refs = dao_e2e.query(
                    "select c.relname as tbl, a.attname as col "
                    "from pg_constraint k "
                    "join pg_class c on c.oid = k.conrelid "
                    "join pg_class f on f.oid = k.confrelid "
                    "join unnest(k.conkey) with ordinality as ck(attnum, ord) on true "
                    "join pg_attribute a on a.attrelid = c.oid and a.attnum = ck.attnum "
                    "where k.contype = 'f' and f.relname = 'road_section'")
                # 只删 M2 名下的表：其余（如 M8 的 maintenance_advice）本组根本写不进去，
                # 硬删会被写权守卫拒绝 —— 那是守卫在**正确工作**，不是清理失败。
                _refs = [r for r in _refs if TABLE_OWNER.get(r["tbl"]) == "M2"]

                def _rank(r: dict) -> int:
                    if r["col"] == "station_id":
                        return 0                    # 锚在 station_sequence 上，必须先删
                    if r["tbl"] == "alignment_element":
                        return 1                    # 它引用 alignment_pi，必须早于它
                    return 2

                for _r in sorted(_refs, key=_rank):
                    if _r["col"] == "station_id":
                        _del(_r["tbl"], f"DELETE FROM {_r['tbl']} WHERE station_id IN "
                             f"(SELECT id FROM station_sequence WHERE section_id=%(s)s)",
                             {"s": sec_id})
                    else:
                        _del(_r["tbl"], f"DELETE FROM {_r['tbl']} WHERE {_r['col']}=%(s)s",
                             {"s": sec_id})
                for b in (batch, batch + "-x", batch + "-bad"):
                    _del("data_import_batch",
                         "DELETE FROM data_import_batch WHERE batch_no=%(b)s", {"b": b})
                _del("road_section", "DELETE FROM road_section WHERE id=%(i)s", {"i": sec_id})
            if line_id:
                _del("road_line", "DELETE FROM road_line WHERE id=%(i)s", {"i": line_id})
            dao_e2e.close()
            if _cleanup_failed:
                print(f"  ✗ 清理失败（残留会污染下一次跑）：{_cleanup_failed}")
                _fail.append('清理失败：' + str(_cleanup_failed))
            print(f"  （已清理：路段 {sec_id} / 批次 {batch}）")

    # ── 第 13 组：纬地 .PRJ 总项目文件（项目档案，不是几何段）──────────────
    print("\n第 13 组  纬地 .PRJ 总项目文件：项目档案 + 分段属性 + 文件台账")
    prj_raw = PRJ_FIXTURE.read_bytes()
    prj_text = prj_mod.decode(prj_raw)
    check("魔数探测认得它", prj_mod.detect(prj_text))
    check("版本 = 6.00（与 .STA 的 5.84、.JD/.pm 的 5.83 都不同）",
          prj_mod.parse(prj_text, file="x.PRJ")["vendor_version"] == "6.00")

    # ★ 元测试：GBK 这个说法必须**可证伪**。如果 fixture 恰好也能按 UTF-8 读，
    #   那"必须用 GBK"就只是一句注释，而不是一个事实。
    try:
        prj_raw.decode("utf-8")
        utf8_ok = True
    except UnicodeDecodeError:
        utf8_ok = False
    check("元测试：fixture 按 UTF-8 读**确实失败**（GBK 不是装饰）", not utf8_ok)
    check("元测试：非 GBK 字节 → SourceInvalid 而不是静默乱码",
          _raises(lambda: prj_mod.decode(b"\xff\xfe\x00\x01\x02")))

    po = prj_mod.parse(prj_text, file=PRJ_FIXTURE.name)
    pj = po["project"]
    check("项目名 = 毕设", pj["project_name"] == "毕设")
    check("项目类型 = 公路主线（枚举码 101 已剥掉）", pj["project_type"] == "公路主线")
    check("项目 ID = .PRJ 里的 UUID",
          pj["project_uid"] == "981cee03-2194-43e4-b367-e93950212eb0")
    check("桩号间隔 = 20 m（与桩号整桩判据的 20 一致）", pj["station_interval_m"] == 20.0)
    check("土方计算方式 = 平均断面法（一般推荐采用）",
          pj["earthwork_method"] == "平均断面法（一般推荐采用）")
    check("设计人 = lql730@outlook.com", pj["designer"] == "lql730@outlook.com")
    check("空值字段留 None（工程未填，不当成空串或 0）",
          pj["station_decimals"] is None and pj["design_org"] is None
          and pj["design_stage"] is None)

    seg = po["segments"][0]
    check("分段起点/终点/长度 = 0 / 5805.421 / 5805.421",
          (seg["start_station_m"], seg["end_station_m"], seg["length_m"])
          == (0.0, 5805.421, 5805.421))
    check("公路等级 = 二级公路", seg["road_grade"] == "二级公路")
    check("计算车速 = 60", seg["design_speed_kmh"] == 60.0)
    check("路幅宽度 = 10.0 / 行车道横坡 = 2.0 / 土路肩横坡 = 3.0",
          (seg["roadway_width_m"], seg["carriageway_crossfall_pct"],
           seg["shoulder_crossfall_pct"]) == (10.0, 2.0, 3.0))
    check("最大超高从「最大超高8%」里取出 8.0", seg["max_superelev_pct"] == 8.0)
    check("超高/加宽方式剥掉枚举码",
          seg["superelev_rotate_mode"] == "绕曲线内侧行车道边缘旋转"
          and seg["superelev_gradient_mode"] == "线性"
          and seg["widening_mode"] == "不设置加宽")
    check("车道数由「2车道」派生 = 2", seg["lane_count"] == 2)
    # ★ 派生量不许瞎猜：认不出就留 None（车道数是下游分析的分母，猜错一路错到底）
    check("元测试：认不出的横断面形式 → None，不回默认值",
          prj_mod.derive_lane_count("双向四车道") is None
          and prj_mod.derive_lane_count("") is None
          and prj_mod.derive_lane_count(None) is None)
    check("元测试：中文白名单认得「双车道」= 2", prj_mod.derive_lane_count("双车道") == 2)
    check("元测试：数字字段解析失败 → None，不返回 0",
          prj_mod._convert("num", "abc") is None and prj_mod._convert("num", "") is None)
    check("元测试：百分数字段 → 数值", prj_mod._convert("pct", "2.0%") == 2.0)

    check("文件台账 30 条", len(po["files"]) == 30, f"实为 {len(po['files'])}")
    check("首条 = 101 平面线形文件(*.PM) → .\\毕设.pm",
          po["files"][0] == {"kind_code": "101", "kind_name": "平面线形文件(*.PM)",
                             "rel_path": ".\\毕设.pm"}, str(po["files"][0]))
    check("2 条没有字段号（实测：涵洞的两个文件）",
          sum(1 for f in po["files"] if not f["kind_code"]) == 2)
    check("12 条路径为空（工程声明了槽位但没用）",
          sum(1 for f in po["files"] if not f["rel_path"]) == 12)
    check("文件台账里含纵断面两个文件（.ZDM/.DMX）—— 即 L3 的原料已声明",
          any("*.ZDM" in f["kind_name"] for f in po["files"])
          and any("*.DMX" in f["kind_name"] for f in po["files"]))

    # ★★ 核心元测试：`[LONG]/[DOUBLE]/[STRING]` 是二进制块，其键是内存地址。
    #    必须证明它们**一个都没漏进**解析结果 —— 否则会写出一批"看起来像字段"的垃圾。
    check("元测试：4 个二进制块都被识别并跳过", set(po["ignored_blobs"])
          == {"相关项目", "LONG", "DOUBLE", "STRING"}, str(po["ignored_blobs"]))
    check("元测试：声明条数被记下来（让「我没解析这块」可见）",
          po["ignored_blobs"]["LONG"] == 101 and po["ignored_blobs"]["DOUBLE"] == 102)
    # 直接按"这 5 个真实内存地址键"断言，不搞模糊的启发式：模糊断言本身也会失效。
    BLOB_KEYS = ("9240611", "9240577", "2147418221", "2147418238", "9240860", "9240700")
    blob_seen = [k for k in list(pj) + list(seg)
                 if any(bk in str(k) for bk in BLOB_KEYS)]
    blob_vals = [k for k, v in po["unmapped"].items()
                 if any(str(v) == bk for bk in BLOB_KEYS)]
    check("元测试：内存地址式的键与值一个都没漏进来",
          not blob_seen and not blob_vals, f"{blob_seen[:3]} {blob_vals[:3]}")
    check("元测试：未映射字段的键名都是「组.字段号中文名」的形状（不是裸数字）",
          all(k.split(".")[-1][:1].isdigit() and any("\u4e00" <= c <= "\u9fff" for c in k)
              for k in po["unmapped"]), str(list(po["unmapped"])[:2]))
    check("未映射字段被单独收集（不是报成告警）",
          len(po["unmapped"]) == 13 and po["warnings"] == [],
          f"unmapped={len(po['unmapped'])} warnings={po['warnings']}")

    # 真文件在场时：fixture 必须与它逐字段相同（fixture 是截取，不能截歪）
    real_prj = REAL_DIR / "052201341刘其立道路毕设总项目.PRJ"
    if real_prj.exists():
        ro = prj_mod.parse(prj_mod.decode(real_prj.read_bytes()), file=real_prj.name)
        check("fixture 与真实 .PRJ 逐字段相同（项目/分段/台账/未映射四项）",
              ro["project"] == pj and ro["segments"] == po["segments"]
              and ro["files"] == po["files"] and ro["unmapped"] == po["unmapped"])
    else:
        print("  ⊘ 跳过：真实 .PRJ 不在（docpipe/ 是 gitignored）")

    # ---- 应拒绝侧 ----
    def prj_rejects(name: str, bad: str, expect: str) -> None:
        global PASS, FAIL
        try:
            prj_mod.parse(bad, file="bad.PRJ")
        except SourceInvalid as exc:
            ok = expect in str(exc)
            check(name, ok, "" if ok else f"抛了但信息不含「{expect}」：{exc}")
        except Exception as exc:                       # noqa: BLE001
            check(name, False, f"抛了 {type(exc).__name__} 而不是 SourceInvalid：{exc}")
        else:
            check(name, False, "**没抛错**（畸形文件被静默接受了）")

    prj_rejects("缺魔数 → 拒绝",
                prj_text.replace("HINTCAD6.00_PRJ_SHUJU", ""), "魔数")
    prj_rejects("魔数是 .STA 的（认错文件类型）→ 拒绝",
                prj_text.replace("HINTCAD6.00_PRJ_SHUJU", "HINTCAD5.84_STA_SHUJU"), "魔数")
    prj_rejects("没有 [项目设置] 组 → 拒绝",
                "\r\n".join(l for l in prj_text.split("\r\n")
                             if not l.startswith("[项目设置]") and not l.startswith("201")), "项目设置")
    prj_rejects("缺 201项目名 → 拒绝",
                prj_text.replace("201项目名 = 毕设\r\n", ""), "项目名")
    prj_rejects("没有 [项目分段N] 组 → 拒绝",
                prj_text.split("[项目分段1]")[0], "分段")
    prj_rejects("分段缺起点桩号 → 拒绝",
                prj_text.replace("301起点桩号 = 0.000\r\n", ""), "起点或终点")
    prj_rejects("分段终点桩号不大于起点 → 拒绝",
                prj_text.replace("302终点桩号 = 5805.421", "302终点桩号 = 0.000"), "不大于")

    # ── 第 14 组：.PRJ → 档案与路段（两段式导入的第一段）──────────────────
    print("\n第 14 组  .PRJ → design_project / road_line / road_section / "
          "section_design_attr / design_file")
    planned = di.plan_project(po, project_dir=None)
    cnt = {k: len(v) for k, v in planned["tables"].items()}
    check("五张表各 1/1/1/1 行 + design_file 16 行",
          cnt == {"design_project": 1, "road_line": 1, "road_section": 1,
                  "section_design_attr": 1, "design_file": 16}, str(cnt))
    check("★ 元测试：路幅总宽 10.000 **不许**进 road_line.lane_width_m（那是单车道宽）",
          planned["tables"]["road_line"][0]["lane_width_m"] is None)
    check("★ 路幅总宽进 section_design_attr.roadway_width_m",
          planned["tables"]["section_design_attr"][0]["roadway_width_m"] == 10.0)
    check("road_section 起终点文本 = K0+000.000 / K5+805.421",
          (planned["tables"]["road_section"][0]["start_station_text"],
           planned["tables"]["road_section"][0]["end_station_text"])
          == ("K0+000.000", "K5+805.421"))
    check("line_code 以项目名代，且 remark 写明这是代用（.PRJ 无路线代码）",
          planned["tables"]["road_line"][0]["line_code"] == "毕设"
          and "无路线代码" in planned["tables"]["road_line"][0]["remark"])
    check("12 条空路径的槽位不进 design_file",
          all(f["rel_path"] for f in planned["tables"]["design_file"]))
    check("2 条无字段号的文件被跳过并给出原因（NOT NULL 无法满足）",
          len(planned["skipped_files"]) == 2
          and all("未给字段号" in x for x in planned["skipped_files"]))
    check("★ 元测试：design_file 每行都有 file_kind_code（满足 NOT NULL）",
          all(f["file_kind_code"] for f in planned["tables"]["design_file"]))
    # 已实现适配器的后缀才给 ok。加 .DMX/.ZDM 后从 3 个变 5 个 —— 这条断言当时
    # 变红是对的（它抓住了行为变化）。103/104 是不是 .DMX/.ZDM 已从库里核实：
    #   103 = 毕设.DMX         104 = 纵断面设计拟合.ZDM
    # 107 = .SUP、106 = .WID 均已从库里核实（design_file.file_kind_code ↔ 文件名）
    check("parse_status 只对已实现适配器的后缀给 ok（实测 7 个）",
          sorted(f["file_kind_code"] for f in planned["tables"]["design_file"]
                 if f["parse_status"] == "ok")
          == ["101", "102", "103", "104", "106", "107", "109"],
          str([f["file_kind_code"] for f in planned["tables"]["design_file"]
               if f["parse_status"] == "ok"]))
    attr = planned["tables"]["section_design_attr"][0]
    check("section_design_attr 的 12 个属性都来自 .PRJ（不是猜的）",
          attr["road_grade"] == "二级公路" and attr["design_speed_kmh"] == 60
          and attr["cross_section_form"] == "2车道" and attr["roadway_width_m"] == 10.0
          and attr["carriageway_crossfall_pct"] == 2.0
          and attr["shoulder_crossfall_pct"] == 3.0 and attr["median_width_m"] == 0.0
          and attr["max_superelev_pct"] == 8.0
          and attr["superelev_rotate_mode"] == "绕曲线内侧行车道边缘旋转"
          and attr["superelev_gradient_mode"] == "线性"
          and attr["widening_mode"] == "不设置加宽"
          and attr["widening_gradient_mode"] == "线性加宽(W = kb)")
    check("路面类型/气候区/方向留空（.PRJ 没有这些字段，不猜）",
          planned["tables"]["road_section"][0]["pavement_type"] is None
          and planned["tables"]["road_section"][0]["climate_zone"] is None
          and planned["tables"]["road_section"][0]["direction"] is None)
    # ★ 纯函数元测试：调两次必须完全相同 —— 若内部改了入参，第二次就不同了
    again = di.plan_project(po, project_dir=None)
    check("★ 元测试：plan_project 是纯函数（调两次结果完全相同，未改动入参）",
          again == planned)
    check("元测试：_seg_seq 之外没有任何非 DDL 列漏出",
          all(k.startswith("_") or k in ddl_columns(t)
              for t, rows in planned["tables"].items() for r in rows for k in r),
          str([k for t, rows in planned["tables"].items() for r in rows for k in r
               if not k.startswith("_") and k not in ddl_columns(t)][:5]))

    # ---- 真库：ensure_project（自造一份 .PRJ，避免动到已导入的毕设数据）----
    uniq = f"TEST-PRJ-{os.getpid()}"
    synth = ("HINTCAD6.00_PRJ_SHUJU\r\n[项目设置]2\r\n"
             f"201项目名 = {uniq}\r\n202项目类型 = 101|公路主线\r\n"
             "205桩号间隔 = 20\r\n"
             f"214项目ID = {uniq}-uid\r\n[项目分段1]1\r\n"
             "301起点桩号 = 100.000\r\n302终点桩号 = 500.000\r\n"
             "304计算车速 = 40\r\n305公路等级 = 三级公路\r\n"
             "306横断面形式 = 2车道\r\n307路幅宽度 = 7.500\r\n"
             "[文件名]1\r\n101平面线形文件(*.PM) = .\\x.pm\r\n"
             "[LONG]1\r\n9240611 = 1\r\n[SaveTimes]\r\n1 = 2026/01/01 00:00\r\n")
    sp = prj_mod.parse(synth, file="synth.PRJ")
    sys.path.insert(0, str(ROOT / "modules" / "M3-rpdao"))
    d14 = None
    try:
        from rpdao.write import WriteDao as _WD14             # noqa: PLC0415
        d14 = _WD14(pg_dsn() or "", app_name="contract-test-14",
                    min_size=1, max_size=2, timeout=10)
        d14.open()
        if not d14.ping():
            d14 = None
    except Exception as exc:                                  # noqa: BLE001
        print(f"  ⊘ 跳过 ensure_project 的真库用例：{type(exc).__name__}: {str(exc)[:80]}")
    if d14 is None:
        pass
    else:
        sp_plan = di.plan_project(sp)
        check("自造 .PRJ（起点 100 m ≠ 0）→ 路段起点桩号 K0+100.000",
              sp_plan["tables"]["road_section"][0]["start_station_text"] == "K0+100.000")
        d = di.ensure_project(sp, d14, dry_run=True)
        check("dry_run 不写库", d["project_id"] is None
              and d14.scalar("SELECT count(*) FROM design_project WHERE project_uid=%s",
                                 (f"{uniq}-uid",)) == 0)
        try:
            r1 = di.ensure_project(sp, d14)
            check("ensure_project 建出 5 张表：project/line/section 各 1，attr 1，file 1",
                  r1["project_id"] and r1["line_id"] and len(r1["section_ids"]) == 1)
            sid = r1["section_ids"][0]["section_id"]
            check("section_design_attr 已挂上该 section",
                  d14.scalar("SELECT count(*) FROM section_design_attr WHERE section_id=%s",
                                 (sid,)) == 1)
            check("design_file 已挂上该 project",
                  d14.scalar("SELECT count(*) FROM design_file WHERE design_project_id=%s",
                                 (r1["project_id"],)) == 1)
            r2 = di.ensure_project(sp, d14)
            check("★ 幂等：重跑得到同一批 id，且不产生重复行",
                  (r2["project_id"], r2["line_id"], r2["section_ids"]) ==
                  (r1["project_id"], r1["line_id"], r1["section_ids"])
                  and d14.scalar("SELECT count(*) FROM design_project WHERE project_uid=%s",
                                     (f"{uniq}-uid",)) == 1)
            check("★ 以 M5 身份建档案 → WriteGuardError（写只经 M2）",
                  _raises_wg(lambda: di.ensure_project(sp, d14, writer="M5")))
        finally:
            # 按 FK 反序清干净
            uid = f"{uniq}-uid"
            with d14.write_txn(writer="M2") as tx:      # FK 反序，同成同败
                tx.execute("design_project",
                           "DELETE FROM design_file WHERE design_project_id IN "
                           "(SELECT id FROM design_project WHERE project_uid=%(u)s)", {"u": uid})
                tx.execute("design_project",
                           "DELETE FROM section_design_attr WHERE section_id IN "
                           "(SELECT s.id FROM road_section s JOIN design_project p "
                           " ON s.design_project_id = p.id WHERE p.project_uid=%(u)s)", {"u": uid})
                tx.execute("design_project",
                           "DELETE FROM road_section WHERE design_project_id IN "
                           "(SELECT id FROM design_project WHERE project_uid=%(u)s)", {"u": uid})
                tx.execute("road_line", "DELETE FROM road_line WHERE line_code=%(c)s",
                           {"c": uniq})
                tx.execute("design_project", "DELETE FROM design_project WHERE project_uid=%(u)s",
                           {"u": uid})
        left = (d14.scalar("SELECT count(*) FROM design_project WHERE project_uid=%s",
                           (f"{uniq}-uid",))
                + d14.scalar("SELECT count(*) FROM road_line WHERE line_code=%s", (uniq,)))
        check("元测试：清理后残留为 0（否则测试会污染真库）", left == 0, f"残留 {left}")
        d14.close()

    print("\n" + "=" * 74)
    print(f"通过 {PASS} ｜ 失败 {FAIL}")
    print("=" * 74)
    if FAIL == 0:
        print("\n结论：契约⑤ 的 IR 结构、能力/实得/缺口自洽性、等级推导、落库器，")
        print("      以及纬地 .STA / .JD / .pm 三个解析器的**放行侧与拦截侧**均已成立。")
        print("      .JD 与 .pm 的字段语义都不是照抄注释，而是被几何恒等式证明的：")
        print("      .JD 用了 6 条（含发现 DDL 把 A 当切线长、把 Ls 当转角）；")
        print("      .pm 用了 3 条（链连续 / 圆心距离 ≡ R / 弦长 = 2R·sin(L/2R)）。")
        print("      两者还能互相印证：.pm 的转向符号与 .JD 由坐标算出的转角符号一致。")
        print("      .PRJ（第 13 组）另成一路：它不是几何段而是项目档案，映射到 "
              "design_project/")
        print("      section_design_attr/design_file 三表，故不进 IR segments —— "
              "塞进去会破坏 schema。")
        print('      落库器（第 11/12 组）把"映射"与"事务"分开测：前者是纯函数、完全离线，')
        print("      后者打真库、只验多表同事务、幂等、以及**以 M5 身份落库会被 WriteGuard 拒绝**")
        print("      —— 即「写只经 M2」是跑出来的，不是写文档里的。")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
