"""契约②：**DDL ↔ 数据字典 ↔ catalog 三处表数一致性**契约测试。

运行（**完全离线，不触库**）：
    cd /data/cy/shujuku/scaffold
    python3 tests/contract/test_ddl_dict_catalog.py

为什么需要这个测试
------------------------------------------------------------------------------
同一件事（"库里有哪几张表"）写在**三个地方**：

    1. ``scaffold/sql/10_ddl_v0.3.sql``   ← 建表的真源（契约②）
    2. ``scaffold/sql/数据字典-v0.3.md``   ← 给人看的真源

    ⚠ 这两份 2026-09-15 从 ``output/`` 搬来 ``scaffold/sql/``：它们**不是交付物
      而是契约**——compose 要挂载 DDL 建库、本测试要读它比对，必须随代码走。
      其余 ``output/``（报告、图件）与 ``docpipe/`` 已移出代码仓库。
    3. ``scaffold/modules/M3-rpdao/catalog.py``        ← 代码用的真源

三处各自都是"真源"，于是**天生有互相打脸的风险**：改了 DDL 忘了改字典、
加了表没登记进 catalog、或者图上还写着旧表数。历史上本项目的图件就出现过
"图 E 改了阶段条、底部注记还在复述旧日期"这种**同一份产物内部自相矛盾**。

所以这里把三个数字钉成一条可执行断言：**任何一处单独改动，测试立刻失败**。
这比"评审时提醒大家注意同步"可靠得多。

同时校验：新表（v0.2 的 quality_rule / mapping_set）的**关键约束确实存在于 DDL 文本**里——
因为这两张表的价值主要在那几条约束上，表建出来而约束漏掉，等于白建。
"""
from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT / "modules" / "M3-rpdao") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules" / "M3-rpdao"))

DDL_PATH = ROOT / "sql" / "10_ddl_v0.3.sql"
DICT_PATH = ROOT / "sql" / "数据字典-v0.3.md"

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


def ddl_tables() -> set[str]:
    """从 DDL 文本抽出所有 ``CREATE TABLE IF NOT EXISTS <name>``。

    刻意用**文本解析**而不是连库查 ``pg_tables``：契约测试必须能离线跑，
    且要能在库还没建起来时就发现"DDL 与字典不一致"。
    """
    if not DDL_PATH.exists():
        return set()
    txt = DDL_PATH.read_text(encoding="utf-8")
    return set(re.findall(r"CREATE TABLE IF NOT EXISTS\s+([a-z_][a-z0-9_]*)", txt))


# 数据字典用**两种**方式列一张表，解析必须两种都认：
#   (a) 章节标题      ``### C1. wim_axle_record 车辆轴载记录 ⭐分区表（按月）``
#   (b) 汇总表的行    ``| test_project | 试验项目：… |``，且其表头首格必须是「表」
#
# ★ 关键：**不能**把所有 markdown 表格的首列都当表名 —— 字典里还有大量
#   ``| 字段 | 类型 | 说明 |`` 的**逐字段明细表**。第一版解析器就栽在这里：
#   它把 id / axle_num / plate_no 等 30 个**字段名**当成了表名，于是
#   "DDL 32 张 vs 字典 44 张"报不一致 —— **是解析器错了，不是字典错了**。
#   判据是表头首格：``表`` = 表清单，``字段`` = 字段明细，``库`` = 库分工。
_HEADING_RE = re.compile(r"^#{2,4}\s+[A-Z][0-9]*\.\s+([a-z_][a-z0-9_]*)\b")


def dict_tables() -> set[str]:
    """从数据字典抽出全部表名（章节标题 ＋ 表头首格为「表」的汇总表行）。"""
    if not DICT_PATH.exists():
        return set()
    names: set[str] = set()
    in_table_list = False
    for line in DICT_PATH.read_text(encoding="utf-8").splitlines():
        m = _HEADING_RE.match(line)
        if m:
            names.add(m.group(1))
            in_table_list = False
            continue
        if line.startswith("## "):          # 新章节：重置
            in_table_list = False
            continue
        if not line.startswith("|"):
            in_table_list = False
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not cells:
            continue
        head = cells[0].replace("*", "")
        if head == "表":                     # 表头行 → 进入"表清单"模式
            in_table_list = True
            continue
        if set(head) <= set("-: "):          # 分隔行
            continue
        if head in ("字段", "库", "项", "模块"):   # 明确不是表清单
            in_table_list = False
            continue
        if not in_table_list:
            continue
        cand = head.replace("*", "")
        cand = re.split(r"[〔（(\s]", cand)[0]
        if re.fullmatch(r"[a-z_][a-z0-9_]*", cand):
            names.add(cand)
    return names


