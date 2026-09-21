"""契约②：**DDL ↔ 数据字典 ↔ catalog 三处表数一致性**契约测试。

运行（**完全离线，不触库**）：
    cd /data/cy/shujuku/scaffold
    python3 tests/contract/test_ddl_dict_catalog.py

为什么需要这个测试
------------------------------------------------------------------------------
同一件事（"库里有哪几张表"）写在**三个地方**：

    1. ``scaffold/sql/10_ddl_v0.5.sql``   ← 建表的真源（契约②）
    2. ``scaffold/sql/数据字典-v0.5.md``   ← 给人看的真源

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

DDL_PATH = ROOT / "sql" / "10_ddl_v0.5.sql"
DICT_PATH = ROOT / "sql" / "数据字典-v0.5.md"

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

    # ── 0) DDL 文件本身 ────────────────────────────────────────────────
    #
    # ★★ 这一节是为一个**真实发生过的错**加的，不是假设。
    #
    # 背景：本套件的**每一个**测试都把 DDL 文件名**写死**成
    # `10_ddl_v0.5.sql`（DDL_PATH 常量）。所以当仓库里同时躺着第二个
    # `10_ddl_v0.6.sql` 时，**没有任何测试会去看它** —— 它对全部断言完全隐形。
    #
    # 真实案例：v0.5 那一版曾把 DDL 改名成 v0.6（准备升版），`git add` 进去了；
    # 后来决定"留在 v0.5、只写迁移脚本"，**磁盘上改回了 v0.5，但 v0.6 已留在 git 里**
    # —— 于是仓库里多了一个被跟踪的幽灵文件：
    #     · 文件名写 v0.6
    #     · 文件头写 v0.5
    #     · 内容 55 张表，**没有 K 节**（是加 cross_section_ground_point 之前的快照）
    # 危险在于：谁要找 DDL，看到 `10_ddl_v0.6.sql` 会打开它，拿到
    # **55 张表、少一张**的结果 —— 一个看起来更"新"、其实更旧的错误答案。
    #
    # 所以这里钉两件事：**只能有一个 DDL 文件**，且**文件名版本必须等于文件头版本**。
    print("\n=== 0) DDL 文件本身：唯一，且文件名版本 == 文件头版本 ===")
    _ddl_files = sorted((ROOT / "sql").glob("10_ddl_v*.sql"))
    check("sql/ 下只有一个 10_ddl_v*.sql（多出来的那个对所有测试隐形）",
          len(_ddl_files) == 1,
          f"找到 {[p.name for p in _ddl_files]}"
          f" —— 若非本测试引用的那个，请确认它不是改名残留的幽灵" if len(_ddl_files) != 1 else "")
    _head = DDL_PATH.read_text(encoding="utf-8")[:2000]
    _hm = re.search(r"—\s*DDL\s+(v[0-9][0-9.]*)\s*$", _head, re.M)
    _nm = re.search(r"10_ddl_(v[0-9][0-9.]*)\.sql$", DDL_PATH.name)
    check("DDL 文件名版本 == 文件头版本（改名残留会让两者不一致）",
          bool(_hm and _nm) and _hm.group(1) == _nm.group(1),
          f"文件名 {_nm.group(1) if _nm else '?'} vs 文件头 {_hm.group(1) if _hm else '未找到'}")

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

    print("\n=== 7) 命名统一（丁-2）：同一物理量的等价关系必须写在注释里 ===")
    # 丁-2 定的是「不改列名，改注释」。那么**注释就是唯一载体** ——
    # 它要是没写、或写错，这个决定就等于没做。故在此钉住。
    def _has(text: str, *needles: str) -> bool:
        return all(n in text for n in needles)

    check("★ alignment_pi.x_coord 的注释写明与 alignment_element.start_x 同量",
          _has(txt, "alignment_pi.x_coord", "alignment_element.start_x")
          or re.search(r"comment on column alignment_pi\.x_coord is\s*'[^']*"
                       r"alignment_element\.start_x", txt, re.I | re.S) is not None)
    check("★ 两个同名 median_width_m 的注释**互相**提到对方（同名必须写明同量）",
          len(re.findall(r"comment on column (?:section_design_attr|roadbed_width)\.median_width_m",
                         txt, re.I)) == 2
          and "roadbed_width.median_width_m" in txt
          and "section_design_attr.median_width_m" in txt)
    # ★ 谓词抽出来，好让元测试**真的把它跑在篡改过的文本上** ——
    #   只断言"篡改后的文本与原文不同"是**假证明**（那说明不了检查会失败）。
    _half_ok = lambda t: _has(t, "median_half_width_m", "半幅", "2 倍")  # noqa: E731
    check("★★ median_half_width_m 的注释必须点明「半幅」，且点明与全宽列**差 2 倍**",
          _half_ok(txt),
          "半幅/全宽混用会差 2 倍，是本组唯一会出真数错的隐患")

    # 非空转证明：把关键词从 DDL 里抹掉，**同一个谓词**必须变红。
    # 一个永远不会失败的检查，比没有检查更糟。
    _m1 = txt.replace("半幅", "XX")
    _m2 = txt.replace("2 倍", "XX")
    check("元测试：抹掉「半幅」后 _half_ok 确实为假（证明它不是恒真）",
          _m1 != txt and not _half_ok(_m1))
    check("元测试：抹掉「2 倍」后 _half_ok 确实为假（每个关键词都是必需的）",
          _m2 != txt and not _half_ok(_m2))
    check("元测试：原文下 _half_ok 为真（证明它不是恒假）", _half_ok(txt))

    print("\n=== 8) 侧词表统一：全库 side 列只能是 left / right ===")
    # 【这个缺陷真实发生过】本会话给 `cross_section_ground_point` 写 DDL 时，
    #   随手写了 `char(1) ... check (side in ('L','R'))`，而库里另外 7 张 side 表
    #   一直是 `varchar(8)` + `left`/`right`。同一个库里两套侧词表。
    #
    #   危害是**静默**的：跨表查"左侧测点"时 `where side = 'left'` 在本表上
    #   返回 **0 行且不报错** —— 正是本项目最怕的失败形态。
    #
    #   为什么会写错：`.HDM` 源文件**没有任何侧的标记**（靠"3 行一组"的位置关系：
    #   桩号 / 左行 / 右行），侧是**位置推出来的**，脑子里没有现成词表可抄。
    #   而源文件里**确实**写 `[LEFT]`/`[RIGHT]` 的是 `.WID` —— 注意那是**源文件**的
    #   写法，库里的词表由解析器归一化。别把两者搞混（DDL 里 roadbed_width 那行
    #   注释「实测为 [LEFT]/[RIGHT]」说的是源文件，**是对的**）。
    #
    #   所以这条检查放在 **DDL 层、离线可跑**：数据还没导进来就能拦住。
    def _side_cols(text: str) -> list[tuple[str, str]]:
        """返回 [(表名, 该行原文)]，只认**列定义**里的 side（缩进 + 行首）。

        用行首缩进而不是 'side' 子串，是为了避开 `side_ditch`（边沟）这类
        名字里带 side 的列 —— 否则会误报。
        """
        out: list[tuple[str, str]] = []
        cur = ""
        for line in text.splitlines():
            m = re.match(r"\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_][a-z0-9_]*)",
                         line, re.I)
            if m:
                cur = m.group(1)
                continue
            if re.match(r"\s*side\s+", line):
                out.append((cur, line.strip()))
        return out

    def _side_vocab_ok(text: str) -> bool:
        """全库 side 列的词表一致：varchar(8)，且不许出现 'L'/'R' 这种单字母词表。"""
        cols = _side_cols(text)
        if len(cols) < 8:                      # 少一张就说明扫描坏了，不能算通过
            return False
        for _t, ln in cols:
            if "varchar(8)" not in ln.lower():
                return False
            if re.search(r"'L'\s*,\s*'R'", ln):   # char(1) + L/R 那套
                return False
        return True

    _cols = _side_cols(txt)
    check(f"★ DDL 里带 side 列的表共 8 张（实为 {len(_cols)}）", len(_cols) == 8,
          "；".join(t for t, _ in _cols))
    check("★ 全库 side 列词表统一：varchar(8) 且无 'L'/'R' 单字母词表",
          _side_vocab_ok(txt),
          "；".join(f"{t}: {ln}" for t, ln in _cols if "varchar(8)" not in ln.lower()
                    or re.search(r"'L'\s*,\s*'R'", ln)))

    # 非空转证明：把词表改回那套错的，**同一个谓词**必须变红。
    _bad = txt.replace("side         varchar(8)    not null check (side in ('left','right')),",
                       "side         char(1)       not null check (side in ('L','R')),")
    check("元测试：把 cross_section_ground_point 的 side 改回 char(1)+L/R 后必须变红",
          _bad != txt and not _side_vocab_ok(_bad))
    check("元测试：原文下 _side_vocab_ok 为真（证明它不是恒假）", _side_vocab_ok(txt))
    # 换一个**真的会减少**的变异：删掉一张表的 side 列行。
    # （一开始我写的是"把表名改掉"，那不减少表的数量，扫描照样找到 8 张 ——
    #   变异本身选错了，红不了是当然的。变异必须真的改变被检查的事实。）
    _short = txt.replace(
        "    side         varchar(8) NOT NULL,              -- left / right\n", "", 1)
    check("元测试：某张表的 side 列行被删掉（只剩 7 张）时也必须变红 —— 防扫描静默失效",
          _short != txt and not _side_vocab_ok(_short))

    print("\n" + "=" * 74)
    print(f"通过 {PASS} ｜ 失败 {FAIL}")
    print("=" * 74)
    if FAIL == 0:
        print(f"\n结论：三处真源（DDL / 数据字典 / catalog）在 {len(dd)} 张表上完全一致；")
        print("      v0.2 两张新表的关键约束均已落进 DDL。任何一处单独改动都会让本测试失败。")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
