"""纬地 HintTF `.tsf` → 段 ``earthwork_fill_stat``（土方调配扩展记录）。

★ **输入不是厂商原始文件**，而是 `tools/tsf2txt.py` 摊出来的 `.tsftxt` 文本 ——
   原因见 `tsf.py` 的模块 docstring。

★★ 又一个模块的理由：`_PARSERS[seg]` 按**段**取解析器，而一个模块只能有**一个**
   `SEGMENT` / `PAYLOAD_KEY`。`.tsftxt` 现在一个文件要出**六个段**，故六个模块。

★★★ 本段**列特别多**（46 列），且**绝大部分列在本工程里恒为 0**
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
SEGMENT = "earthwork_fill_stat"
TABLE = "土方调配扩展记录"

# ★ 逐列**显式映射**（源列名 → IR 键名）。
COLUMN_MAP: dict[str, str] = {
    "起始桩号": "start_station_m",
    "终止桩号": "end_station_m",
    "填方总量松": "fill_total_loose_m3",
    "填土量松": "fill_soil_loose_m3",
    "填石量松": "fill_rock_loose_m3",
    "填松1": "fill_loose_1_m3",
    "填松2": "fill_loose_2_m3",
    "填松3": "fill_loose_3_m3",
    "填松4": "fill_loose_4_m3",
    "填松5": "fill_loose_5_m3",
    "填松6": "fill_loose_6_m3",
    "填1": "fill_1_m3",
    "填2": "fill_2_m3",
    "填3": "fill_3_m3",
    "填4": "fill_4_m3",
    "填5": "fill_5_m3",
    "填6": "fill_6_m3",
    "利土量松": "utilize_soil_loose_m3",
    "利石量松": "utilize_rock_loose_m3",
    "利松1": "utilize_loose_1_m3",
    "利松2": "utilize_loose_2_m3",
    "利松3": "utilize_loose_3_m3",
    "利松4": "utilize_loose_4_m3",
    "利松5": "utilize_loose_5_m3",
    "利松6": "utilize_loose_6_m3",
    "利1": "utilize_1_m3",
    "利2": "utilize_2_m3",
    "利3": "utilize_3_m3",
    "利4": "utilize_4_m3",
    "利5": "utilize_5_m3",
    "利6": "utilize_6_m3",
    "缺土量松": "deficit_soil_loose_m3",
    "缺石量松": "deficit_rock_loose_m3",
    "缺松1": "deficit_loose_1_m3",
    "缺松2": "deficit_loose_2_m3",
    "缺松3": "deficit_loose_3_m3",
    "缺松4": "deficit_loose_4_m3",
    "缺松5": "deficit_loose_5_m3",
    "缺松6": "deficit_loose_6_m3",
    "缺1": "deficit_1_m3",
    "缺2": "deficit_2_m3",
    "缺3": "deficit_3_m3",
    "缺4": "deficit_4_m3",
    "缺5": "deficit_5_m3",
    "缺6": "deficit_6_m3",
    "分段编号": "section_seq",}

# ★ 源里桩号是数（米），但列名是 `*_m` —— 入库时 design_import 会换算成 km
#   并**改名**为 `*_km`（只换值不换名会让 PG 报 column does not exist）。
_STATION_KEYS = ("start_station_m", "end_station_m")

# ★ 源里 `分段编号` 是整数。
_INT_COLS = frozenset({"section_seq"})

detect = _base.detect


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.tsftxt` → ``{"vendor_version":"6.00","table":"土方调配扩展记录","rows":[{…}]}``"""
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
