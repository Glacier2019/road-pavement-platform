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
from pathlib import Path
import re
import shutil
import sys
import tempfile
from decimal import Decimal
from decimal import ROUND_HALF_UP, Decimal

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "modules" / "M2-ingest"))

import design_import as di                              # noqa: E402
from adapters import base, detect_vendor, geom, weidi          # noqa: E402
from adapters.errors import SourceInvalid                # noqa: E402
from adapters.weidi import (ctr, dmx, jd, lj, pm, prj as prj_mod, sta, sup, tf,
                              tsf as tsf_mod, tsfborrow as tsf_borrow_mod,
                              tsfspoil as tsf_spoil_mod,
                              tsfhaul as tsf_haul_mod,
                              tsffill as tsf_fill_mod,
                              tsftransfer as tsf_transfer_mod,
                              wid, zdm)  # noqa: E402

PRJ_FIXTURE = ROOT / "tests" / "fixtures" / "design_import" / "weidi_prj_excerpt.PRJ"
IR_SCHEMA_PATH = ROOT / "contracts" / "design-import" / "road_geometry_ir.v0.3.schema.json"
DDL_PATH = ROOT / "sql" / "10_ddl_v0.5.sql"


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
# ★ 用**列表**而不是整数：三处 ⊘ 跳过里有一处在函数内部，`SKIP += 1` 会把它
#   变成那个函数的**局部变量**（UnboundLocalError: referenced before assignment）。
#   改用可变对象就不必逐个函数加 `global` —— 这个坑我在同一次改动里就踩到了。
SKIP = [0]    # 跳过（环境不具备，不是通过）—— 必须在汇总里显形，否则会读成"全绿"


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
    # ⚠ 末点竖曲线半径原写 6000 —— **不合规**。教程 §13.3：「竖曲线的半径
    #   （第一个变坡点和最后一个变坡点**只能为 0**）」（竖曲线不能伸出路线之外）。
    #   真文件两端也都是 0（行 3 与行 14 的第三项均为 0.00000000）。
    #   故改成 0 —— 是**修一个手搓错的 fixture**，不是迁就代码。
    _row2 = "       300.000\t58.82200000\t0.00000000\t     0.000\t0.00000000\r\n"
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
    # R>0 但缺一侧纵坡 → **算不出来**，必须是 None 并告警。
    # 不能写 0 —— 0 会被下游当成"这条竖曲线长 0 米"，而真相是"无法确定"。
    #
    # ⚠ 这条分支现在**解析器送不进来**：缺一侧纵坡只可能发生在端点，而教程
    #   §13.3 规定端点半径**只能为 0**，parse() 已据此拒绝。所以这里**手工造点**
    #   直接调 derive_grades —— 测的是**库级**的防御（有人绕过 parse 直接调用时
    #   仍不能把"算不出来"写成 0），不是解析路径。
    _z3, _w3 = zdm.derive_grades([
        {"vpi_seq": 1, "station_m": 0.0, "elevation_m": 57.262, "vertical_curve_radius_m": 0.0},
        {"vpi_seq": 2, "station_m": 300.0, "elevation_m": 58.822, "vertical_curve_radius_m": 6000.0},
    ])
    check("派生：末点 R>0 却缺一侧纵坡 → 竖曲线长 None（不是 0）且必须告警",
          _z3[-1]["grade_len_m"] is None and any("竖曲线长无法推得" in w for w in _w3),
          f"{_z3[-1]['grade_len_m']} / {_w3}")
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

    # ★ 第 4/5 列**不再是"含义未知"** —— 教程 §13.3 写明了它们是标高错台：
    #   「最后两项数据是针对互通立交匝道上出现标高错台现象而设置的，分别表示
    #     标高错台位置的桩号及错台的标高差值（向上错开输正值，向下为负值，
    #     单位为米）。如果没有错台现象或当前项目为一般公路主线时，这两项数据
    #     同时输为 0 即可。」
    #   本工程是一般公路主线 → 全 0 **是正常值**，原来那句"含义未知"的告警
    #   是把正常当异常报，已删。
    check("★ 错台两列解析出来了（教程 §13.3 的第 4/5 项）",
          all("offset_station_m" in p and "offset_elev_m" in p for p in _z["points"]))
    check("★ 本工程是一般公路主线 → 错台两列**全为 0**（正是教程说的情形）",
          all(p["offset_station_m"] == 0.0 and p["offset_elev_m"] == 0.0
              for p in _z["points"]))
    check("★ 全 0 不再产生告警（正常值不该被当异常报）",
          not any("offset" in w for w in _w2), str(_w2))

    # ★ 新不变量：教程 §13.3「竖曲线的半径（第一个变坡点和最后一个变坡点
    #   只能为 0）」—— 竖曲线不能伸出路线之外。
    check_raises("应拒绝：首个变坡点的竖曲线半径非 0（教程 §13.3 规定只能为 0）",
                 _zh + "          2\r\n"
                 + "     0.000\t57.26200000\t100.00000000\t0.000\t0.00000000\r\n"
                 + "   300.000\t58.82200000\t0.00000000\t0.000\t0.00000000\r\n",
                 parser=zdm, expect="竖曲线半径应为 0")
    check_raises("应拒绝：错台两列只有一项为 0（半截错台数据无法解释）",
                 _zh + "          2\r\n"
                 + "     0.000\t57.26200000\t0.00000000\t0.000\t0.00000000\r\n"
                 + "   300.000\t58.82200000\t0.00000000\t123.000\t0.00000000\r\n",
                 parser=zdm, expect="只有一项为 0")
    check_raises("应拒绝：错台标高差超出合理区间（疑似列错位）",
                 _zh + "          2\r\n"
                 + "     0.000\t57.26200000\t0.00000000\t0.000\t0.00000000\r\n"
                 + "   300.000\t58.82200000\t0.00000000\t123.000\t9.50000000\r\n",
                 parser=zdm, expect="超出合理区间")
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
        # 第 5 次变红：加 .tf（土方断面）与 .lj（路基设计断面）时又红了。
        # 答案**仍然是"等级不该动"**，而且理由更强：这两张表是**逐桩的设计细节**，
        # 连"设计线"都不是 —— 它们是从设计线派生出来的土方量与路幅断面。
        # 一个"加了段就要改等级"的测试才是坏的：等级的定义是平的，实现进度不该动它。
        # ★★ 第 6 次变红 —— 但**与前 5 次性质相反**，所以答案也相反。
        #
        # 前 5 次（加 .CTR / .SUP / .WID / .tf / .lj）红的时候，答案都是"等级不该动"：
        #   那些段是**逐桩的设计细节**（超高/宽度/土方/路幅断面），
        #   它们**不在** _LEVEL_RULES 的任何一条里 —— 实现了也不改变"平纵横齐不齐"。
        #   所以那时改等级才是错的：等级的定义是平的，实现进度不该动它。
        #
        # 这次（加 .HDM 横断面地面线）**反过来**：
        #   `cross_section` **就是 L4 的定义本身** —— _LEVEL_RULES 里写着
        #   ("L4", ("cross_section",), "any")。它一直没实现，所以本工程一直封顶在 L3，
        #   base.py 那句注释也一直写着「L4 …当前数据源拿不到，故本工程最高到 L3」。
        #   .HDM 适配器落地后，L4 的**必要条件**第一次被满足 —— 等级**该动**。
        #
        # 判据（写给下一次红的人）：**看那个段在不在 _LEVEL_RULES 里**。
        #   在  → 等级该动，改这条断言，并说明是定义内的段被实现了；
        #   不在 → 等级不该动，是"顺手加了段"，改断言等于把测试改坏。
        # ★★ 第 7 次变红：加 .tsf（土石方调配）时又红了。按上面那条判据走 ——
        #    `earthwork_factor` **不在** _LEVEL_RULES 的任何一条里（L0–L4 只认平/纵/横），
        #    所以答案和加 .CTR/.SUP/.WID/.tf/.lj 那五次一样：**等级不该动**。
        #    等级的定义是平的，实现进度不该动它 —— 改的是下面这份**已实现清单**。
        check("★ 等级 = L4（cross_section 就是 L4 的定义，.HDM 落地后第一次满足）",
              full["geometry_level"] == "L4"
              and sorted(weidi.IMPLEMENTED)
              == ["alignment_element", "alignment_pi", "borrow_pit", "cross_section",
                  "design_control",
                  "earthwork_factor", "earthwork_fill_stat", "earthwork_haul_stat",
                  "earthwork_section", "earthwork_transfer", "profile_grade_point",
                  "profile_ground_point", "roadbed_design_point", "roadbed_width",
                  "spoil_pit",
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
                  "厂商版本 5.83",   # ← .HDM 横断面地面线（v0.5 K 节）
                  "厂商版本 5.84",   # ← .STA
                  "厂商版本 6.00", "厂商版本 6.00",
                  "厂商版本 6.00",   # ← .tsftxt 土石方调配（L 节）
                  "厂商版本 6.00",   # ← .tsftxt 调配过程（M 节）
                  "厂商版本 6.00",   # ← .tsftxt 取土坑（N 节）
                  "厂商版本 6.00",   # ← .tsftxt 弃土坑（N 节）
                  "厂商版本 6.00",   # ← .tsftxt 统计扩展（O 节）
                  "厂商版本 6.00",   # ← .tsftxt 土方调配扩展记录（O 节）
                  "厂商版本 7.0"],   # ← .tf 土石方量
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
        # ★ v0.5 K 节起缺口从 2 项降到 1 项：cross_section 的 .HDM 适配器做完了。
        #   剩下 geometry_point 一项，且它的原因**不是**"没做适配器"——
        #   见下面那条（source_absent：.3DR 源文件本工程根本没生成）。
        check("缺口只剩 1 项（平纵横全齐，只剩一个无源的 .3DR）",
              sorted(x["segment"] for x in full["gaps"]) == ["geometry_point"],
              str([x["segment"] for x in full["gaps"]]))
        # ★★ 缺口必须说**真正**的原因，不能只报"适配器没做"。这条判据在
        #    cross_section 被关掉**之后**反而更重要了：原先两项一对比就看得出区别
        #    （一个有源、一个无源），现在只剩一项，**没有对照物了** ——
        #    如果哪天有人把 reason 统一写成 not_supported，光看这一项看不出错。
        #    所以这里显式钉死：唯一剩下的这项必须是 source_absent。
        #    geometry_point 的源（.3DR 横断面三维数据文件）本工程**没生成** ——
        #    源都不在，做不做适配器都导不出东西来。
        #    对照：cross_section 之前是 not_supported（.HDM 文件在、只是适配器没做），
        #    现在它已被实现、不再出现在 gaps 里 —— 而**正是它的存在**证明了两者含义不同。
        check("★★ 剩下的唯一缺口原因必须是 source_absent（不是 not_supported）",
              {x["segment"]: x.get("reason") for x in full["gaps"]}
              == {"geometry_point": "source_absent"},
              str({x["segment"]: x.get("reason") for x in full["gaps"]}))
        # ★ .3DR 台账必须登记且标 absent —— "这个文件不存在"正是"这段没解析出来"的答案
        _dr = [f for f in full["source"]["files"] if f["kind"] == "横断面三维数据文件"]
        check("★ 缺源也要进台账（name 允许空串，parse_status=absent）",
              len(_dr) == 1 and _dr[0]["parse_status"] == "absent" and _dr[0]["name"] == "",
              str(_dr))

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
        # 12 → 13：v0.5 L 节引入 `.tsftxt`（.tsf 经 tools/tsf2txt.py 摊出的文本）。
        # ★ 它是**另一个文件类别**，不是 .tsf 的别名 —— 两者在台账里各占一行：
        #   `.tsf`     → pending（这个二进制确实还导不进去，要先转换）
        #   `.tsftxt`  → ok     （适配器读的就是它）
        check("台账登记了 18 类文件（含未实现的）", len(full["source"]["files"]) == 18,
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

    # ── 第 6e 组：.CTR 设计参数控制解析器（应通过 / 应拒绝两侧）─────────────────
    #    .CTR 与其余 6 个适配器**结构上不同**：它是**关键字驱动**的，36 个关键字
    #    各成一类格式，一个文件带 9 张表的载荷。所以"应拒绝"一侧要覆盖两类错：
    #      ① 文件级 —— 魔数、数据行出现在关键字之前、终止符之后还有数据；
    #      ② 行级   —— 组数 × 每组项数 ≠ 实际字段数、桩号位出现 9999、列数不符。
    #    ⚠ 第二类里"组数"最要紧：组数是**第二列**，它决定后面还有多少个数。
    #      按空格切完直接当下标取，会在"组数变了"的行上**静默错位**——
    #      读出来的一组数看着都像坡度，但整体错开一位。
    print("\n第 6e 组  .CTR 设计参数控制解析器（应通过 / 应拒绝两侧）")
    _ch = "HINTCAD5.83_CTR_SHUJU\r\n"

    def _ctr_kw(kw, *rows):
        """拼一个「关键字行 + 若干数据行 + 空行」的块（.CTR 用空行分隔块）。"""
        return kw + "\r\n" + "".join(r + "\r\n" for r in rows) + "\r\n"

    # 正例：每类格式各来一条，覆盖 9 张表里能造出来的全部
    _c_ok = (_ch
             # 左填方边坡：桩号 100.000，2 级（坡度/控制坡高/最大坡高/砌护）
             + _ctr_kw("ZTFBP.DAT", "100.000 2  -1.500 0.000 8.000 0  0.000 0.000 1.500 0")
             # 右边沟：3 个折点 → 组数 3
             + _ctr_kw("YBGXS.DAT", "200.000 3  0.500 0.600 1  0.000 0.400 1  0.500 0.600 0")
             # 标准断面：桩号 + 9 个数值
             + _ctr_kw("ZBZDM.DAT", "300.000 0.000 2.000 0.000 3.500 2.000 0.750 2.000 0.750 3.000")
             # 路槽：桩号 + 4 个深度
             + _ctr_kw("ZLCSD.DAT", "300.000 0.000 0.150 0.150 0.200")
             # 涵洞：5 列 =「中心桩号 与路线角度 跨径说明 构造物名称 控制标高」
             # ⚠ 列序与我第一版猜的不同（名称在第 4 列，交角在第 2 列）——
             #   当时写成「桩号 名称 跨径 交角 类型」，被 _to_float("盖板涵") 当场打回。
             + _ctr_kw("HDSJ.DAT", "400.000 90.0000 1-1.500×2.000 盖板涵 52.3227")
             # 土石成份：桩号 + 6 个百分比
             + _ctr_kw("TFFD.DAT", "500.000 20 60 20 0 0 0")
             # 用地宽度：桩号 填方宽 挖方宽
             + _ctr_kw("ZYDK.DAT", "600.000 5.000 6.000")
             # 清除表土：桩号 增加宽度 厚度
             + _ctr_kw("QCHBT.DAT", "700.000 0.500 0.300")
             # 水准点：桩号 名称 高程 说明
             + _ctr_kw("SHUIZHUNDIAN.DAT", "800.000 BM1 57.262 路基顶")
             + "XXXX.DAT\r\n")
    _c = ctr.parse(_c_ok, file="ok.CTR")
    _cc = _c["control"]
    check("应通过：厂商版本从魔数取出", _c["vendor_version"] == "5.83", _c["vendor_version"])
    check("应通过：detect 认得自家魔数", ctr.detect(_c_ok) is True)
    check("应通过：detect 不认别家的魔数",
          ctr.detect("HINTCAD5.83_ZDM_SHUJU\r\n") is False)
    check("应通过：段名/文件类别/载荷键与登记一致",
          ctr.SEGMENT == "design_control"
          and weidi.SEGMENT_FILES["design_control"][0] == ".CTR"
          and weidi.SEGMENT_FILES["design_control"][1] == ctr.FILE_KIND
          and ctr.PAYLOAD_KEY == "control",
          f"{ctr.SEGMENT} / {ctr.FILE_KIND} / {ctr.PAYLOAD_KEY}")
    check("★ 应通过：9 张表各自的条数（由组数展开得出，不是行数）",
          {k: len(v) for k, v in _cc.items()}
          == {"slope_segments": 2, "ditch_segments": 3, "standard_cross_sections": 1,
              "roadbed_trenches": 1, "structures": 1, "earthwork_compositions": 1,
              "land_use_widths": 1, "extra_fills": 1, "design_control_texts": 1},
          str({k: len(v) for k, v in _cc.items()}))
    check("★ 应通过：组数展开后 group_seq 从 1 起递增（同桩号多级）",
          [r["group_seq"] for r in _cc["slope_segments"]] == [1, 2]
          and [r["group_seq"] for r in _cc["ditch_segments"]] == [1, 2, 3],
          str([r["group_seq"] for r in _cc["slope_segments"]]))
    check("应通过：边坡侧别/填挖由关键字决定（ZTFBP = 左 填）",
          all(r["side"] == "left" and r["slope_kind"] == "fill"
              for r in _cc["slope_segments"]))
    check("应通过：左填方第 1 级坡度 = -1.500，最大坡高 = 8.000",
          _cc["slope_segments"][0]["slope_ratio"] == -1.5
          and _cc["slope_segments"][0]["max_height_m"] == 8.0)
    check("应通过：跨径是 text，`1-1.500×2.000` 原样保住（不是数值）",
          _cc["structures"][0]["span_text"] == "1-1.500×2.000",
          repr(_cc["structures"][0]["span_text"]))
    check("应通过：土石成份 6 项百分比", _cc["earthwork_compositions"][0]["pct_1"] == 20.0
          and _cc["earthwork_compositions"][0]["pct_6"] == 0.0)
    check("应通过：终止符 XXXX.DAT 之后不再收数据",
          ctr.TERMINATOR == "XXXX.DAT" and len(_cc["design_control_texts"]) == 1)

    # ── 应拒绝（文件级）────────────────────────────────────────────────
    def _reject(txt, why, *, want=None):
        try:
            ctr.parse(txt, file="bad.CTR")
        except SourceInvalid as e:
            if want and want not in str(e):
                check(f"应拒绝：{why}", False, f"报错了但话不对：{e}")
                return
            check(f"应拒绝：{why}", True, f"已拒绝：{e}")
            return
        check(f"应拒绝：{why}", False, "**没有报错** —— 畸形输入被静默接受")

    _reject("", "空文件")
    _reject("随便一个文本\r\n", "非纬地文件")
    _reject("HINTCAD5.83_ZDM_SHUJU\r\n", "魔数是 .ZDM 的（张冠李戴）")
    _reject(_ch + "0.000 1  -1.500 0.000 8.000 0\r\n",
            "数据行出现在任何关键字之前", want="关键字")

    # ── 应拒绝（行级）★ 这一组才是 .CTR 真正容易读错的地方 ────────────────
    # ① 组数 × 每组 4 项 ≠ 剩余字段数：声明 2 级却只给了 1 级（少 4 个数）
    _reject(_ch + _ctr_kw("ZTFBP.DAT", "100.000 2  -1.500 0.000 8.000 0"),
            "★ 边坡：声明 2 组但只给了 1 组的数（组数 × 4 ≠ 剩余）", want="组数")
    # ② 多给：声明 1 组却给了 2 组的数
    _reject(_ch + _ctr_kw("ZTFBP.DAT",
                          "100.000 1  -1.500 0.000 8.000 0  0.000 0.000 1.500 0"),
            "★ 边坡：声明 1 组却给了 2 组的数", want="组数")
    # ③ 边沟每组 3 项，给了 4 项
    _reject(_ch + _ctr_kw("YBGXS.DAT", "200.000 1  0.500 0.600 1  9"),
            "★ 边沟：每组应为 3 项，多给了 1 项", want="组数")
    # ④ 组数为负
    _reject(_ch + _ctr_kw("YBGXS.DAT", "200.000 -1  0.500 0.600 1"),
            "边沟：组数为负", want="负")
    # ⑤ 只有桩号一列，连组数都没有
    _reject(_ch + _ctr_kw("YBGXS.DAT", "200.000"),
            "边沟：只有桩号，缺组数", want="两列")
    # ⑥ ★★ 桩号位出现 9999 —— 9999 是"忽略此数据"的哨兵，而桩号是这行的坐标
    _reject(_ch + _ctr_kw("ZTFBP.DAT", "9999.000 1  -1.500 0.000 8.000 0"),
            "★★ 桩号列出现 9999（哨兵只用于坡度/标高列）", want="9999")
    _reject(_ch + _ctr_kw("HDSJ.DAT", "9999.000 90.0 1-2.000 盖板涵 52.0"),
            "★★ 构造物：桩号列出现 9999", want="9999")
    # ⑦ 桩号为负
    _reject(_ch + _ctr_kw("ZTFBP.DAT", "-1.000 1  -1.500 0.000 8.000 0"),
            "桩号为负", want="负")
    # ⑧ 标准断面列数不符（应为「桩号 + 9」共 10 列）
    _reject(_ch + _ctr_kw("ZBZDM.DAT", "300.000 0.000 2.000 0.000 3.500"),
            "标准断面：应为「桩号 + 9 个数值」共 10 列，只给了 5 列", want="9 个数值")
    # ⑨ 非数
    _reject(_ch + _ctr_kw("ZTFBP.DAT", "abc 1  -1.500 0.000 8.000 0"),
            "桩号不是数", want="数")
    # ⑩ 坡度绝对值超上限（SLOPE_ABS_MAX = 100，1:101 显然不是坡度而是列错位）
    _reject(_ch + _ctr_kw("ZTFBP.DAT", "100.000 1  -150.000 0.000 8.000 0"),
            "★ 坡度 |1:m| 的 m 超过上限 100（多半是列错位）", want="坡度")

    # ★★ 元测试：上面每一条"应拒绝"都必须**真的靠那条规则**被拒，
    #    而不是被别的规则顺手拦下。做法：把违规点修好，同一段就应当通过。
    _fix = _ch + _ctr_kw("ZTFBP.DAT", "100.000 2  -1.500 0.000 8.000 0  0.000 0.000 1.500 0")
    check("★★ 元测试：把「组数 2 只给 1 组」补成 2 组 → 同一段即通过（证明拒的就是组数）",
          len(ctr.parse(_fix)["control"]["slope_segments"]) == 2)
    _fix2 = _ch + _ctr_kw("ZTFBP.DAT", "100.000 1  -1.500 0.000 8.000 0")
    check("★★ 元测试：把 9999 桩号换成真桩号 → 即通过（证明拒的就是 9999）",
          ctr.parse(_fix2)["control"]["slope_segments"][0]["station_m"] == 100.0)

    # ── check_control：正例零告警（否则告警就是噪声，人会学会无视它）────────
    _ctl_warn = ctr.check_control(_cc)
    check("★ 物理检查：正例零告警（含同桩号多级边坡 —— 这是合法的）",
          _ctl_warn == [], str(_ctl_warn))

    # ★★ 元测试：证明「同桩号多级」与「同桩号重复」是**可区分**的两种情形。
    #    这条是**数据库抓到的 bug 反推出来的**：第一版 check_control 的身份签名里
    #    漏了 group_seq，于是本工程真实的边坡（填方 5 级、挖方 6 级都在同一桩号）
    #    被报了 **22 条假告警**。恒真的检查比没有检查更糟 —— 它会淹没真告警。
    #    这里把两件事分开证明：① 多级不报 ② 真重复要报。
    _dup = {k: [dict(r) for r in v] for k, v in _cc.items()}
    _dup["slope_segments"].append(dict(_dup["slope_segments"][0]))   # 逐字段相同的两行
    check("★★ 元测试：把同一级边坡复制一遍 → 必须报重复（证明上面那条不是恒真）",
          bool(ctr.check_control(_dup)),
          str(ctr.check_control(_dup)[:2]))
    # ★ 上面那条的第一版写成 `all(... or True ...)` —— **恒真**，等于没查。
    #   换成可伪证的形式：让两行在 (side, slope_kind, station_m) 上**撞在一起**，
    #   只有 group_seq 不同。若身份签名漏了 group_seq，这里必然报重复。
    _no_group = {(r["side"], r["slope_kind"], r["station_m"]) for r in _cc["slope_segments"]}
    check("★★ 元测试：两行在「侧别/填挖/桩号」上完全相同、只有 group_seq 不同 → "
          "仍不报重复（若签名漏了 group_seq 这里必报，就是那 22 条假告警的成因）",
          len(_cc["slope_segments"]) == 2 and len(_no_group) == 1
          and ctr.check_control(_cc) == [],
          f"2 行 → 去掉 group_seq 只剩 {len(_no_group)} 个签名；"
          f"告警 {ctr.check_control(_cc)[:1]}")

    # ── check_against_stations：范围对账（.CTR 不必覆盖全线，只查越界）────────
    _sts = [{"station_m": x} for x in (0.0, 100.0, 300.0, 800.0, 1000.0)]
    check("对账：.CTR 的分段桩号都落在路线范围内 → 不报",
          ctr.check_against_stations(_cc, _sts) == [],
          str(ctr.check_against_stations(_cc, _sts)))
    _out = {k: [dict(r, station_m=2000.0) for r in v] for k, v in _cc.items()}
    check("★ 对账：桩号越出路线终点 → 必须报（否则会静默挂到别的路段上）",
          bool(ctr.check_against_stations(_out, _sts)))

    # ── 登记：17 个空关键字 + ZDMDG 必须**登记在册**（不是"忘了")────────────────
    # ⚠ XXXX.DAT 不在 PARSED_KEYWORDS 里 —— 它是**终止符**不是数据关键字。
    #   第一版把它也算进去，于是这条断言是"我自己写错了"而不是"代码错了"。
    check("登记：正例里用到的 9 个数据关键字都在 PARSED_KEYWORDS 里",
          {"ZTFBP.DAT", "YBGXS.DAT", "ZBZDM.DAT", "ZLCSD.DAT", "HDSJ.DAT",
           "TFFD.DAT", "ZYDK.DAT", "QCHBT.DAT", "SHUIZHUNDIAN.DAT"}
          <= set(ctr.PARSED_KEYWORDS),
          str(sorted(set(ctr.PARSED_KEYWORDS))))
    check("登记：终止符 XXXX.DAT **不是**数据关键字（它只表示文件结束）",
          ctr.TERMINATOR not in ctr.PARSED_KEYWORDS)
    check("★ 登记：教程有定义但本工程为空的关键字在 REGISTERED_NOT_BUILT 里（不建表）",
          len(ctr.REGISTERED_NOT_BUILT) == 6
          and {"ZFJBK.DAT", "YFJBK.DAT", "ZJSG.DAT", "YJSG.DAT",
               "ZFYHP.DAT", "YFYHP.DAT"} == set(ctr.REGISTERED_NOT_BUILT),
          str(sorted(ctr.REGISTERED_NOT_BUILT)))
    check("★ 登记：ZDMDG 单列一类（教程全文搜不到，但有 20 行数据）—— 不能混进"
          "「教程有定义但为空」，那会自相矛盾",
          set(ctr.UNDOCUMENTED_WITH_DATA) == {"ZDMDG.DAT"},
          str(sorted(ctr.UNDOCUMENTED_WITH_DATA)))
    check("登记：教程未定义且本工程也为空的关键字单列一类",
          {"GONGDIAN.DAT", "BGKZ.DAT", "PZSTDG.DAT", "RAILWAY_SHJG.DAT"}
          == set(ctr.UNDOCUMENTED),
          str(sorted(ctr.UNDOCUMENTED)))

    # ── 第 6f 组：.tf 土方数据解析器（应通过 / 应拒绝两侧）────────────────────
    #    .tf 与其余适配器**结构上不同**：它第 2 行**自带列名**（74 个）。
    #    本适配器靠它对齐列序 —— 供应商改列序就当场拒绝，而不是按位置静默错位。
    #    所以"应拒绝"一侧的重点是**表头**，不是数据行。
    print("\n第 6f 组  .tf 土方数据解析器（应通过 / 应拒绝两侧）")
    _th = "HINTCAD6.00_TF_SHUJU\r\n"

    def _tf_hdr(names=None):
        return "//" + "".join("[" + n + "]" for n in (names or tf.EXPECTED_HEADER)) + "\r\n"

    def _tf_row(**over):
        """造一行 74 列的 .tf 数据。**由 tf.COLUMNS 生成**，不手抄 74 个数。"""
        vals = {en: "0.000" for _, en in tf.COLUMNS}
        vals["station_m"] = "100.000"
        vals["cut_area_m2"] = "12.500"
        vals["fill_area_m2"] = "0.000"
        vals["drainage_ditch_flag"] = "1"
        vals.update({k: str(v) for k, v in over.items()})
        return "\t".join(vals[en] for _, en in tf.COLUMNS) + '\t""'

    _t_ok = _th + _tf_hdr() + _tf_row() + "\r\n"
    _t = tf.parse(_t_ok, file="ok.tf")
    _tp = _t["points"][0]
    check("应通过：厂商版本从魔数取出", _t["vendor_version"] == "6.00", _t["vendor_version"])
    check("应通过：detect 认得自家魔数", tf.detect(_t_ok) is True)
    check("应通过：detect 不认别家的魔数", tf.detect("HINTCAD6.00_LJ_SHUJU\r\n") is False)
    check("应通过：段名/文件类别/载荷键与登记一致",
          tf.SEGMENT == "earthwork_section"
          and weidi.SEGMENT_FILES["earthwork_section"][0] == ".tf"
          and weidi.SEGMENT_FILES["earthwork_section"][1] == tf.FILE_KIND
          and tf.PAYLOAD_KEY == "points",
          f"{tf.SEGMENT} / {tf.FILE_KIND} / {tf.PAYLOAD_KEY}")
    check("★ 应通过：74 列全部解出，且键集 == 适配器 COLUMNS 的键集",
          len(_tp) == 74 and set(_tp) == {en for _, en in tf.COLUMNS},
          f"实为 {len(_tp)} 列")
    check("★ 应通过：桩号是**米**（IR 约定），不叫 station_km",
          _tp["station_m"] == 100.0 and "station_km" not in _tp,
          str({k: v for k, v in _tp.items() if "station" in k}))
    check("应通过：挖方/填方各归其位（文件表头口径：第 2 列挖、第 3 列填）",
          _tp["cut_area_m2"] == 12.5 and _tp["fill_area_m2"] == 0.0,
          f"{_tp['cut_area_m2']} / {_tp['fill_area_m2']}")
    check("应通过：第 75 列 `\"\"` 被认掉，不算多一列", len(_tp) == 74)

    def _rej(text, tag, want):
        try:
            tf.parse(text, file="bad.tf")
        except SourceInvalid as exc:
            check(f"应拒绝：{tag}", want in str(exc), str(exc)[:110])
        else:
            check(f"应拒绝：{tag}", False, "竟然解析通过了")

    _rej("HINTCAD6.00_ZDM_SHUJU\r\n" + _tf_hdr() + _tf_row() + "\r\n",
         "魔数不是 .tf", "魔数不匹配")
    _rej(_th + _tf_row() + "\r\n", "缺第 2 行的列名注释", "列名注释")
    _rej(_th + _tf_hdr(list(tf.EXPECTED_HEADER)[:-1]) + _tf_row() + "\r\n",
         "表头列数少一个", "列名个数")
    # ★★ 这一条最要紧：把第 2/3 列在**表头**上换过来（即说明书正文的写法）——
    #    表头变了就必须报，否则 74 列会整体按位置静默错位。
    _sw = list(tf.EXPECTED_HEADER)
    _sw[1], _sw[2] = _sw[2], _sw[1]
    _rej(_th + _tf_hdr(_sw) + _tf_row() + "\r\n",
         "表头第 2/3 列换序（= 说明书正文的写法）", "列名与期望不符")
    _rej(_th + _tf_hdr() + "\t".join(["0.000"] * 73) + "\r\n",
         "数据行只有 73 列", "字段数应为 74")
    _rej(_th + _tf_hdr() + _tf_row(cut_area_m2="abc") + "\r\n",
         "挖方面积不是数字", "不是合法数字")
    _rej(_th + _tf_hdr() + _tf_row(station_m="-5.000") + "\r\n",
         "桩号为负", "桩号为负")
    _rej("", "空文件", "空文件")

    # ★★ 元测试：把上面被判拒的**逐个改回合法**，必须全部通过 ——
    #    证明这些"应拒绝"不是靠别的原因顺带拒绝的（非空洞）。
    check("★★ 元测试：表头列序改回正序 → 必须通过",
          len(tf.parse(_th + _tf_hdr() + _tf_row() + "\r\n")["points"]) == 1)
    check("★★ 元测试：第 2/3 列**值**互换（表头不动）→ 不该报错，只是两个数换了位置",
          (lambda r: r["cut_area_m2"] == 0.0 and r["fill_area_m2"] == 12.5)(
              tf.parse(_th + _tf_hdr() + _tf_row(cut_area_m2="0.000", fill_area_m2="12.500") + "\r\n")["points"][0]),
          "表头没变时不该报错 —— 否则这条检查就是在替数据做业务判断")

    # ★ 物理检查：正例零告警；且**必须能响**
    _tpts = tf.parse(_th + _tf_hdr()
                     + _tf_row() + "\r\n"
                     + _tf_row(station_m="200.000", cut_area_m2="3.000") + "\r\n")["points"]
    check("★ 应通过：正例物理检查零告警", tf.check_earthwork(_tpts) == [],
          str(tf.check_earthwork(_tpts)[:2]))
    check("★ 元测试：桩号倒序 → 必须报（非空洞）",
          len(tf.check_earthwork(list(reversed(_tpts)))) == 1,
          str(tf.check_earthwork(list(reversed(_tpts)))))
    check("★ 元测试：挖方面积为负 → 必须报",
          any("为负" in w for w in tf.check_earthwork([dict(_tpts[0], cut_area_m2=-1.0)])),
          str(tf.check_earthwork([dict(_tpts[0], cut_area_m2=-1.0)])))
    check("★ 元测试：计排水沟写 2 → 必须报",
          any("0/1" in w for w in tf.check_earthwork([dict(_tpts[0], drainage_ditch_flag=2.0)])))
    # ★★ 反面：半填半挖断面**不该**报 —— 第一版这里写了「填挖不能同时为正」，
    #    在真实 332 行里报了 16 条假告警（山区半填半挖极常见）。规则已删。
    check("★★ 半填半挖断面（填挖都为正）→ **不该**报 —— 曾在此报过 16 条假告警",
          tf.check_earthwork([dict(_tpts[0], cut_area_m2=8.0, fill_area_m2=5.0)]) == [],
          str(tf.check_earthwork([dict(_tpts[0], cut_area_m2=8.0, fill_area_m2=5.0)])))

    # ★ 与 .STA 对账：逐桩段比**集合相等**
    _sta2 = [{"station_m": 100.0}, {"station_m": 200.0}]
    check("★ 应通过：与 .STA 集合相等 → 零告警", tf.check_against_stations(_tpts, _sta2) == [],
          str(tf.check_against_stations(_tpts, _sta2)))
    check("★ 元测试：.STA 多一个桩号 → 必须报",
          any("没有的" in w for w in tf.check_against_stations(_tpts, _sta2 + [{"station_m": 300.0}])))
    check("★ 元测试：.tf 多一个桩号 → 必须报",
          any("没有的" in w for w in tf.check_against_stations(_tpts + [{"station_m": 999.0}], _sta2)))

    # ★★ 适配器列名 ↔ DDL 列名 逐条对账（改了适配器不改 DDL 会红）
    _ddl_txt = (ROOT / "sql" / "10_ddl_v0.5.sql").read_text(encoding="utf-8")
    _m = re.search(r"CREATE TABLE IF NOT EXISTS earthwork_section\s*\((.*?)\n\);", _ddl_txt, re.S)
    _ddl_cols = [x.group(1) for x in
                 re.finditer(r"^\s{4}([a-z_][a-z0-9_]*)\s+(?:bigint|numeric|smallint|text)", _m.group(1), re.M)]
    _meta = {"id", "section_id", "station_id", "remark", "station_km"}
    _ddl_data = [c for c in _ddl_cols if c not in _meta]
    # 桩号在两边**故意不同名**：IR 用 station_m（米，源文件原生单位），
    # 落库才换算成 station_km。所以对账时两边都排除它，改名本身由下一条断言单独验。
    _adapter = [en for _, en in tf.COLUMNS if en != "station_m"]
    check("★★ 适配器 73 列 ↔ DDL earthwork_section 73 列，逐条一致（缺/多都为 0）",
          sorted(_ddl_data) == sorted(_adapter),
          f"缺 {sorted(set(_adapter) - set(_ddl_data))} ／ 多 {sorted(set(_ddl_data) - set(_adapter))}")
    _adapter_all = [en for _, en in tf.COLUMNS]
    check("★★ DDL 里那列叫 station_km（落库换算），适配器里叫 station_m（IR 约定）",
          "station_km" in _ddl_cols and "station_m" not in _ddl_cols
          and "station_m" in _adapter_all and "station_km" not in _adapter_all,
          f"DDL 有 station_km={('station_km' in _ddl_cols)}／适配器有 station_m={('station_m' in _adapter_all)}")

    # ── 第 6g 组：.lj 路基设计中间数据解析器（应通过 / 应拒绝两侧）──────────────
    #    .lj **没有**自带表头（与 .tf 相反），只能按位置解析 ——
    #    所以这里最要紧的"应拒绝"是**列数**：说明书说 20 列、实测 24 列，
    #    一旦列数变了却照收，24 列会整体错位。
    print("\n第 6g 组  .lj 路基设计中间数据解析器（应通过 / 应拒绝两侧）")
    _lh = "HINTCAD7.0_LJ_SHUJU\r\n"

    def _lj_row(**over):
        vals = {en: "0.000" for en in lj.COLUMNS}
        vals["station_m"] = "100.000"
        vals["ground_elev_m"] = "57.262"
        vals["design_elev_m"] = "57.500"
        vals["left_lane_width_m"] = "3.500"
        vals["right_lane_width_m"] = "3.500"
        vals.update({k: str(v) for k, v in over.items()})
        return "\t".join(vals[en] for en in lj.COLUMNS)

    _l_ok = _lh + _lj_row() + "\r\n"
    _l = lj.parse(_l_ok, file="ok.lj")
    _lp = _l["points"][0]
    check("应通过：厂商版本从魔数取出", _l["vendor_version"] == "7.0", _l["vendor_version"])
    check("应通过：detect 认得自家魔数", lj.detect(_l_ok) is True)
    check("应通过：detect 不认别家的魔数", lj.detect("HINTCAD7.0_TF_SHUJU\r\n") is False)
    check("应通过：段名/文件类别/载荷键与登记一致",
          lj.SEGMENT == "roadbed_design_point"
          and weidi.SEGMENT_FILES["roadbed_design_point"][0] == ".lj"
          and weidi.SEGMENT_FILES["roadbed_design_point"][1] == lj.FILE_KIND
          and lj.PAYLOAD_KEY == "points",
          f"{lj.SEGMENT} / {lj.FILE_KIND} / {lj.PAYLOAD_KEY}")
    check("★ 应通过：24 列全部解出，且键集 == 适配器 COLUMNS 的键集",
          len(_lp) == 24 and set(_lp) == set(lj.COLUMNS), f"实为 {len(_lp)} 列")
    check("★ 应通过：桩号是**米**，不叫 station_km",
          _lp["station_m"] == 100.0 and "station_km" not in _lp)
    check("应通过：地面标高/设计标高各归其位",
          _lp["ground_elev_m"] == 57.262 and _lp["design_elev_m"] == 57.5)
    check("★ 应通过：11 个高差列在（第 14–24 列）",
          sum(1 for k in _lp if k.startswith("elev_diff_")) == 11)
    check("★ 登记：两个待考列单列一类（说明书无对应项，本工程恒 0）",
          set(lj.UNKNOWN_COLUMNS) == {"extra_width_09_m", "extra_width_11_m"}
          and all(c in lj.COLUMNS for c in lj.UNKNOWN_COLUMNS),
          str(lj.UNKNOWN_COLUMNS))

    def _lrej(text, tag, want):
        try:
            lj.parse(text, file="bad.lj")
        except SourceInvalid as exc:
            check(f"应拒绝：{tag}", want in str(exc), str(exc)[:110])
        else:
            check(f"应拒绝：{tag}", False, "竟然解析通过了")

    _lrej("HINTCAD7.0_TF_SHUJU\r\n" + _lj_row() + "\r\n", "魔数不是 .lj", "魔数不匹配")
    _lrej(_lh + "\t".join(["0.000"] * 23) + "\r\n", "只有 23 列", "字段数应为 24")
    _lrej(_lh + "\t".join(["0.000"] * 25) + "\r\n", "有 25 列", "字段数应为 24")
    _lrej(_lh + _lj_row(design_elev_m="abc") + "\r\n", "设计标高不是数字", "不是合法数字")
    _lrej(_lh + _lj_row(station_m="-1.000") + "\r\n", "桩号为负", "桩号为负")
    _lrej("", "空文件", "空文件")
    # ★★ 说明书说 20 列 —— 若真按 20 列收，第 21–24 列会被静默丢掉。这里把
    #    "20 列也能过"钉死为**必须拒绝**。
    _lrej(_lh + "\t".join(["0.000"] * 20) + "\r\n",
          "★ 按说明书写的 20 列 → 必须拒绝（否则 21–24 列静默丢失）", "字段数应为 24")

    # ★ 物理检查
    _lpts = lj.parse(_lh + _lj_row() + "\r\n"
                     + _lj_row(station_m="200.000") + "\r\n")["points"]
    check("★ 应通过：正例物理检查零告警", lj.check_design(_lpts) == [],
          str(lj.check_design(_lpts)[:2]))
    check("★ 元测试：桩号倒序 → 必须报（非空洞）",
          len(lj.check_design(list(reversed(_lpts)))) == 1,
          str(lj.check_design(list(reversed(_lpts)))))
    check("★ 元测试：宽度为负 → 必须报",
          any("为负" in w for w in lj.check_design([dict(_lpts[0], left_lane_width_m=-1.0)])))
    check("★ 元测试：标高量级离谱（列错位的样子）→ 必须报",
          any("量级" in w for w in lj.check_design([dict(_lpts[0], design_elev_m=99999.0)])))
    check("★★ 标高为负但不离谱（如 −50 m 的洼地）→ **不该**报 —— 否则会常响",
          lj.check_design([dict(_lpts[0], ground_elev_m=-50.0)]) == [],
          str(lj.check_design([dict(_lpts[0], ground_elev_m=-50.0)])))

    # ★ 与 .STA 对账
    check("★ 应通过：与 .STA 集合相等 → 零告警",
          lj.check_against_stations(_lpts, [{"station_m": 100.0}, {"station_m": 200.0}]) == [])
    check("★ 元测试：.STA 多一个桩号 → 必须报",
          any("没有的" in w for w in lj.check_against_stations(
              _lpts, [{"station_m": 100.0}, {"station_m": 200.0}, {"station_m": 300.0}])))

    # ★★ 适配器列名 ↔ DDL 列名 逐条对账
    _m2 = re.search(r"CREATE TABLE IF NOT EXISTS roadbed_design_point\s*\((.*?)\n\);", _ddl_txt, re.S)
    _ddl2 = [x.group(1) for x in
             re.finditer(r"^\s{4}([a-z_][a-z0-9_]*)\s+(?:bigint|numeric|smallint|text)", _m2.group(1), re.M)]
    _ddl2_data = [c for c in _ddl2 if c not in _meta]
    _adapter2 = [en for en in lj.COLUMNS if en != "station_m"]
    check("★★ 适配器 23 列 ↔ DDL roadbed_design_point 23 列，逐条一致（缺/多都为 0）",
          sorted(_ddl2_data) == sorted(_adapter2),
          f"缺 {sorted(set(_adapter2) - set(_ddl2_data))} ／ 多 {sorted(set(_ddl2_data) - set(_adapter2))}")


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

    ddl_text = (ROOT / "sql" / "10_ddl_v0.5.sql").read_text(encoding="utf-8")
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

    # ★★ 通用断言（本轮补）：DDL 里**所有** station*_km 列的 scale 都必须 ≥ 6。
    #    上面那条只钉了 station_local_km。本轮实测抓到 J1 earthwork_section.station_km
    #    被我写成了 numeric(10,4)（0.1 m），而全库其余 30 处都是 numeric(12,6)。
    #    本工程 .tf 的桩号是 20 m 一个，0.1 m 确实"够用" —— 但"对本工程够用"不是标准：
    #    下一条路的桩号精度不由本工程决定。同族列必须同一个标准，故这条钉**全族**。
    _km_cols = re.findall(r"^\s{4}(\w*station\w*_km)\s+numeric\((\d+),\s*(\d+)\)",
                          ddl_text, re.M)
    _km_bad = [(c, f"numeric({p},{sc})") for c, p, sc in _km_cols if int(sc) < 6]
    check(f"★★ DDL 里 {len(_km_cols)} 个 station*_km 列，scale 全部 ≥ 6（1 mm）",
          bool(_km_cols) and not _km_bad, f"不合格：{_km_bad}")
    # 元测试：证明这条不是空断言 —— 把我犯过的那个写法喂进去，必须被判不合格
    _fake = "    station_km                             numeric(10,4)    NULL,   -- x"
    _fm = re.findall(r"^\s{4}(\w*station\w*_km)\s+numeric\((\d+),\s*(\d+)\)", _fake, re.M)
    check("★★ 元测试：numeric(10,4) 的写法**确实会被这条判不合格**（非空断言）",
          bool(_fm) and int(_fm[0][2]) < 6, f"解析到 {_fm}")

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
    # 合成 IR 里没有纵断面/超高/路幅/.CTR，故那些表是 0 行 —— 但仍然必须在 plan 的产出里：
    # 漏掉一个键会让落库阶段静默少写一张表，而不是报错。
    #
    # ★ 两件事**分开断言**，不合成一个"整份字典相等"：
    #   ① 表集合 —— 且**推导**出来（已实现段对应的表 ∪ CTR_ON_CONFLICT 里的 9 张），
    #      不写死清单：每加一段都要回来改一次断言，会诱使人"顺手把新表名填进去"，
    #      而不是想清楚"这张表该不该被 plan 产出"。
    #   ② 有数据的表的行数 —— 精确；其余必须全 0。
    _want = ({"station_sequence", "alignment_pi", "alignment_element",
              "profile_grade_point", "profile_ground_point",
              "superelev_transition", "roadbed_width",
              # v0.5 新增两张逐桩表：各对应一个段，表名与段名同名
              "earthwork_section", "roadbed_design_point",
              # v0.5 K 节：★这张**表名与段名不同名** —— 段是 cross_section，
              # 表是 cross_section_ground_point。上面那句"表名与段名同名"对 .tf/.lj 成立，
              # 对 .HDM 不成立，所以它单独列在这里并说明。
              "cross_section_ground_point"}
             | {t for t, _ in di.CTR_ON_CONFLICT})
    check("plan 产出的表集合 = 已实现段对应的表 ∪ .CTR 的 9 张（漏一个键会静默少写一张表）",
          set(counts) == _want,
          f"多出 {sorted(set(counts) - _want)}／缺少 {sorted(_want - set(counts))}")
    check("行数：有数据的 3 张表正确（桩号 30 / 交点 1 / 单元 4），其余全 0",
          {t: n for t, n in counts.items() if n}
          == {"station_sequence": 30, "alignment_pi": 1, "alignment_element": 4},
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

    # ★★ 交点桩号必须落库，且与 .JD 文件里的值逐条对上。
    #   原注释写「交点桩号 = ZH + 切线长，是派生量，故 DDL 里没有它的列」——
    #   **前提是错的**：.JD 第 3 列「本点桩号」本来就给了它（jd.py 的 f10[0] 早在读，
    #   只是没往下传）。"可派生"不等于"源里没有"：源里给了却不存，就永远没有第二个值可比，
    #   而这张表也是全库唯一没有桩号列的几何表。
    # ⚠ 容差不能用 TOL_COORD_M(1e-6 m)：station_km 以**公里**存 numeric(12,6)，
    #   即存储粒度是 1 mm —— 比 1e-6 m 还粗 1000 倍。拿比存储精度更细的容差去比，
    #   报的是"公里→米往返的舍入"，不是几何错误（实测 fixture 上差 1.36 µm）。
    #   取半个存储粒度（0.5 mm）才是这条断言真正能保证的东西。
    _KM_STORE_TOL_M = 0.5e-3
    _pi = planned["tables"]["alignment_pi"]
    check("★ alignment_pi 每行都有 station_km（不再只有内部 _station_m）",
          bool(_pi) and all(r.get("station_km") is not None for r in _pi),
          str([r.get("station_km") for r in _pi][:4]))
    _jdp = {p_["seq"]: p_ for p_ in (ir_fx["segments"].get("alignment_pi") or [])}
    _diffs = []
    for _r in _pi:
        _f = _jdp.get(_r["pi_seq"])
        if _f is None:
            _diffs.append((_r["pi_seq"], "文件里没有同号控制点")); continue
        _d = abs(_r["station_km"] * 1000.0 - _f["station_m"])
        if _d > _KM_STORE_TOL_M:
            _diffs.append((_r["pi_seq"], _d))
    check("★★ station_km 与 .JD 的「本点桩号」逐条吻合（两条独立路径对账）",
          not _diffs, str(_diffs))
    # ★ 元测试：把 station_km 改错，上面那条必须响 —— 否则它是空的
    _bad = [dict(r) for r in _pi]
    _bad[0]["station_km"] = (_bad[0]["station_km"] or 0) + 0.5
    _hit = [r for r in _bad
            if abs(r["station_km"] * 1000.0 - _jdp[r["pi_seq"]]["station_m"]) > _KM_STORE_TOL_M]
    check("★ 元测试：桩号改错 0.5 km 时对账会响（上面那条不是空的）",
          len(_hit) == 1, f"响了 {len(_hit)} 条")

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
        SKIP[0] += 1
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
        # ★ 静态抠源码只抓得到**内联元组**。`load()` 里若把 on_conflict 写成
        #   循环变量（.CTR 那 9 张表就是这样），静态抠就漏了 —— 实测漏过：
        #   9 处写成局部变量时本检查只覆盖 12 对，.CTR 的 9 对完全没被比过。
        #   所以改成：静态抠 + **直接读模块级常量** CTR_ON_CONFLICT，两路合并。
        _pairs += [(t, tuple(cols)) for t, cols in di.CTR_ON_CONFLICT]
        # ★ 元测试：抠不到东西 = 下面那条检查永远通过（空洞）—— 所以先证明抠得到。
        #   下限是**推导出来的**（内联 12 对 + .CTR 9 对 = 21），不是拍脑袋写的数字。
        _inline = len(_pairs) - len(di.CTR_ON_CONFLICT)
        check("★★ 元测试：能抠到 on_conflict 对（内联 + 常量两路，否则下面是空洞检查）",
              _inline >= 12 and len(di.CTR_ON_CONFLICT) == 9 and len(_pairs) == _inline + 9,
              f"内联 {_inline} 对 + CTR 常量 {len(di.CTR_ON_CONFLICT)} 对 = {len(_pairs)} 对")
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
            check("dry_run 报告计划行数（有数据的 3 张表精确，其余全 0）",
                  {t: n for t, n in rep["planned"].items() if n}
                  == {"station_sequence": 30, "alignment_pi": 1, "alignment_element": 4},
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
            # ★★ 必须钉住「**导入当时**」这四个字，不能只钉「几何等级 L2」。
            #   因为「导入当时几何等级 L2」**包含**「几何等级 L2」—— 只钉后者的话，
            #   两种写法都会绿，等于没钉。
            #   为什么这四个字重要：批次备注记的是**那一批导进来时**的等级快照，
            #   不是当前等级。毕设路段就有活例子 —— id=8 写「导入当时几何等级 L3」、
            #   id=155 写「…L4」，**两条都对**（中间 .HDM 适配器才写完）。
            #   旧措辞「几何等级 L3」读起来像当前状态，而它早就不是了。
            check("批次备注含几何等级，且写明是**导入当时**的快照",
                  "导入当时几何等级 L2" in remark, remark[:90])
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

    # ── 第 12a 组：completeness() 的等级必须与 derive_level() 自洽（打真库）──
    #
    # ★ 这一组是**因为一个真 bug 才补的**：
    #   `completeness()` 里那段内联循环只算了 `level_reason`，**忘了写 `level = lv`** ——
    #   于是 `geometry_level` 恒为 "L0"，而理由是对的（"cross_section 有数据"）。
    #   毕设路段明明有 2215 个横断面测点（L4 的条件），徽章却显示 L0。
    #
    #   为什么原来的测试没抓住：交叉核对只比了 `LEVEL_RULES`（规则表）和
    #   `level_hit`（谓词）—— **两个输入都对，用它们的那段循环错了**。
    #   修法是把判级抽成纯函数 `derive_level`（已在 test_dao_contract.py 里与 M2 对拍），
    #   这里再钉一层：**库里的真实数据走完整条路，等级必须自洽**。
    print("\n" + "=" * 74)
    print("第 12a 组  completeness() 等级 ↔ derive_level()（打真库，全部路段）")
    print("=" * 74)
    try:
        from rpdao import Dao as _DaoL                                # noqa: PLC0415
        from rpdao.repo import GeRepository as _GeL                   # noqa: PLC0415
        _dao_l = _DaoL(pg_dsn() or "", app_name="contract-test-12a")
        _dao_l.open()
    except Exception as exc:                                          # noqa: BLE001
        SKIP[0] += 1
        print(f"  ⊘ 跳过：{type(exc).__name__}: {str(exc)[:90]}")
    else:
        try:
            _secs = _dao_l.ge.sections()
            _mismatch, _checked = [], 0
            for _s in _secs:
                _sid = _s["id"]
                _c = _dao_l.ge.completeness(_sid)
                _lv = _c.get("geometry_level")
                _want = _GeL.derive_level({k for k, v in (_c.get("present") or {}).items() if v})[0]
                _checked += 1
                if _lv != _want:
                    _mismatch.append((_sid, _s.get("section_name"), _lv, _want,
                                      _c.get("level_reason")))
            check(f"★★ 每个路段的 geometry_level 都等于 derive_level(present)（查了 {_checked} 条）",
                  _checked > 0 and not _mismatch,
                  f"不自洽：{_mismatch[:3]}" if _mismatch else f"路段数 {_checked}")

            # ★ 具体到毕设：有 cross_section 就必须是 L4，且理由要能说出是哪一段。
            _b = _dao_l.ge.completeness(6)
            check("★★ 毕设（有 2215 个横断面测点）必须是 L4，不是 L0",
                  _b.get("geometry_level") == "L4",
                  f"实为 {_b.get('geometry_level')!r}，理由 {_b.get('level_reason')!r}")
            check("★ 毕设的理由要点出 cross_section",
                  "cross_section" in (_b.get("level_reason") or ""),
                  repr(_b.get("level_reason")))

            # 元测试：拿同一个方法、把 present 掏空，等级必须跟着变 ——
            # 证明上面那条不是"无论数据怎样都返回 L4"。
            check("★ 元测试：空段集合下 derive_level 给 L0（说明它真的看数据）",
                  _GeL.derive_level(set())[0] == "L0",
                  f"实为 {_GeL.derive_level(set())}")
        finally:
            _dao_l.close()

    # ── 第 12b 组：.lj 的 11 个高差列 ↔ .SUP 的逐桩横坡（交叉验证）──────────
    #
    # ★ 这一组是 .lj 解析器 docstring 里那句「它们能反过来校验 .SUP」的兑现 ——
    #   原文写着「（这一条留给契约测试，尚未做。）」，这里把它做了。
    #
    # 关系（实测 332/332 行成立，容差由源精度推出，见下）：
    #
    #     elev_diff_{i+1} − elev_diff_i = −σ · 宽度_i · 横坡_i / 100
    #
    #   其中 σ = +1（左半幅）/ −1（右半幅）。**σ 是必须的** —— .SUP 里左右两侧
    #   的横坡用的是同一套符号（正常路拱两侧都写 −2.00），而高差是「离开旋转轴
    #   就下降」，所以右半幅要翻号。漏掉 σ 时 332 行**全部**不符（最大差 0.14）。
    #
    #   10 个增量依次是：
    #     左土路肩 / 左硬路肩 / 左中分带 / 左行车道 / （0）/（0）
    #     右行车道 / 右中分带 / 右硬路肩 / 右土路肩
    #   中间两个 0 是左右中分带（本工程宽 0）。**宽度取自 .lj 自己那一行**，
    #   横坡取自 .SUP —— 两个文件互相印证，谁也没抄谁。
    #
    # 为什么容差是 2e-4：.SUP 的横坡只有**两位小数**（0.01%），乘最大宽度 3.5 m
    #   得 3.5 × 0.005% = 1.75e-4；.lj 的高差是四位小数，半 ULP 5e-5。
    #   取两者之和的量级 2e-4。实测最大残差 7.3e-5，远在界内 —— 也就是说
    #   剩下的差**全是两处源文件的舍入**，不是模型不对。
    print("\n" + "=" * 74)
    print("第 12b 组  .lj 的 11 个高差列 ↔ .SUP 逐桩横坡（交叉验证，打真库）")
    print("=" * 74)
    try:
        from rpdao.write import WriteDao as _WDx                  # noqa: PLC0415
        _dao_x = _WDx(pg_dsn() or "", app_name="contract-test-12b")
        _dao_x.open()
    except Exception as exc:                                      # noqa: BLE001
        SKIP[0] += 1
        print(f"  ⊘ 跳过：{type(exc).__name__}: {str(exc)[:90]}")
    else:
        try:
            _lj = _dao_x.query("""
                select station_km,
                       left_earth_shoulder_width_m l_es, left_hard_shoulder_width_m l_hs,
                       left_lane_width_m l_ln, left_median_width_m l_md,
                       right_median_width_m r_md, right_lane_width_m r_ln,
                       right_hard_shoulder_width_m r_hs, right_earth_shoulder_width_m r_es,
                       elev_diff_01_m e1, elev_diff_02_m e2, elev_diff_03_m e3,
                       elev_diff_04_m e4, elev_diff_05_m e5, elev_diff_06_m e6,
                       elev_diff_07_m e7, elev_diff_08_m e8, elev_diff_09_m e9,
                       elev_diff_10_m e10, elev_diff_11_m e11
                  from roadbed_design_point order by station_km""")
            _sup = _dao_x.query("""
                select station_km, earth_shoulder_left_pct p_es_l, hard_shoulder_left_pct p_hs_l,
                       lane_left_pct p_ln_l, lane_right_pct p_ln_r,
                       hard_shoulder_right_pct p_hs_r, earth_shoulder_right_pct p_es_r
                  from superelev_transition order by station_km""")
        finally:
            _dao_x.close()

        if not _lj or not _sup:
            SKIP[0] += 1
            print(f"  ⊘ 跳过：库里没有 .lj/.SUP 数据（roadbed_design_point={len(_lj)}，"
                  f"superelev_transition={len(_sup)}）")
        else:
            import bisect as _bisect

            _PCT = ["p_es_l", "p_hs_l", "p_ln_l", "p_ln_r", "p_hs_r", "p_es_r"]
            _seq = [{"km": float(r["station_km"]),
                     **{c: (None if r[c] is None else float(r[c])) for c in _PCT}} for r in _sup]
            _kms = [x["km"] for x in _seq]

            def _slope(col: str, km: float, *, carry: bool = False) -> float:
                """取 km 处的横坡。

                9999 → NULL。语义是「**跳过**」——横坡渐变穿过这个控制点继续走，
                所以在它两侧的控制点之间**线性插值**。（仓库另一处断言已钉住
                「跳过 ≠ 沿用上值」；这里 carry=True 就是那个错误的做法，留给变异用。）
                """
                if km <= _kms[0]:
                    for x in _seq:
                        if x[col] is not None:
                            return x[col]
                    return 0.0
                i = _bisect.bisect_right(_kms, km + 1e-9) - 1
                j = i
                while j >= 0 and _seq[j][col] is None:
                    j -= 1
                k = i + 1
                while k < len(_seq) and _seq[k][col] is None:
                    k += 1
                if j < 0 and k >= len(_seq):
                    return 0.0
                if j < 0:
                    return _seq[k][col]
                if k >= len(_seq) or carry:
                    return _seq[j][col]
                x0, x1 = _kms[j], _kms[k]
                y0, y1 = _seq[j][col], _seq[k][col]
                return y0 if x1 == x0 else y0 + (y1 - y0) * (km - x0) / (x1 - x0)

            # (宽度列, 横坡列, σ)   σ=+1 左半幅 / −1 右半幅
            _SPEC = [("l_es", "p_es_l", 1), ("l_hs", "p_hs_l", 1),
                     ("l_md", "p_ln_l", 1), ("l_ln", "p_ln_l", 1),
                     (None, None, 1), (None, None, 1),
                     ("r_ln", "p_ln_r", -1), ("r_md", "p_ln_r", -1),
                     ("r_hs", "p_hs_r", -1), ("r_es", "p_es_r", -1)]
            _E = ["e%d" % i for i in range(1, 12)]
            _TOL = 2e-4        # 见上面的推导：3.5m × 0.01% + 四位小数半 ULP

            def _scan(*, flip_right: bool = False, carry: bool = False,
                      drop_sigma: bool = False) -> tuple[int, float]:
                """返回 (不符行数, 最大残差)。三个开关供变异用。"""
                n_bad, worst = 0, 0.0
                for r in _lj:
                    km = float(r["station_km"])
                    inc = [float(r[_E[i + 1]]) - float(r[_E[i]]) for i in range(10)]
                    exp = []
                    for w, c, sg in _SPEC:
                        if w is None:
                            exp.append(0.0)
                            continue
                        s = _slope(c, km, carry=carry)
                        eff = 1 if drop_sigma else sg
                        if flip_right and sg < 0:
                            eff = -eff
                        exp.append(-eff * float(r[w]) * s / 100.0)
                    d = max(abs(a - b) for a, b in zip(inc, exp))
                    worst = max(worst, d)
                    if d > _TOL:
                        n_bad += 1
                return n_bad, worst

            _bad, _worst = _scan()
            check(f"★ .lj 的 11 个高差列可由 .SUP 逐桩横坡 + .lj 宽度复现"
                  f"（{len(_lj)} 行全对，容差 {_TOL:g}）",
                  _bad == 0, f"不符 {_bad} 行，最大残差 {_worst:.3e}")

            # ── 变异：证明上面那条不是摆设 ────────────────────────────────
            _b1, _w1 = _scan(flip_right=True)
            check("★★ 元测试：右半幅不翻号 → 大量不符（σ 不是摆设）",
                  _b1 > len(_lj) // 2, f"翻号后不符 {_b1}/{len(_lj)} 行，最大残差 {_w1:.3e}")
            _b2, _w2 = _scan(drop_sigma=True)
            check("★★ 元测试：整个丢掉 σ（左右都不翻）→ 也必须不符",
                  _b2 > 0, f"丢 σ 后不符 {_b2}/{len(_lj)} 行，最大残差 {_w2:.3e}")
            _b3, _w3 = _scan(carry=True)
            check("★ 元测试：9999 按「沿用上值」而不是「插值穿过」→ 超高段必须不符",
                  _b3 > 0, f"沿用上值后不符 {_b3}/{len(_lj)} 行，最大残差 {_w3:.3e}")
            _lc = sum(1 for r in _lj
                      if abs(_slope("p_ln_l", float(r["station_km"])) -
                             _slope("p_ln_l", float(r["station_km"]), carry=True)) > 1e-9)
            check("★ 元测试：确实存在插值≠沿用的桩号（否则上一条是空断言）",
                  _lc > 0, f"{_lc}/{len(_lj)} 个桩号上两种读法不同")

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
        SKIP[0] += 1
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
    # ⚠ 16 → 19：.hda（2026-09-22 定「加」）+ .prj + .dtm（见第 14b 组）。
    #   本用例 `project_dir=None` ⇒ 磁盘补行不发生，故 19 里的 .prj/.dtm 是
    #   **第 14b 组**用临时目录单独验的；这里数的是**只看 .PRJ 时**的条数 = 17。
    # 五张 → 六张 → 七张 → 九张 → 十一张：L 节 earthwork_factor、M 节 earthwork_transfer、
    #   N 节 borrow_pit/spoil_pit、O 节 earthwork_haul_stat/earthwork_fill_stat
#   （六张都来自同一个 .tsftxt）。
    #   本用例 project_dir=None ⇒ 目录里没有 .tsftxt，故**两张都是 0 行**
    #   （缺 .tsf 是正常的，不报错 —— 这与"有文件却解析失败"是两回事）。
    check("十一张表各 1/1/1/1/0/0/0/0/0/0 行 + design_file 17 行"
          "（.PRJ 声明 30 条 − 12 条空路径 − .cys + .hda）",
          cnt == {"design_project": 1, "road_line": 1, "road_section": 1,
                  "section_design_attr": 1, "design_file": 17,
                  "earthwork_factor": 0, "earthwork_transfer": 0,
                  "borrow_pit": 0, "spoil_pit": 0,
                  "earthwork_haul_stat": 0, "earthwork_fill_stat": 0}, str(cnt))
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
    # ★★ 两条**没给键号**的文件，结局**不同** —— 不能合并（2026-09-22 定）：
    #   .hda 首行 `HINTSOFT_HD_**PRJ**_1045`，有 BEGIN_CUL(涵洞)、桩号+GUID、
    #        `[基本参数] 0 17 0` 这样的分节 —— **工程数据**。纬地官方技术支持原文：
    #        「每一个涵洞项目都有两个设计文件（hda 和 cys）组成，其中 **hda 文件用于
    #         保存涵洞设计参数**，cys 文件用于保存绘图参数」「只需要打开新的路线项目，
    #         **在项目管理器中把原来的（hda 和 cys）文件路径添加进来即可**」——
    #        涵洞是**独立产品**(HintHD)，按路径手工挂进来，故〔文件名〕段里
    #        **有名有路径、就是没有号**。**不是疏漏。**
    #        → **进台账**（file_kind_code=NULL），`pending`。
    #        ⚠ 为什么不是 `blocked`：blocked = **结构上**读不了（二进制）；.hda 是
    #          纯文本 GBK，框架**能**解析（4 个涵洞的 10 个分节完全一致、节号固定枚举）。
    #          解析不了的只是**每列数字的含义** —— 只有教程 §24.13.2 说得清，公开取不到。
    #   .cys 首行 `HINTSOFT_HD_**SYS**_1026`，内容是尺寸标注样式(ZDIMAPP)、
    #        图框([TITLE] 1:[SCALE])、填充图案(ANSI31) —— **软件的系统参数**，
    #        纬地自己的说明也写它是"安装目录下'系统设置'文件夹中的系统参数.cys"。
    #        → **不进台账**，跟字段号无关。
    _hda = [f for f in planned["tables"]["design_file"] if f["file_name"].endswith(".hda")]
    check("★ .hda 进台账：file_kind_code=NULL、parse_status=pending",
          len(_hda) == 1 and _hda[0]["file_kind_code"] is None
          and _hda[0]["parse_status"] == "pending", str(_hda))
    check("★ 它的 remark 说清「声明了但没给号，因为是涵洞系统的文件」",
          _hda and "涵洞系统" in (_hda[0]["remark"] or "")
          and "没有键号" in (_hda[0]["remark"] or ""), str(_hda[0] if _hda else None))
    check("★ 只剩 1 条被跳过（.cys），且理由是**系统参数**不是「未给字段号」",
          len(planned["skipped_files"]) == 1
          and "系统参数" in planned["skipped_files"][0]
          and ".cys" in planned["skipped_files"][0],
          str(planned["skipped_files"]))
    check("★★ 元测试：跳过理由**不能**只是「未给字段号」（那是表象，不是原因）",
          all("未给字段号" not in x for x in planned["skipped_files"]),
          str(planned["skipped_files"]))
    # ⚠⚠ 这条原来写的是 `all(".hda" not in x for x in skipped)` —— **判据是错的**。
    #   它靠"跳过理由这段文字里有没有 `.hda` 三个字"来判断，于是
    #   **改一句文案就能让它红/绿**：实测只是把 .cys 的描述写成
    #   「与工程数据 .hda 的 … 成对」（那句话本身是对的、且有用），
    #   这条检查就红了 —— 而 `.hda` 根本没有被跳过。
    #   **一个会因文案改动而变红的检查，测的不是它声称的东西。**
    #   → 改成**结构化**：从每条跳过理由里**提取出后缀**，再判后缀集合。
    #     `skipped_files` 是给人看的 `list[str]`（首段就是 file_kind_name，
    #     形如 `涵洞系统参数文件(*.cys)（…）`），故用 `(*.xxx)` 抓后缀。
    def _skip_suffixes(items):
        out = set()
        for _x in items:
            _m = re.search(r"\(\*\.([A-Za-z0-9]+)\)", _x)
            if _m:
                out.add("." + _m.group(1).lower())
        return out
    _sk = _skip_suffixes(planned["skipped_files"])
    check("★★ 元测试：被跳过的后缀里**不得**有 .hda（它已经进台账了）",
          ".hda" not in _sk, f"被跳过的后缀={sorted(_sk)}")
    check("★★ 元测试：上面那条抓得到后缀（否则它是空转的）",
          _sk == {".cys"}, f"被跳过的后缀={sorted(_sk)}")
    # ⚠ 上面那条原来断言的是「每行都有 file_kind_code（满足 NOT NULL）」——
    #   v0.5 迁移 ⑨② 之后**它不再成立**：没码的行是合法的（NULL = 纬地自己没给码）。
    #   断言改红是对的：它钉住的正是一个被推翻的前提。
    check("★ 台账允许没码的行（NULL=纬地未给码），但**有码的行必须有码**",
          all(f["file_kind_code"] is None or f["file_kind_code"]
              for f in planned["tables"]["design_file"]))

    # ── 第 14b 组：台账 = .PRJ 声明的 ∪ 磁盘上实际存在的（v0.5 迁移 ⑨②）──────
    #   ★ 为什么要有这一组：`design_file` 原来只回答「.PRJ 里写了哪些文件」，
    #     不是「这个工程有哪些文件」。实测磁盘 20 个、库里 16 行 ——
    #     差的那几个（.dtm/.tsf/.prj）在库里**一个字都没有**，
    #     于是"这个工程有哪些文件？哪些我们还没处理？"这句话**答不出来**。
    print("\n第 14b 组  台账补上「磁盘上有、.PRJ 没声明」的文件")
    import tempfile as _tf
    with _tf.TemporaryDirectory() as _td2:
        # 造一个目录：放 .PRJ（已登记）+ 一个**没登记**的后缀（.cys，系统参数）
        for _n in ("x.PRJ", "x.cys", "x.dtm", "x.tsf"):
            (pathlib.Path(_td2) / _n).write_bytes(b"")
        _p3 = di.plan_project(po, project_dir=_td2)
    _rows3 = _p3["tables"]["design_file"]
    # ⚠ 只取**临时目录里造的那两个**（x.PRJ / x.dtm）。不能笼统取"所有没码的行"——
    #   2026-09-22 起 `.hda` 也是没码的行（它来自 .PRJ 声明，不是磁盘补行），
    #   混进来会让本组测的东西变味：本组验的是**磁盘补行**那条路。
    _null3 = [r for r in _rows3
              if r["file_kind_code"] is None and r["file_name"].startswith("x.")]
    # ⚠ 用**文件名**索引，不用下标：追加顺序是 sorted(_LEDGER_EXTRA_SUFFIX)
    #   （.dtm < .prj），写下标就会随登记表增删而错位 —— 我第一版就是这么错的，
    #   实测两次变红（期望 [x.PRJ, x.dtm] 实得 [x.dtm, x.PRJ]）。
    _by3 = {r["file_name"]: r for r in _null3}
    check("★ 磁盘上有、.PRJ 没声明的文件进了台账（file_kind_code=NULL）",
          sorted(_by3) == ["x.PRJ", "x.dtm", "x.tsf"],
          str([(r["file_kind_code"], r["file_name"]) for r in _null3]))
    check("★ .PRJ 那行 parse_status=ok（适配器已实现）、remark 写明来源",
          _by3.get("x.PRJ", {}).get("parse_status") == "ok"
          and "没有声明" in (_by3.get("x.PRJ", {}).get("remark") or ""),
          str(_by3.get("x.PRJ")))
    #   ⚠ .dtm 曾在这条里（当时它还没登记）—— 2026-09-22 用户定「加」，故移出。
    #     .cys **留着**：它是软件参数不是工程数据，收了会把台账弄脏。
    check("★★ 元测试：**没登记**的后缀（.cys 系统参数）**不得**被自动收进来"
          "（「见一个收一个」会把台账弄脏）",
          all(r["file_name"] != "x.cys" for r in _rows3),
          str([r["file_name"] for r in _rows3]))
    check("★ .dtm 已登记 → 必须被收，且记 blocked（二进制数模，读不了）",
          _by3.get("x.dtm", {}).get("parse_status") == "blocked"
          and _by3.get("x.dtm", {}).get("file_kind_name") == "数模文件(*.DTM)",
          str(_by3.get("x.dtm")))

    # ★★★ `.tsf` 土石方调配 —— 2026-09-22 用户定「甲 = 加」
    #   ⚠ 它原来在 `_BLOCKED_SUFFIX` 里，理由是「Microsoft Access 数据库，
    #     **需 ODBC/Jet 引擎**」。**那条理由是错的**：实测纯 Python
    #     （access-parser）就解出 20 张表，表名/列名**全是中文**，数据也全对
    #     （构造物：桥 273~333 m、隧道 930~1800 m）。
    #     → 「需专有引擎」不成立。它不是**结构上**读不了，只是**适配器还没写**。
    #     ⚠ 这与 `.dq` 是**同一个缺陷形状、方向相反**：.dq 被误判成"文本/pending"，
    #       .tsf 被误判成"读不了/blocked"。两次都是**没去读就下了结论**。
    check("★★ .tsf 已从 _BLOCKED_SUFFIX 移出（「需 ODBC/Jet 引擎」不成立）",
          ".tsf" not in di._BLOCKED_SUFFIX,
          str(sorted(di._BLOCKED_SUFFIX)))
    #   ★ 元测试：证明上面那条不是空洞的 —— 把它塞回去，状态**必须**跟着变。
    #     try/finally 复原，免得污染后面的用例。
    #   ⚠ 必须**另开**一个临时目录：上面那个 `_td2` 出了 `with` 就没了，
    #     再拿它去 plan_project 会扫不到任何文件，元测试会假绿/假红。
    #     （我第一版就是这么错的：_back 得到 []。）
    with _tf.TemporaryDirectory() as _td5:      # ⚠ 用 _tf（本组开头 import 的），
                                               #   _tf14 在下面那段才 import

        (pathlib.Path(_td5) / "x.tsf").write_bytes(b"")
        di._BLOCKED_SUFFIX[".tsf"] = "临时塞回去"
        try:
            _back = [r["parse_status"] for r in
                     di.plan_project(po, project_dir=_td5)["tables"]["design_file"]
                     if r["file_name"] == "x.tsf"]
        finally:
            di._BLOCKED_SUFFIX.pop(".tsf")
    check("★★ 元测试：把 .tsf 塞回 _BLOCKED_SUFFIX，它**必须**立刻变成 blocked"
          "（证明归类真的决定状态，上面那条不是空洞检查）",
          _back == ["blocked"], str(_back))
    check("★★ 且它在 _LEDGER_EXTRA_SUFFIX 里（是「磁盘补行」那条路，不是声明那条）",
          ".tsf" in di._LEDGER_EXTRA_SUFFIX)
    #   ★★ 元测试：证明上面那条不是空洞的 —— 把登记拿掉，行**必须**消失。
    #   元测试自己会复原登记（用 try/finally），免得污染后面的用例。
    _saved = di._LEDGER_EXTRA_SUFFIX.pop(".prj")
    try:
        _p5 = di.plan_project(po, project_dir=_td2)
        _gone = not any(r["file_kind_code"] is None and r["file_name"].startswith("x.")
                        for r in _p5["tables"]["design_file"])
    finally:
        di._LEDGER_EXTRA_SUFFIX[".prj"] = _saved
    check("★★ 元测试：把 .prj 从 _LEDGER_EXTRA_SUFFIX 拿掉后这一行**必须**消失"
          "（证明上面那条不是空洞检查）", _gone)
    # ★★ 非空转：没扫描目录时**不得**补行 —— 「没去看」≠「看了没有」
    _p4 = di.plan_project(po, project_dir=None)
    #   ⚠ 这里也只看 x.* —— `.hda` 是**来自 .PRJ 声明**的没码行，与"扫没扫磁盘"无关，
    #     拿它当反例就把两件事混了。
    check("★★ 不给 project_dir 时不补行（没扫描磁盘就补 = 编）",
          not any(r["file_kind_code"] is None and r["file_name"].startswith("x.")
                  for r in _p4["tables"]["design_file"]))

    # 已实现适配器的后缀才给 ok。加 .DMX/.ZDM 后从 3 个变 5 个 —— 这条断言当时
    # 变红是对的（它抓住了行为变化）。103/104 是不是 .DMX/.ZDM 已从库里核实：
    #   103 = 毕设.DMX         104 = 纵断面设计拟合.ZDM
    # 107 = .SUP、106 = .WID 均已从库里核实（design_file.file_kind_code ↔ 文件名）
    # 7 → 10：补进 .CTR(108)/.lj(110)/.tf(111) 三个**早已实现却漏登记**的后缀。
    # 10 → 11：.HDM(105) —— 它是**最后一个**"有源、可解析、只是适配器没写"的段，
    #          v0.5 K 节把适配器做掉了，于是它从 pending 变成 ok。
    # 这条断言变红是对的 —— 它又一次抓住了行为变化。
    check("parse_status 只对已实现适配器的后缀给 ok（实测 11 个）",
          sorted(f["file_kind_code"] for f in planned["tables"]["design_file"]
                 if f["parse_status"] == "ok")
          == ["101", "102", "103", "104", "105", "106", "107", "108", "109", "110", "111"],
          str([f["file_kind_code"] for f in planned["tables"]["design_file"]
               if f["parse_status"] == "ok"]))

    # ★★ 四态必须分得开 —— 把「读不了」和「还没写」混成一句，会让人去写一个
    #    永远写不出来的适配器（.gtm/.BDM/.HDMSJ 就是这种）。
    #
    #    ⚠ 这里必须**喂一个真目录**：上面那次 plan_project(project_dir=None)
    #    根本没看磁盘，此时报 absent 就是撒谎（「没去看」≠「看了没有」）。
    #    所以下面造一个临时目录，按真实后缀各放一个空文件 —— 测的是
    #    **分类逻辑**，不是文件内容。
    import tempfile
    with tempfile.TemporaryDirectory() as _td:
        for _suf in (".gtm", ".BDM", ".HDMSJ", ".HDM", ".dq"):
            (pathlib.Path(_td) / ("x" + _suf)).write_bytes(b"")
        _p2 = di.plan_project(po, project_dir=_td)
    _st = {f["file_kind_code"]: f["parse_status"]
           for f in _p2["tables"]["design_file"]}
    check("★ 二进制源记 blocked（.gtm 115 / .dq 114 / .BDM 120 / .HDMSJ 121）",
          (_st.get("115"), _st.get("114"), _st.get("120"), _st.get("121"))
          == ("blocked", "blocked", "blocked", "blocked"),
          str({k: _st.get(k) for k in ("115", "114", "120", "121")}))
    check("★ 文件不在磁盘上记 absent（.3DR 118）", _st.get("118") == "absent", str(_st.get("118")))
    # ⚠ .dq 一度被我按"文本"放在这一条里 —— **错了**（只看前 32 字节只看到魔数行）。
    #   实测第 2 行起是裸二进制，5 行正好 386 字节 → 定长记录的结构化二进制；
    #   纬地官方亦说明交换格式是 .dqd，「无需转为 dq 格式」。故它归 blocked，不归 pending。
    # ★★ .HDM(105) 从 pending 变 ok —— 四态里**最后一个 pending 被关掉了**。
    #    这条断言原先叫「有源、可解析、只是没写适配器的才记 pending（.HDM 105）」，
    #    它红的原因是**状态转移本身**，正是它该红的时候。
    check("★ .HDM(105) 适配器落地后记 ok（原为 pending：有源、可解析、只是没写）",
          _st.get("105") == "ok", str(_st.get("105")))
    # ★★ 四态里 pending 在本工程**清空了** —— 但这不是删断言的理由。
    #    四态的含义一个字没变，只是本工程每个声明的槽位现在都有归宿了：
    #      ok      = 适配器已实现（11 个）
    #      blocked = 存在，但结构上读不了（4 个：.gtm/.dq/.BDM/.HDMSJ）
    #      absent  = 去看过了，源里根本没有（1 个：.3DR）
    #      pending = 有源、可解析，只是适配器还没写（0 个）
    #    ⚠ 这条断言会红有两种原因，**必须分清楚**（和上面 L3→L4 那条同一个道理）：
    #      ① 有人加了个新槽位、还没来得及写适配器 → pending 是**对的**，改这条断言，
    #         并在注释里写明新槽位是什么；
    #      ② 有人加了个新槽位却**忘了归类**（既不在 _IMPLEMENTED_SUFFIX 也不在
    #         _BLOCKED_SUFFIX）→ 它会被静默当成 pending，看着像"还没做"，
    #         其实可能是"根本读不了"或"早就实现了"。这种情况**不许改断言**，
    #         要去把它归类。
    #    区分办法：看那个新后缀在 _IMPLEMENTED_SUFFIX / _BLOCKED_SUFFIX 里有没有。
    #    ★ 数一下这个临时目录场景下的真实分布（**不是**真工程的分布，别混）：
    #      临时目录里只放了 5 个空文件（.gtm/.BDM/.HDMSJ/.HDM/.dq），其余槽位磁盘上没有：
    #        ok      1  = 105（.HDM —— 唯一"存在且实现了"的）
    #        blocked 4  = 114(.dq) / 115(.gtm) / 120(.BDM) / 121(.HDMSJ)
    #        absent 11  = 其余 11 个槽位（含 118 .3DR）
    #        pending 1  = **.hda**（2026-09-22 起；见下）
    #      这比"真工程里的分布"更适合测四态：**同一轮里 ok/blocked/absent/pending
    #      四种含义当场分得开**。
    #
    #    ★ 2026-09-22 这条断言红了，属于上面写的**第 ① 种**（不是第 ② 种）：
    #      新槽位是 **`.hda` 涵洞数据文件**，它**故意**记 pending，不是忘了归类。
    #      它既不在 _IMPLEMENTED_SUFFIX（适配器没写）也不在 _BLOCKED_SUFFIX
    #      （它是**纯文本 GBK**，框架能解析，不是"结构上读不了"）——
    #      所以 `pending` 正是它该在的档。依据见 `_LEDGER_DECLARED_CODELESS`。
    #      四态至此**每一态都有代表**了，这条断言反而比原来更强。
    _pen = [f for f in _p2["tables"]["design_file"] if f["parse_status"] == "pending"]
    check("★★ 同一轮里四态分得开：ok 1 / blocked 4 / absent 11 / pending 1",
          (sum(1 for f in _p2["tables"]["design_file"] if f["parse_status"] == "ok"),
           sum(1 for f in _p2["tables"]["design_file"] if f["parse_status"] == "blocked"),
           sum(1 for f in _p2["tables"]["design_file"] if f["parse_status"] == "absent"),
           len(_pen)) == (1, 4, 11, 1),
          f"pending={[f['file_kind_code'] for f in _pen]}")
    check("★ pending 的那一个是 .hda（不是别的什么溜进来了）",
          [f["file_name"] for f in _pen] == ["毕设.hda"],
          str([f["file_name"] for f in _pen]))
    check("★★ .dq 必须记 blocked —— 它看着像文本，其实是定长二进制记录",
          _st.get("114") == "blocked", str(_st.get("114")))
    check("★ blocked 的 parse_note 必须写明实测依据（不是一句「读不了」）",
          all("不可解析" in f["parse_note"] for f in planned["tables"]["design_file"]
              if f["parse_status"] == "blocked"))

    # ★★ 钉住：**凡是 adapters/weidi 里 IMPLEMENTED 的段，其后缀必须在
    #    _IMPLEMENTED_SUFFIX 里**。这张表是手抄的，就会漂 —— .ctr/.tf/.lj
    #    就是这样漂成 pending 的。让测试来钉，而不是靠记性。
    # ★★ 值是**元组**不是单值：`.tsftxt` 一个文件出**两个段**（earthwork_factor +
    #    earthwork_transfer）。故这里必须**摊平**再比 —— 直接 `{v: k for k, v in …}`
    #    会把每个元组当成一个"段名"，于是**每个段都报漂了**（我改完泛化就撞上了这一条，
    #    是这条元测试自己把我拦下来的）。它拦得对：值变了，比对方式就得跟着变。
    _seg2suf = {seg: suf for suf, segs in di._IMPLEMENTED_SUFFIX.items() for seg in segs}
    _missing = sorted(seg for seg in weidi.IMPLEMENTED if seg not in _seg2suf)
    check("★★ 元测试：IMPLEMENTED 的每个段都在 _IMPLEMENTED_SUFFIX 里（表不会漂）",
          not _missing, f"漏了 {_missing}")
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
        SKIP[0] += 1
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
            # ★★★ NULLS NOT DISTINCT 的真库钉子（v0.5 迁移 ⑨②）
            #   上面那条幂等检查只覆盖"**有码**的 design_file 行"（synth 只有 101 一行，
            #   且没给 project_dir）。而这次改动引入的是"**没码**的行"，
            #   它的幂等**靠的是另一个机制**：PG 的 UNIQUE 默认把 NULL 当互不相等，
            #   于是 ON CONFLICT 对 (proj, NULL, name) **永不触发**。
            #   实测真踩过：重导一次就多插一行（16→17→18）。
            #   所以这里专门造一个"磁盘上有、.PRJ 没声明"的 .PRJ 文件，跑两遍，数行数。
            import tempfile as _tf14
            with _tf14.TemporaryDirectory() as _td14:
                (pathlib.Path(_td14) / "disk-only.PRJ").write_bytes(b"")
                _n0 = d14.scalar("SELECT count(*) FROM design_file WHERE design_project_id=%s",
                                 (r1["project_id"],))
                _a = di.ensure_project(sp, d14, project_dir=_td14)
                _n1 = d14.scalar("SELECT count(*) FROM design_file WHERE design_project_id=%s",
                                 (r1["project_id"],))
                _b = di.ensure_project(sp, d14, project_dir=_td14)
                _n2 = d14.scalar("SELECT count(*) FROM design_file WHERE design_project_id=%s",
                                 (r1["project_id"],))
            check("★★ 没码的行也进了台账（磁盘上有、.PRJ 没声明）",
                  _n1 == _n0 + 1, f"{_n0} → {_n1}")
            check("★★★ 元测试级钉子：重导**不得**多出没码的行"
                  "（NULLS NOT DISTINCT 一旦丢掉，这里必然 18）",
                  _n2 == _n1, f"{_n1} → {_n2}")
            check("★★ 且那行确实是 NULL 码（不是我们编了个号）",
                  d14.scalar("SELECT count(*) FROM design_file "
                             "WHERE design_project_id=%s AND file_kind_code IS NULL",
                             (r1["project_id"],)) == 1)
            check("★ 以 M5 身份建档案 → WriteGuardError（写只经 M2）",
                  _raises_wg(lambda: di.ensure_project(sp, d14, writer="M5")))

            # ★★★ earthwork_transfer 的**桩号越界检查**（用户选「乙」时我承诺的兜底）
            #   锚 section_id 的代价是：纬地的「分段编号」要映射成我们的 road_section.id。
            #   映射错了不会报错，只会把土方算到别的路段上 —— 所以必须有真检查。
            #   这里造一份 synth .tsftxt：synth .PRJ 的路段是 **100~500 m**，
            #   故 200 m 合法、900 m 越界。
            # ⚠ 不能用第 15 组的 _tsf_txt —— 那个在**后面**才赋值，
            #   而本组跑在前面（刚才就是 UnboundLocalError）。就地读夹具。
            _tsf_txt14, _ = base.read_text_any(
                FIXTURE.parent / "weidi_tsf_excerpt.tsftxt")
            _tf_hdr = next(_l for _l in _tsf_txt14.split("\n")
                           if _l.startswith("//[ GCID ]"))
            _tf_row = next(_l for _l in _tsf_txt14.split("\n") if _l.startswith("23\t"))

            # ★ 两张逐桩统计表的**全列清单**（73 / 46 列）—— 适配器要求一列不少，
            #   少给一列它就会拒绝（这正是「少收列 = 静默丢数据」的反面）。
            _HAUL_COLS = ['起始桩号', '终止桩号', '调土量松', '调石量松', '调松1', '调松2', '调松3', '调松4', '调松5', '调松6', '调1', '调2', '调3', '调4', '调5', '调6', '借土量松', '借石量松', '借松1', '借松2', '借松3', '借松4', '借松5', '借松6', '借1', '借2', '借3', '借4', '借5', '借6', '弃土量松', '弃石量松', '弃松1', '弃松2', '弃松3', '弃松4', '弃松5', '弃松6', '弃1', '弃2', '弃3', '弃4', '弃5', '弃6', '调出土量松', '调出石量松', '调出松1', '调出松2', '调出松3', '调出松4', '调出松5', '调出松6', '调出1', '调出2', '调出3', '调出4', '调出5', '调出6', '调入土量松', '调入石量松', '调入松1', '调入松2', '调入松3', '调入松4', '调入松5', '调入松6', '调入1', '调入2', '调入3', '调入4', '调入5', '调入6', '分段编号']
            _FILL_COLS = ['起始桩号', '终止桩号', '填方总量松', '填土量松', '填石量松', '填松1', '填松2', '填松3', '填松4', '填松5', '填松6', '填1', '填2', '填3', '填4', '填5', '填6', '利土量松', '利石量松', '利松1', '利松2', '利松3', '利松4', '利松5', '利松6', '利1', '利2', '利3', '利4', '利5', '利6', '缺土量松', '缺石量松', '缺松1', '缺松2', '缺松3', '缺松4', '缺松5', '缺松6', '缺1', '缺2', '缺3', '缺4', '缺5', '缺6', '分段编号']

            def _mk_stat_block(*, name, cols, seq, cut_s, cut_e):
                """造一张逐桩统计表的 `== TABLE` 块（表头一行 + 一行数据）。"""
                vals = []
                for c in cols:
                    if c == "起始桩号":
                        vals.append(repr(cut_s))
                    elif c == "终止桩号":
                        vals.append(repr(cut_e))
                    elif c == "分段编号":
                        vals.append(str(seq))
                    else:
                        vals.append("0.0")
                return ("== TABLE %s ==\n" % name
                        + "//" + "".join("[ %s ]" % c for c in cols) + "\n"
                        + "\t".join(vals) + "\n")


            def _mk_tsftxt(*, seq=1, cut_s=200.0, cut_e=200.0):
                """按真表头造一份最小 .tsftxt（4 张表各 1 行）。"""
                _c = _tf_row.split("\t")
                _c[0] = "1"                      # GCID
                _c[1] = repr(cut_s)              # 取土段S
                _c[2] = repr(cut_e)              # 取土段E
                _c[3] = repr(cut_s)              # 弃土段S
                _c[4] = repr(cut_e)              # 弃土段E
                _c[9] = "0"                      # 坑=0（路段内调运）
                _c[30] = str(seq)                # 分段编号
                # ⚠⚠ 每一行都必须带 `+`！第一版漏了 `+`，于是
                #   `return` 的字符串到一半就**结束**，后面几行变成了
                #   **独立的空语句** —— 语法合法、**不报错**，
                #   但两张坑表根本没进字符串。这就是「静默丢数据」。
                return ("HINTTF6.00_TSF_TXT_VER1\n"
                        + "== TABLE 土石系数 ==\n"
                        + "//[ 土方1 ][ 土方2 ][ 土方3 ][ 石方1 ][ 石方2 ][ 石方3 ]\n"
                        + "1.23\t1.16\t1.09\t0.92\t0.92\t0.92\n"
                        + "== TABLE 过程 ==\n" + _tf_hdr + "\n" + "\t".join(_c) + "\n"
                        + "== TABLE 取土坑 ==\n"
                        + "//[ 支线长度 ][ 松土 ][ 普通土 ][ 硬土 ][ 软石 ][ 次坚石 ]"
                        + "[ 坚石 ][ 土方总量 ][ 石方总量 ][ 上路桩号 ]"
                        + "[ 前经济分界点桩号 ][ 后经济分界点桩号 ]\n"
                        + "100.0\t20.0\t20.0\t20.0\t20.0\t20.0\t0.0\t"
                        + "99999892528.71431\t9999999999.0\t" + repr(cut_s) + "\t"
                        + repr(cut_s) + "\t" + repr(cut_s) + "\n"
                        + "== TABLE 弃土坑 ==\n"
                        + "//[ 支线长度 ][ 总容量 ][ 上路桩号 ]"
                        + "[ 前经济分界点桩号 ][ 后经济分界点桩号 ]\n"
                        + "100.0\t999999999999999.0\t" + repr(cut_s) + "\t"
                        + repr(cut_s) + "\t" + repr(cut_s) + "\n"
                            + _mk_stat_block(name="统计扩展", cols=_HAUL_COLS,
                                             seq=seq, cut_s=cut_s, cut_e=cut_e)
                            + _mk_stat_block(name="土方调配扩展记录", cols=_FILL_COLS,
                                             seq=seq, cut_s=cut_s, cut_e=cut_e))

            # ★★ 元测试：synth 里**必须真有这 4 张表**。
            #   第一版 _mk_tsftxt 漏了几个 `+`，于是 return 的字符串到一半就结束了，
            #   后面几行成了**独立的空语句** —— 语法合法、**不报错**，
            #   但两张坑表根本没进字符串。**这就是静默丢数据**，只有这条能拦住。
            _mk_tables = [l[len("== TABLE "):-3]
                          for l in _mk_tsftxt(cut_s=200.0, cut_e=300.0).split("\n")
                          if l.startswith("== TABLE ")]
            check("★★ 元测试：synth 里真有 6 张表（漏 `+` 会让字符串提前结束，静默丢掉后几张）",
                  _mk_tables == ["土石系数", "过程", "取土坑", "弃土坑",
                                 "统计扩展", "土方调配扩展记录"], str(_mk_tables))

            with _tf14.TemporaryDirectory() as _td14b:
                # ① 合法：200 m 落在 100~500 m 内 → 落库，且**桩号从米转成 km**
                (pathlib.Path(_td14b) / "ok.tsftxt").write_text(
                    _mk_tsftxt(cut_s=200.0, cut_e=300.0), encoding="utf-8")
                _before = d14.scalar("SELECT count(*) FROM earthwork_transfer "
                                     "WHERE section_id=%s", (sid,))
                di.ensure_project(sp, d14, project_dir=_td14b)
                _after = d14.scalar("SELECT count(*) FROM earthwork_transfer "
                                    "WHERE section_id=%s", (sid,))
                check("★★ 合法的调配行落库了（section_seq=1 → 该路段）",
                      _after == _before + 1, f"{_before} → {_after}")
                check("★★ 元测试：若这条没落库，下面的越界检查就是空转的",
                      _after > 0)
                _km = d14.scalar("SELECT cut_start_km FROM earthwork_transfer "
                                 "WHERE section_id=%s ORDER BY transfer_no DESC LIMIT 1", (sid,))
                check("★★ 桩号从**米**转成了 **km**（200 m → 0.2，不是 200）",
                      _km == Decimal("0.2"), str(_km))

                # ② 越界：900 m 在 500 m 之外 → LoadError，**且一行都不许写**
                _n0 = d14.scalar("SELECT count(*) FROM earthwork_transfer WHERE section_id=%s",
                                 (sid,))
                (pathlib.Path(_td14b) / "ok.tsftxt").write_text(
                    _mk_tsftxt(cut_s=900.0, cut_e=900.0), encoding="utf-8")
                _exc = None
                try:
                    di.ensure_project(sp, d14, project_dir=_td14b)
                except Exception as _e:                          # noqa: BLE001
                    _exc = _e
                check("★★★ 桩号越界 → LoadError（不是默默存进别的路段）",
                      isinstance(_exc, di.LoadError), repr(_exc))
                check("★★★ 越界时**一行都没写**（事务整体回滚，不是写一半）",
                      d14.scalar("SELECT count(*) FROM earthwork_transfer WHERE section_id=%s",
                                 (sid,)) == _n0)

                # ③ 分段编号对不上 → LoadError（synth 只有 1 个路段）
                (pathlib.Path(_td14b) / "ok.tsftxt").write_text(
                    _mk_tsftxt(seq=99, cut_s=200.0, cut_e=300.0), encoding="utf-8")
                _exc2 = None
                try:
                    di.ensure_project(sp, d14, project_dir=_td14b)
                except Exception as _e:                          # noqa: BLE001
                    _exc2 = _e
                check("★★★ 分段编号 99 对不上任何路段 → LoadError",
                      isinstance(_exc2, di.LoadError), repr(_exc2))

                # ④ 取土坑/弃土坑：锚 section_id，锚法是**桩号落在哪个路段**
                #    （这两张表源里没有「分段编号」—— 桩号就是唯一依据）
                # ⚠ 注意：第 ① 步的 ensure_project **已经把坑表插进去了**
                #   （四张表同一个 .tsftxt，一次全落）。
                #   我第一版在这里又读一次 _b0 然后等 "+1" ——
                #   而 ON CONFLICT(section_id, pit_no) 让重插**不新增行**，
                #   于是这条永远不成立。**是测试写错了，不是代码错了。**
                #   改成查**值**：只要行在、且值对，就说明这条链路通了。
                check("★★ 取土坑按**桩号落点**锚到该路段（200 m ∈ 100~500 m），且只有 1 行",
                      d14.scalar("SELECT count(*) FROM borrow_pit WHERE section_id=%s",
                                 (sid,)) == 1)
                check("★★ 弃土坑同上",
                      d14.scalar("SELECT count(*) FROM spoil_pit WHERE section_id=%s",
                                 (sid,)) == 1)
                check("★★ 元测试：两条若没落库，下面的越界检查就是空转的",
                      d14.scalar("SELECT count(*) FROM borrow_pit WHERE section_id=%s",
                                 (sid,)) > 0)
                _bk = d14.scalar("SELECT access_station_km FROM borrow_pit WHERE section_id=%s "
                                 "ORDER BY pit_no DESC LIMIT 1", (sid,))
                check("★★ 桩号从**米**转成 **km**，且**键名**也换了（200 m → 0.2）",
                      _bk == Decimal("0.2"), str(_bk))
                # ★★ 占位符**原样存**（不是 NULL，也不是「看起来合理」的数）。
                #   ⚠ 列是 numeric(20,4)，故存进去会**舍入到 4 位小数**：
                #     99999892528.71431 → 99999892528.7143。我第一版拿原值比，差一位。
                #   故这里断言**哨兵形状**（≈1e11）而不是逐位相等 ——
                #   这也正是设计意图：它本来就是「无限」，不是一个精确的方量。
                _soil = d14.scalar("SELECT soil_total_m3 FROM borrow_pit WHERE section_id=%s "
                                   "ORDER BY pit_no DESC LIMIT 1", (sid,))
                check("★★ 占位符**原样存**（不是 NULL，也不是「看起来合理」的数）",
                      _soil is not None and _soil > Decimal("1e10"),
                      "%s —— 替源文件做主（改 NULL 或改小）都是错的" % _soil)
                check("★★ 且它确实被 numeric(20,4) 舍入到 4 位小数（证明列宽够）",
                      _soil == Decimal("99999892528.7143"), str(_soil))
                check("★★ 弃土坑 1e15 的「总容量」也存得下（numeric(20,4)，全库最宽）",
                      d14.scalar("SELECT capacity_m3 FROM spoil_pit WHERE section_id=%s "
                                 "ORDER BY pit_no DESC LIMIT 1", (sid,))
                      == Decimal("999999999999999.0"))
                check("★ pct_6 是出厂默认 0（六项和 = 100）",
                      d14.scalar("SELECT pct_6 FROM borrow_pit WHERE section_id=%s "
                                 "ORDER BY pit_no DESC LIMIT 1", (sid,)) == Decimal("0"))

                # ⑤ 坑的桩号落在所有路段之外 → LoadError
                #   ⚠ 这里 900 m 会让 **transfer 先抛**（它的桩号校验在前），
                #     所以这条实际验的是 transfer。坑表自己的越界路径由 ⑥ 覆盖。
                (pathlib.Path(_td14b) / "ok.tsftxt").write_text(
                    _mk_tsftxt(cut_s=900.0, cut_e=900.0), encoding="utf-8")
                _exc3 = None
                try:
                    di.ensure_project(sp, d14, project_dir=_td14b)
                except Exception as _e:                          # noqa: BLE001
                    _exc3 = _e
                check("★★★ 坑的桩号不落在任何路段 → LoadError（桩号定不了路段，不猜）",
                      isinstance(_exc3, di.LoadError), repr(_exc3))

        finally:
            # 按 FK 反序清干净
            uid = f"{uniq}-uid"
            with d14.write_txn(writer="M2") as tx:      # FK 反序，同成同败
                tx.execute("design_project",
                           "DELETE FROM earthwork_haul_stat WHERE section_id IN "
                           "(SELECT s.id FROM road_section s JOIN design_project p "
                           " ON s.design_project_id = p.id WHERE p.project_uid=%(u)s)", {"u": uid})
                tx.execute("design_project",
                           "DELETE FROM earthwork_fill_stat WHERE section_id IN "
                           "(SELECT s.id FROM road_section s JOIN design_project p "
                           " ON s.design_project_id = p.id WHERE p.project_uid=%(u)s)", {"u": uid})
                tx.execute("design_project",
                           "DELETE FROM borrow_pit WHERE section_id IN "
                           "(SELECT s.id FROM road_section s JOIN design_project p "
                           " ON s.design_project_id = p.id WHERE p.project_uid=%(u)s)", {"u": uid})
                tx.execute("design_project",
                           "DELETE FROM spoil_pit WHERE section_id IN "
                           "(SELECT s.id FROM road_section s JOIN design_project p "
                           " ON s.design_project_id = p.id WHERE p.project_uid=%(u)s)", {"u": uid})
                tx.execute("design_project",
                           "DELETE FROM earthwork_factor WHERE design_project_id IN "
                           "(SELECT id FROM design_project WHERE project_uid=%(u)s)", {"u": uid})
                tx.execute("design_project",
                           "DELETE FROM earthwork_transfer WHERE section_id IN "
                           "(SELECT s.id FROM road_section s JOIN design_project p "
                           " ON s.design_project_id = p.id WHERE p.project_uid=%(u)s)", {"u": uid})
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

    # ── 第 15 组：.tsf 土石方调配（适配器吃的是**转换文本** .tsftxt）──────────
    print("\n第 15 组  .tsf 土石方调配（纬地 HintTF；适配器吃的是转换文本 .tsftxt）")
    # ★★ 本组要钉住的第一件事：**这个适配器的输入不是厂商原始文件**。
    #   .tsf 是 Microsoft Access / Jet 4 二进制，而 M2 的适配器契约是 parse(text)、
    #   零第三方依赖。故先经 tools/tsf2txt.py 摊成 .tsftxt，适配器读那个。
    #   把这件事钉住，是为了防止将来有人「顺手」让适配器去吃 .tsf ——
    #   那会让 M2 的依赖集出现分叉，而契约⑤ 整个设计建立在「只认文本」之上。
    _tsf_fix = FIXTURE.parent / "weidi_tsf_excerpt.tsftxt"
    check("★ 夹具存在（转换文本，不是 .tsf 二进制）", _tsf_fix.is_file())
    _tsf_txt = _tsf_fix.read_text(encoding="utf-8")
    check("★ 夹具首行是**转换器**的魔数（不是纬地的）",
          _tsf_txt.splitlines()[0] == "HINTTF6.00_TSF_TXT_VER1",
          repr(_tsf_txt.splitlines()[0]))
    check("应通过：detect 认自家魔数", tsf_mod.detect(_tsf_txt))
    check("应通过：detect 不认纬地 .tf 原文（两者魔数不同，不能互相误认）",
          not tsf_mod.detect("HINTCAD6.00_TF_SHUJU\n//[ 桩号 ]\n0.0"))
    _tf_out = tsf_mod.parse(_tsf_txt, file="x.tsftxt")
    check("厂商版本从魔数取出", _tf_out["vendor_version"] == "6.00",
          _tf_out["vendor_version"])
    check("表名 = 土石系数", _tf_out["table"] == "土石系数", _tf_out["table"])
    _EXPECT_F = {"factor_soil_1": 1.23, "factor_soil_2": 1.16, "factor_soil_3": 1.09,
                 "factor_rock_1": 0.92, "factor_rock_2": 0.92, "factor_rock_3": 0.92}
    _tf_row = _tf_out[tsf_mod.PAYLOAD_KEY][0]
    check("★ 六类系数逐值（土方 1.23/1.16/1.09、石方 0.92×3）",
          _tf_row == _EXPECT_F, str(_tf_row))
    check("段名与文件类别与 SEGMENT_FILES 登记一致",
          tsf_mod.SEGMENT == "earthwork_factor"
          and weidi.SEGMENT_FILES[tsf_mod.SEGMENT][0].lower() == ".tsftxt",
          "%s / %s" % (tsf_mod.SEGMENT, weidi.SEGMENT_FILES[tsf_mod.SEGMENT]))

    # ── 应拒绝侧：7 例，全部必须抛 SourceInvalid ──────────────────────────
    # ⚠ parser=tsf_mod **必须显式传**：check_raises 默认拿 .STA 的解析器，
    #   那样每个用例都会「恰好」因为魔数不符被拒，看起来全绿，
    #   实际**一条也没走到本适配器自己的校验逻辑上**（第 1 组踩过这个坑）。
    for _nm, _bad in [
        ("魔数不是这个格式", _tsf_txt.replace("_TSF_TXT_VER1", "_BAD_SUFFIX")),
        ("转换格式版本不认识", _tsf_txt.replace("_VER1", "_VER2")),
        ("要的表不在文件里", _tsf_txt.replace("土石系数", "别的表")),
        ("出现未知列", _tsf_txt.replace("[ 土方1 ]", "[ 土方X ]")),
        ("缺列", _tsf_txt.replace("[ 土方1 ]", "")),
        ("值不是合法数字", _tsf_txt.replace("1.23", "abc")),
        ("字段数不符", _tsf_txt.replace("1.23\t1.16\t1.09\t0.92\t0.92\t0.92",
                                        "1.23\t1.16")),
    ]:
        check_raises("应拒绝：" + _nm, _bad, parser=tsf_mod)

    # ── _plan_earthwork_factor：工程级落库的出入口 ─────────────────────────
    _prj_txt, _ = base.read_text_any(FIXTURE.parent / "weidi_prj_excerpt.PRJ")
    _prj_out = prj_mod.parse(_prj_txt, file="x.PRJ")
    with tempfile.TemporaryDirectory() as _td15:
        check("无 .tsftxt 的目录 → 空列表（缺 .tsf 是**正常**的，不报错）",
              di.plan_project(_prj_out, project_dir=_td15)["tables"]["earthwork_factor"]
              == [])
    check("project_dir=None → 空列表",
          di.plan_project(_prj_out)["tables"]["earthwork_factor"] == [])
    with tempfile.TemporaryDirectory() as _td15b:
        shutil.copy(_tsf_fix, Path(_td15b) / "a.tsftxt")
        _p15 = di.plan_project(_prj_out, project_dir=_td15b)["tables"]["earthwork_factor"]
        check("★ 有 .tsftxt → 出 1 行，且**不含** design_project_id（由事务补）",
              len(_p15) == 1 and "design_project_id" not in _p15[0], str(_p15))
        check("★ 出行的六类系数与源一致",
              all(_p15[0][k] == v for k, v in _EXPECT_F.items()), str(_p15[0]))
        check("★ 出行带 remark 说明来源", "tsftxt" in (_p15[0].get("remark") or ""),
              str(_p15[0].get("remark")))
    with tempfile.TemporaryDirectory() as _td15c:
        shutil.copy(_tsf_fix, Path(_td15c) / "a.tsftxt")
        shutil.copy(_tsf_fix, Path(_td15c) / "b.tsftxt")
        check("应拒绝：目录里有 2 个 .tsftxt（两套工程混了，不能默默取第一个）",
              _raises(lambda: di.plan_project(_prj_out, project_dir=_td15c)))
    with tempfile.TemporaryDirectory() as _td15d:
        # 源里 2 行 → 必须拒绝（本表 UNIQUE(design_project_id)，多行必然冲突）
        _two = _tsf_txt.rstrip("\n") + "\n1.00\t1.00\t1.00\t1.00\t1.00\t1.00\n"
        (Path(_td15d) / "a.tsftxt").write_text(_two, encoding="utf-8")
        check("应拒绝：源里 2 行系数（本表 UNIQUE(design_project_id)，取第一行=静默丢数据）",
              _raises(lambda: di.plan_project(_prj_out, project_dir=_td15d)))
    with tempfile.TemporaryDirectory() as _td15e:
        (Path(_td15e) / "a.tsftxt").write_text("不是这个格式\n", encoding="utf-8")
        check("应拒绝：有 .tsftxt 但魔数不对（**与「没有文件」必须区分开**）",
              _raises(lambda: di.plan_project(_prj_out, project_dir=_td15e)))

    # ── 段 earthwork_transfer（.tsf「过程」）──────────────────────────────
    #   ★ 与 earthwork_factor 同一个文件、同一个后缀，**第二个段**。
    #     这条本身就是被测项：证明"一个文件出多段"是支持的（见下面那条元测试）。
    print("\n第 15b 组  .tsf「过程」→ earthwork_transfer（调配过程）")
    _tf_out = tsf_transfer_mod.parse(_tsf_txt, file="x.tsftxt")
    check("解析 .tsftxt 的「过程」表 → 3 行（夹具切的是 GCID 1/23/24）",
          len(_tf_out["rows"]) == 3, str(len(_tf_out["rows"])))
    check("★ 每行 31 个字段，一个不省（源 31 列全落）",
          all(len(r) == 31 for r in _tf_out["rows"]),
          str(sorted(len(r) for r in _tf_out["rows"])))
    check("★ transfer_no / section_seq 是 **int**（schema 声明 integer，不是 number）",
          all(isinstance(r["transfer_no"], int) and isinstance(r["section_seq"], int)
              for r in _tf_out["rows"]),
          str([type(r["transfer_no"]).__name__ for r in _tf_out["rows"]]))
    check("★★ `坑` 不是「坑的类型」，是「土从哪来」：0=路段内调运 1=取土坑取土",
          [r["source_kind"] for r in _tf_out["rows"]] == [0, 1, 1],
          str([r["source_kind"] for r in _tf_out["rows"]]))
    check("★★ source_kind=1 时取土段起止**相等**（取土坑退化成一个点）",
          all(r["cut_start_m"] == r["cut_end_m"]
              for r in _tf_out["rows"] if r["source_kind"] == 1),
          str([(r["cut_start_m"], r["cut_end_m"]) for r in _tf_out["rows"]
               if r["source_kind"] == 1]))
    check("★★ 元测试：source_kind=0 的行取土段**不**退化（否则这条判据是空转的）",
          all(r["cut_start_m"] != r["cut_end_m"]
              for r in _tf_out["rows"] if r["source_kind"] == 0),
          "若这条也过了，说明夹具里根本没有 source_kind=0 的行，上一条判据等于没测")
    check("★★ 取土坑那个点 == 4100.000 m（与 取土坑.上路桩号 完全一致）",
          all(r["cut_start_m"] == 4100.0
              for r in _tf_out["rows"] if r["source_kind"] == 1),
          str([r["cut_start_m"] for r in _tf_out["rows"] if r["source_kind"] == 1]))
    check("★ 桩号在 IR 里是**米**（与其余适配器一致），不是 km",
          all(r["cut_start_m"] > 100 for r in _tf_out["rows"]),
          "落库时才 ÷1000 转 km —— 若这里已是 km，说明单位搞反了")
    check("★ 六分类列齐全（用土1/2/3 + 用石4/5/6 → used_class_1..6_m3）",
          all(f"used_class_{n}_m3" in _tf_out["rows"][0] for n in range(1, 7)))
    check("★ 六类运距列齐全（土方1/2/3运距 + 石方4/5/6运距）",
          all(f"haul_class_{n}_m" in _tf_out["rows"][0] for n in range(1, 7)))

    _bad_cases = [
        ("缺「过程」表", _tsf_txt.split("== TABLE 过程 ==")[0]),
        ("未知列", _tsf_txt.replace("][ 坑 ]", "][ 谁 ]")),
        # ⚠ 下面两条第一版写的是 "1\t5800.000" —— 真值是 "5800.0"，
        #   于是 replace **没匹配上**，坏样本 == 好样本，两条"应拒绝"全都**空转通过**。
        #   是测试自己把这件事报出来的（"本应拒绝却通过了"）。见下面那条元测试。
        ("GCID 不是整数", _tsf_txt.replace("1\t5800.0\t", "1.5\t5800.0\t")),
        ("字段数不符（过程表）",
         _tsf_txt.replace("5800.0\t5805.421\t5775.481", "5800.0\t5775.481")),
    ]
    # ★★ 元测试：每个"坏样本"**必须真的与好样本不同**。
    #   否则 replace 打空 → 坏样本就是好样本 → 那条"应拒绝"永远不会失败。
    #   「一个永远不会失败的检查，比没有检查更糟」。
    _vacu = [nm for nm, bad in _bad_cases if bad == _tsf_txt]
    check("★★ 元测试：每个坏样本都真的与好样本不同（否则那条应拒绝是空转的）",
          not _vacu, f"这几个 replace 打空了：{_vacu}")
    for _nm, _bad in _bad_cases:
        check_raises("应拒绝：" + _nm, _bad, parser=tsf_transfer_mod)

    # ── 段 borrow_pit / spoil_pit（.tsf「取土坑」「弃土坑」）──────────────────
    #   ★★ 这两张表**几乎全是出厂默认值**，真值只有桩号 —— 断言就钉这一点。
    print("\n第 15c 组  .tsf「取土坑」「弃土坑」→ borrow_pit / spoil_pit")
    _bp = tsf_borrow_mod.parse(_tsf_txt, file="x.tsftxt")["rows"][0]
    _sp = tsf_spoil_mod.parse(_tsf_txt, file="x.tsftxt")["rows"][0]
    check("取土坑 12 列 / 弃土坑 5 列，一个不省",
          len(_bp) == 12 and len(_sp) == 5, "%d / %d" % (len(_bp), len(_sp)))
    check("★★ 真值：取土坑上路桩号 = 4100.000 m（与 transfer 的 source_kind=1 一致）",
          _bp["access_station_m"] == 4100.0, str(_bp["access_station_m"]))
    check("★★ 真值：弃土坑上路桩号 = 1900 m（**没有任何 transfer 行指向它**）",
          _sp["access_station_m"] == 1900.0, str(_sp["access_station_m"]))
    check("★★ 出厂默认：支线长度 100.0（两张表同值）",
          _bp["access_road_length_m"] == 100.0 and _sp["access_road_length_m"] == 100.0)
    check("★★ 出厂默认：pct_1..6 = 20/20/20/20/20/0，**六项和恰为 100**（是比例不是方量）",
          [_bp["pct_%d" % k] for k in range(1, 7)] == [20.0] * 5 + [0.0]
          and sum(_bp["pct_%d" % k] for k in range(1, 7)) == 100.0,
          str([_bp["pct_%d" % k] for k in range(1, 7)]))
    check("★★★ 元测试：那个 20/20/20/20/20/0 **不是**喂进算法的参数 —— "
          "transfer 里 坑=1 的实际组成是 33.3/33.3/33.3",
          all(abs(r["used_class_%d_m3" % k] / r["used_soil_m3"] - 1 / 3) < 1e-6
              for r in _tf_out["rows"] if r["source_kind"] == 1 for k in (1, 2, 3)),
          "若这条变了，说明纬地改了算法，本表的注释要跟着改")
    check("★★ 占位符：土方总量 ≈1e11（不是容量）",
          _bp["soil_total_m3"] > 1e10, str(_bp["soil_total_m3"]))
    check("★★ 占位符：石方总量 ≈1e10",
          _bp["rock_total_m3"] > 1e9, str(_bp["rock_total_m3"]))
    check("★★★ 占位符：弃土坑总容量 ≈1e15 —— 需要 numeric(20,4)，全库最宽",
          _sp["capacity_m3"] > 1e14, str(_sp["capacity_m3"]))
    for _nm, _bad in [
        ("缺「取土坑」表", _tsf_txt.split("== TABLE 取土坑 ==")[0]),
        ("缺「弃土坑」表", _tsf_txt.split("== TABLE 弃土坑 ==")[0]),
        ("取土坑未知列", _tsf_txt.replace("][ 松土 ]", "][ 啥土 ]")),
        ("取土坑字段数不符",
         _tsf_txt.replace("100.0\t20.0\t20.0\t20.0\t20.0\t20.0\t0.0\t",
                          "100.0\t20.0\t20.0\t20.0\t20.0\t20.0\t")),
    ]:
        check_raises("应拒绝：" + _nm, _bad,
                     parser=tsf_borrow_mod if "取土坑" in _nm else tsf_spoil_mod)

    # ── 段 earthwork_haul_stat / earthwork_fill_stat（.tsf O 节）──────────────
    #   ★★ 这两张是**逐桩**统计（334 段），锚法与 transfer 相同（用 分段编号）。
    #      实测的**闭合关系**就钉在这里 —— 它们当初正是「收」的理由。
    print("\n第 15d 组  .tsf「统计扩展」「土方调配扩展记录」→ 两张逐桩统计表")
    _hs = tsf_haul_mod.parse(_tsf_txt, file="x.tsftxt")["rows"]
    _fs = tsf_fill_mod.parse(_tsf_txt, file="x.tsftxt")["rows"]
    check("★★ 列数 73 / 46，一列不少（少收列 = 静默丢数据）",
          len(_hs[0]) == 73 and len(_fs[0]) == 46,
          "%d / %d" % (len(_hs[0]), len(_fs[0])))
    check("★★ 两表桩号区间**逐行完全相同**（同一套 334 段）",
          [(r["start_station_m"], r["end_station_m"]) for r in _hs]
          == [(r["start_station_m"], r["end_station_m"]) for r in _fs])
    check("★★ 但**列完全不重叠**（是两个维度，不是互相投影）",
          not (set(_hs[0]) & set(_fs[0]) - {"start_station_m", "end_station_m", "section_seq"}),
          str(sorted(set(_hs[0]) & set(_fs[0]))))
    check("★★★ 恒等式 填方总量松 == 利土量松 + 缺土量松（**逐行**）",
          all(abs(r["fill_total_loose_m3"]
                  - (r["utilize_soil_loose_m3"] + r["deficit_soil_loose_m3"])) < 1e-6
              for r in _fs),
          "本工程实测 334 行 0 例外")
    check("★★★ 总量闭合：统计扩展的 调+借 == 调配扩展的 缺（松方 m³）",
          abs(sum(r["haul_soil_loose_m3"] + r["borrow_soil_loose_m3"] for r in _hs)
              - sum(r["deficit_soil_loose_m3"] for r in _fs)) < 1e-6,
          "实测两侧都等于 568907.6536")
    check("★★ 出厂全 0 的整组列**照样建了**（本工程没用上 ≠ 这一列不存在）",
          all(_hs[0][k] == 0 for k in ("spoil_soil_loose_m3", "haul_out_soil_loose_m3",
                                       "haul_in_soil_loose_m3"))
          and all(_fs[0][k] == 0 for k in ("fill_rock_loose_m3",)),
          "弃 / 调出 / 调入 三组与石方全 0，但列都在")
    check("★ 分段编号是整数（源里是整数，不该变成 float）",
          all(isinstance(r["section_seq"], int) for r in _hs + _fs))
    for _nm, _bad, _mod in [
        ("缺「统计扩展」表", _tsf_txt.split("== TABLE 统计扩展 ==")[0], tsf_haul_mod),
        ("缺「土方调配扩展记录」表", _tsf_txt.split("== TABLE 土方调配扩展记录 ==")[0],
         tsf_fill_mod),
        ("统计扩展未知列", _tsf_txt.replace("][ 调松1 ]", "][ 啥松1 ]"), tsf_haul_mod),
        # ⚠ 原有一条「字段数不符」用的是 `100.0\t20.0` —— 那串**匹配到的是取土坑**，
        #   不是统计扩展，所以那条「应拒绝」测的根本是别处（反空转元测试当场揭穿）。
        #   统计扩展的字段数检查与列检查走同一条路，去掉这条冗余项。
        ("统计扩展某列不是数字",
         _tsf_txt.replace("\t0.0\t0.0\n", "\tabc\t0.0\n", 1), tsf_haul_mod),
    ]:
        # ★★ 反空转：坏样本**必须真的与好样本不同**。
        #   上一版这里有一条 `… if False else None` + `continue` —— 那是个
        #   **永远不会执行的检查**，比没有检查更糟。现在每条都真跑。
        check("★★ 元测试：坏样本确实与好样本不同（「" + _nm + "」）",
              _bad != _tsf_txt, "一样的话这条应拒绝就是空转的")
        check_raises("应拒绝：" + _nm, _bad, parser=_mod)

    # ── _plan_earthwork_transfer：锚 section_id 的出入口 ────────────────────
    with tempfile.TemporaryDirectory() as _td15f:
        check("无 .tsftxt 的目录 → transfer 也是空列表",
              di.plan_project(_prj_out, project_dir=_td15f)["tables"]["earthwork_transfer"]
              == [])
    with tempfile.TemporaryDirectory() as _td15g:
        shutil.copy(_tsf_fix, Path(_td15g) / "a.tsftxt")
        _t15 = di.plan_project(_prj_out, project_dir=_td15g)["tables"]
        check("★ 有 .tsftxt → transfer 出 3 行（不是 1 行 —— 与 factor 不同）",
              len(_t15["earthwork_transfer"]) == 3, str(len(_t15["earthwork_transfer"])))
        check("★★ transfer 行**不含** section_id（由 ensure_project 按 section_seq 映射）",
              all("section_id" not in r for r in _t15["earthwork_transfer"]))
        check("★★ 同一个文件同时出两个段：factor 1 行 + transfer 3 行",
              len(_t15["earthwork_factor"]) == 1
              and len(_t15["earthwork_transfer"]) == 3,
              "factor=%d transfer=%d" % (len(_t15["earthwork_factor"]),
                                         len(_t15["earthwork_transfer"])))
        check("★ 出行带 remark 说明来源",
              all("tsftxt" in (r.get("remark") or "")
                  for r in _t15["earthwork_transfer"]))
    with tempfile.TemporaryDirectory() as _td15h:
        # ★ 只有 factor 没有 过程 → 必须拒绝，不能"有一张算一张"
        _only_f = _tsf_txt.split("== TABLE 过程 ==")[0]
        (Path(_td15h) / "a.tsftxt").write_text(_only_f, encoding="utf-8")
        check("应拒绝：.tsftxt 里有系数没过程（**不能只导一半还当成功**）",
              _raises(lambda: di.plan_project(_prj_out, project_dir=_td15h)))


    # ── 分工：工程级 vs 路段级 ────────────────────────────────────────────
    check("★ plan() 的表里**没有** earthwork_factor（按路段那步不该管工程级数据）",
          "earthwork_factor" not in di.plan({"segments": {}}, section_id=1)["tables"])
    check("★ ARCHIVE_TABLES 里有它（与 design_project 同组）",
          "earthwork_factor" in di.ARCHIVE_TABLES, str(di.ARCHIVE_TABLES))
    check("★ _IMPLEMENTED_SUFFIX 认 .tsftxt（**不是** .tsf —— 二进制确实还导不进去）",
          di._IMPLEMENTED_SUFFIX.get(".tsftxt")
          == ("earthwork_factor", "earthwork_transfer",
              "borrow_pit", "spoil_pit",
              "earthwork_haul_stat", "earthwork_fill_stat")
          and ".tsf" not in di._IMPLEMENTED_SUFFIX,
          ".tsftxt=%s / .tsf 在不在=%s" % (di._IMPLEMENTED_SUFFIX.get(".tsftxt"),
                                          ".tsf" in di._IMPLEMENTED_SUFFIX))

    # ── ★★ 元测试：证明上面那些检查**不是摆设** ──────────────────────────
    # 「一个永远不会失败的检查，比没有检查更糟」。下面每条都先**制造**一个
    # 应该被抓到的缺陷，确认检查**真的会红** —— 而不是只跑一遍绿灯。
    check("元测试：把夹具魔数改坏后 detect **必须**变 False",
          not tsf_mod.detect(_tsf_txt.replace("HINTTF", "HINTTX")))
    _mt = tsf_mod.parse(_tsf_txt, file="x")[tsf_mod.PAYLOAD_KEY][0]
    check("元测试：把 1.23 改成 9.99 后，逐值断言**必须**对不上",
          dict(_mt, factor_soil_1=9.99) != _EXPECT_F)
    check("元测试：拿 .STA 的解析器去解析 .tsftxt **必须**失败"
          "（证明上面 parser=tsf_mod 不是多余的）",
          _raises(lambda: sta.parse(_tsf_txt, file="x")))
    check("元测试：夹具目录（只有 1 个 .tsftxt）不该被「多个文件」那条拦下 —— "
          "否则那条检查会变成「永远都在报错」",
          di.plan_project(_prj_out, project_dir=str(FIXTURE.parent))["tables"]["earthwork_factor"]
          != [])

    print("\n" + "=" * 74)
    print(f"通过 {PASS} ｜ 失败 {FAIL} ｜ 跳过 {SKIP[0]}")
    if SKIP[0]:
        # ★★ 跳过必须显形。原因：契约⑤ 的**第 12 组（落库器端到端）**依赖 psycopg，
        #   而 `run_contract_tests.sh` 给本套件的依赖集里**没有** psycopg ——
        #   于是那 25 条断言（含"批次备注必须写明导入当时"）在总运行器下**从未执行**，
        #   而汇总只说"通过 489 ｜ 失败 0"，读起来和全绿一模一样。
        #   这不是"跳过就等于没问题"：**空转不是通过**。
        #   正确做法是给本套件补上 psycopg（已同步改 run_contract_tests.sh）；
        #   这一行是兜底 —— 万一将来又在别的环境下缺依赖，得能一眼看见。
        print(f"⚠ 有 {SKIP[0]} 处因环境不具备而**跳过** —— 跳过不等于通过，请确认这不是漏装依赖")
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