def _load_catalog():
    """直接加载 catalog 模块，**不经 rpdao/__init__**。

    因为 ``__init__`` 会 import ``pool``（进而 import psycopg），而本测试
    是纯文本一致性校验、根本不需要连库。绕开它可以让契约测试在任何
    干净环境里离线跑——契约测试不该有数据库依赖。
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_rpdao_catalog", ROOT / "modules" / "M3-rpdao" / "rpdao" / "catalog.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    _cat = _load_catalog()
    ALL_TABLES = _cat.ALL_TABLES
    EXPECTED_PHYSICAL_TABLES = _cat.EXPECTED_PHYSICAL_TABLES

    print("=" * 74)
    print("契约②：DDL ↔ 数据字典 ↔ catalog 三处表数一致性")
    print("=" * 74)

    dd = ddl_tables()
    dt = dict_tables()
    cat = set(ALL_TABLES)

    print(f"\n  DDL 文件      : {DDL_PATH.name}  → {len(dd)} 张")
    print(f"  数据字典      : {DICT_PATH.name}  → {len(dt)} 张")
    print(f"  catalog       : ALL_TABLES  → {len(cat)} 张")
    print(f"  EXPECTED 常量 : {EXPECTED_PHYSICAL_TABLES} 张")

    if not dd:
        check("DDL 文件存在且可解析", False, f"未找到或解析为空：{DDL_PATH}")
        return 1
    if not dt:
        check("数据字典存在且可解析", False, f"未找到或解析为空：{DICT_PATH}")
        return 1

    print("\n=== 1) 三处数量一致 ===")
    check("DDL 表数 == catalog 表数", len(dd) == len(cat),
          f"{len(dd)} vs {len(cat)}" if len(dd) != len(cat) else "")
    check("DDL 表数 == 数据字典表数", len(dd) == len(dt),
          f"{len(dd)} vs {len(dt)}" if len(dd) != len(dt) else "")
    check("DDL 表数 == EXPECTED_PHYSICAL_TABLES 常量",
          len(dd) == EXPECTED_PHYSICAL_TABLES,
          f"{len(dd)} vs {EXPECTED_PHYSICAL_TABLES}" if len(dd) != EXPECTED_PHYSICAL_TABLES else "")

    print("\n=== 2) 三处**逐表**比对（数量相同不等于集合相同）===")
    # 数量相同而内容不同的情况最容易漏过——必须比集合
    only_ddl = sorted(dd - cat)
    only_cat = sorted(cat - dd)
    check("DDL 里每张表都登记进 catalog", not only_ddl,
          f"DDL 有、catalog 无：{only_ddl}" if only_ddl else "")
    check("catalog 里每张表都在 DDL 中", not only_cat,
          f"catalog 有、DDL 无：{only_cat}" if only_cat else "")

    only_dict = sorted(dt - dd)
    missing_dict = sorted(dd - dt)
    check("DDL 里每张表都在数据字典中", not missing_dict,
          f"DDL 有、字典无：{missing_dict}" if missing_dict else "")
    check("数据字典里每张表都在 DDL 中", not only_dict,
          f"字典有、DDL 无：{only_dict}" if only_dict else "")

    print("\n=== 3) v0.2 新增的两张表确实三处齐备 ===")
    for t in ("quality_rule", "mapping_set"):
        check(f"{t}：DDL / catalog / 字典 三处均在",
              t in dd and t in cat and t in dt,
              f"ddl={t in dd} cat={t in cat} dict={t in dt}")

    print("\n=== 4) 两张新表的**关键约束**确实写在 DDL 里 ===")
    txt = DDL_PATH.read_text(encoding="utf-8")
    # 这些约束是这两张表的主要价值所在，漏掉等于白建
    must_have = [
        ("ck_qr_code_layer", "规则码前缀必须等于 layer —— 使跨层命名漂移在库里写不进去"),
        ("ck_qr_calibrated", "标定态为真必有时间 —— 「标定过没有」可审计"),
        ("ck_qr_severity", "severity 枚举收口"),
        ("ck_ms_confirmed", "已确认必带确认人与时间 —— 与 JSON Schema 的 if-then 同规则"),
        ("ck_ms_kind", "source_kind 枚举（表头/桩号/单位/编码）"),
        ("ck_ms_entries", "entries 必须是非空数组"),
        ("uq_ms_id_ver", "(mapping_id, version) 唯一 —— 映射可迭代但不重号"),
    ]
    for name, why in must_have:
        check(f"DDL 含约束 {name}", name in txt, why)

    print("\n=== 5) 与既有 JSON Schema 的字段对齐 ===")
    sch = ROOT / "contracts" / "semantic" / "mapping_set.v0.1.schema.json"
    if sch.exists():
        import json
        js = json.loads(sch.read_text(encoding="utf-8"))
        props = set(js.get("properties", {}))
        # DDL 里必须为这些 schema 字段留有同名列（否则"schema 说能存，库里存不下"）
        for f in sorted(props):
            # entries/target_field 在 DDL 里以列或 jsonb 内键存在
            in_ddl = re.search(rf"\b{f}\b", txt) is not None
            check(f"schema 字段 {f} 在 DDL 中有落点", in_ddl)
    else:
        print(f"  ⚠ 未找到 {sch}，跳过（不影响其它检查）")

    print("\n=== 6) 反向验证：解析器不是恒真 ===")
    # 若解析器坏掉（比如永远返回空集或全集），上面的"一致"就是假的
    check("DDL 解析结果非空且不是全集", 0 < len(dd) < 500, f"{len(dd)}")
    check("字典解析结果非空", len(dt) > 0, f"{len(dt)}")
    # 故意查一个不存在的表名，解析器应不会"认为它存在"
    check("解析器不虚报表存在", "no_such_table_xyz" not in dd and "no_such_table_xyz" not in dt)

    print("\n" + "=" * 74)
    print(f"通过 {PASS} ｜ 失败 {FAIL}")
    print("=" * 74)
    if FAIL == 0:
        print(f"\n结论：三处真源（DDL / 数据字典 / catalog）在 {len(dd)} 张表上完全一致；")
        print("      v0.2 两张新表的关键约束均已落进 DDL。任何一处单独改动都会让本测试失败。")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
