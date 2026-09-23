"""纬地 HintTF `.tsf` → 段 ``earthwork_haul_stat``（统计扩展）。

★ **输入不是厂商原始文件**，而是 `tools/tsf2txt.py` 摊出来的 `.tsftxt` 文本 ——
   原因见 `tsf.py` 的模块 docstring。

★★ 又一个模块的理由：`_PARSERS[seg]` 按**段**取解析器，而一个模块只能有**一个**
   `SEGMENT` / `PAYLOAD_KEY`。`.tsftxt` 现在一个文件要出**六个段**，故六个模块。

★★★ 本段**列特别多**（73 列），且**绝大部分列在本工程里恒为 0**
   （石方、土的 4/5/6 类、以及若干整组）。**照样全收** ——
   用户明确要求「如实标注」：少收列 = 静默丢数据，
   「本工程没用上」和「这一列不存在」是两件不同的事。

★ 列名是源列名的**机械对应**（逐列可回溯），不用位置映射 ——
   位置映射在源列序变化时会**静默错位**（把调出当成借入存进去，谁也不会报错）。
"""

from __future__ import annotations

from typing import Any

from adapters.errors import SourceInvalid

from adapters.weidi import tsf as _base

MAGIC_RE = _base.MAGIC_RE
SEGMENT = "earthwork_haul_stat"
TABLE = "统计扩展"

# ★ 逐列**显式映射**（源列名 → IR 键名）。
COLUMN_MAP: dict[str, str] = {
    "起始桩号": "start_station_m",
    "终止桩号": "end_station_m",
    "调土量松": "haul_soil_loose_m3",
    "调石量松": "haul_rock_loose_m3",
    "调松1": "haul_loose_1_m3",
    "调松2": "haul_loose_2_m3",
    "调松3": "haul_loose_3_m3",
    "调松4": "haul_loose_4_m3",
    "调松5": "haul_loose_5_m3",
    "调松6": "haul_loose_6_m3",
    "调1": "haul_1_m3",
    "调2": "haul_2_m3",
    "调3": "haul_3_m3",
    "调4": "haul_4_m3",
    "调5": "haul_5_m3",
    "调6": "haul_6_m3",
    "借土量松": "borrow_soil_loose_m3",
    "借石量松": "borrow_rock_loose_m3",
    "借松1": "borrow_loose_1_m3",
    "借松2": "borrow_loose_2_m3",
    "借松3": "borrow_loose_3_m3",
    "借松4": "borrow_loose_4_m3",
    "借松5": "borrow_loose_5_m3",
    "借松6": "borrow_loose_6_m3",
    "借1": "borrow_1_m3",
    "借2": "borrow_2_m3",
    "借3": "borrow_3_m3",
    "借4": "borrow_4_m3",
    "借5": "borrow_5_m3",
    "借6": "borrow_6_m3",
    "弃土量松": "spoil_soil_loose_m3",
    "弃石量松": "spoil_rock_loose_m3",
    "弃松1": "spoil_loose_1_m3",
    "弃松2": "spoil_loose_2_m3",
    "弃松3": "spoil_loose_3_m3",
    "弃松4": "spoil_loose_4_m3",
    "弃松5": "spoil_loose_5_m3",
    "弃松6": "spoil_loose_6_m3",
    "弃1": "spoil_1_m3",
    "弃2": "spoil_2_m3",
    "弃3": "spoil_3_m3",
    "弃4": "spoil_4_m3",
    "弃5": "spoil_5_m3",
    "弃6": "spoil_6_m3",
    "调出土量松": "haul_out_soil_loose_m3",
    "调出石量松": "haul_out_rock_loose_m3",
    "调出松1": "haul_out_loose_1_m3",
    "调出松2": "haul_out_loose_2_m3",
    "调出松3": "haul_out_loose_3_m3",
    "调出松4": "haul_out_loose_4_m3",
    "调出松5": "haul_out_loose_5_m3",
    "调出松6": "haul_out_loose_6_m3",
    "调出1": "haul_out_1_m3",
    "调出2": "haul_out_2_m3",
    "调出3": "haul_out_3_m3",
    "调出4": "haul_out_4_m3",
    "调出5": "haul_out_5_m3",
    "调出6": "haul_out_6_m3",
    "调入土量松": "haul_in_soil_loose_m3",
    "调入石量松": "haul_in_rock_loose_m3",
    "调入松1": "haul_in_loose_1_m3",
    "调入松2": "haul_in_loose_2_m3",
    "调入松3": "haul_in_loose_3_m3",
    "调入松4": "haul_in_loose_4_m3",
    "调入松5": "haul_in_loose_5_m3",
    "调入松6": "haul_in_loose_6_m3",
    "调入1": "haul_in_1_m3",
    "调入2": "haul_in_2_m3",
    "调入3": "haul_in_3_m3",
    "调入4": "haul_in_4_m3",
    "调入5": "haul_in_5_m3",
    "调入6": "haul_in_6_m3",
    "分段编号": "section_seq",}

# ★ 源里桩号是数（米），但列名是 `*_m` —— 入库时 design_import 会换算成 km
#   并**改名**为 `*_km`（只换值不换名会让 PG 报 column does not exist）。
_STATION_KEYS = ("start_station_m", "end_station_m")

# ★ 源里 `分段编号` 是整数。
_INT_COLS = frozenset({"section_seq"})

detect = _base.detect


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.tsftxt` → ``{"vendor_version":"6.00","table":"统计扩展","rows":[{…}]}``"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines:
        raise SourceInvalid("空文件", file=file)

    m = MAGIC_RE.match(lines[0].lstrip("\ufeff").strip())
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTTF<版本>_TSF_TXT_VER<n>`，实为 {lines[0][:40]!r}",
            file=file, line_no=1)
    version, fmt_ver = m.group(1), m.group(2)
    if fmt_ver != "1":
        raise SourceInvalid(
            f"转换格式版本是 {fmt_ver}，本适配器只认 1 —— "
            f"格式变了就必须同步改适配器，**不猜**", file=file, line_no=1)

    cols, data = _base._find_table(lines, TABLE, file)
    if not cols:
        raise SourceInvalid(f"表「{TABLE}」的表头是空的", file=file)

    unknown = [c for c in cols if c not in COLUMN_MAP]
    if unknown:
        raise SourceInvalid(
            f"表「{TABLE}」出现未知列 {unknown} —— 源格式可能变了，"
            f"已知列只有 {len(COLUMN_MAP)} 个", file=file)
    missing = [c for c in COLUMN_MAP if c not in cols]
    if missing:
        raise SourceInvalid(f"表「{TABLE}」缺列 {missing}", file=file)

    rows: list[dict[str, Any]] = []
    for ln in data:
        parts = ln.split("\t")
        if len(parts) != len(cols):
            raise SourceInvalid(
                f"字段数应为 {len(cols)}，实为 {len(parts)}：{ln.strip()[:40]!r}", file=file)
        rec: dict[str, Any] = {}
        for c, v in zip(cols, parts):
            key = COLUMN_MAP[c]
            v = v.strip()
            if v in ("NULL", ""):
                rec[key] = None
                continue
            try:
                rec[key] = float(v)
            except ValueError:
                raise SourceInvalid(
                    f"列「{c}」的值不是合法数字：{v!r}", file=file) from None
            if key in _INT_COLS:
                rec[key] = int(rec[key])
        rows.append(rec)

    if not rows:
        raise SourceInvalid(f"表「{TABLE}」一行数据都没有", file=file)

    return {"vendor_version": version, "table": TABLE, "rows": rows}


PAYLOAD_KEY = "rows"
