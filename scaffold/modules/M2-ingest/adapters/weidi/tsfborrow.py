"""纬地 HintTF `.tsf` → 段 ``borrow_pit``（取土坑）。

★ **本适配器的输入不是厂商原始文件**，而是 `tools/tsf2txt.py` 摊出来的**文本**
（`.tsftxt`）—— 与 `tsf.py` / `tsftransfer.py` 完全一样，原因见 `tsf.py` 的模块 docstring。

★★ 为什么又是**另一个模块**：`_PARSERS[seg]` 按**段**取解析器，而一个模块只能有
   **一个** `SEGMENT` / `PAYLOAD_KEY`。`.tsftxt` 一个文件现在要出**四个段**
   （earthwork_factor + earthwork_transfer + borrow_pit + spoil_pit），故四个模块。
   `build_ir` 是「每段查**自己的**后缀」，所以四个段共用 `.tsftxt` 本来就支持。

★★ 本段的表**几乎全是出厂默认值**（详见 DDL N 节与数据字典 N1/N2）：
   `pct_*` / `access_road_length_m` 是纬地出厂默认，`*_total_m3` / `capacity_m3`
   是「无限」占位符。**真值只有桩号** —— 而 `earthwork_transfer.source_kind=1`
   的取土点就靠 `access_station_m` 落地。适配器**原样解析、不做任何"修正"**：
   把占位符改成 NULL 或改成"看起来合理"的数，都是**替源文件做主**。

★ 桩号单位：本解析器对外一律用**米**（``access_station_m`` 等），与其余适配器一致；
  入库时由 design_import 换算成 km（DDL 列是 ``*_km``）。
"""

from __future__ import annotations

from typing import Any

from adapters.errors import SourceInvalid

# ★ 复用 tsf.py 已验证的那套机器（魔数、表定位、表头抠列名）——
#   同一个文件格式，**不复制**。复制的话几份会各自漂移。
from adapters.weidi import tsf as _base

MAGIC_RE = _base.MAGIC_RE
SEGMENT = "borrow_pit"
TABLE = "取土坑"

# ★ 逐列**显式映射**，不用位置。位置映射在源列序变化时会**静默错位**
#   （把占比当方量存进去，谁也不会报错）；显式映射会当场炸。
COLUMN_MAP: dict[str, str] = {
    "支线长度": "access_road_length_m",
    "松土": "pct_1",
    "普通土": "pct_2",
    "硬土": "pct_3",
    "软石": "pct_4",
    "次坚石": "pct_5",
    "坚石": "pct_6",
    "土方总量": "soil_total_m3",
    "石方总量": "rock_total_m3",
    "上路桩号": "access_station_m",
    "前经济分界点桩号": "econ_front_m",
    "后经济分界点桩号": "econ_back_m",
}

# ★ 源里桩号是**文本**（实测 '4100.000' / '1900'），要转成数。
#   其余列源里就是数。
_STR_NUM_COLS = frozenset({"access_station_m", "econ_front_m", "econ_back_m"})

detect = _base.detect


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.tsftxt` → ``{"vendor_version":"6.00","table":"取土坑","rows":[{…}]}``

    校验策略同其余适配器：**宁可拒绝，不要猜。**
    """
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
            f"已知列只有 {sorted(COLUMN_MAP)}", file=file)
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
        rows.append(rec)

    if not rows:
        raise SourceInvalid(f"表「{TABLE}」一行数据都没有", file=file)

    # ★★ 本段的表**每张只有 1 行**（一个工程一个取土坑/弃土坑的记录）。
    #   源里多行就是源的问题 —— 取第一行会**静默丢数据**，故在这里就说清楚。
    #   ⚠ 不是"拒绝多坑"：多坑是合法的，但**本适配器还没处理多坑**，
    #     与其默默取第一行，不如明确拒绝。
    if len(rows) != 1:
        raise SourceInvalid(
            f"表「{TABLE}」应有 1 行，实为 {len(rows)} 行 —— "
            f"多坑是合法的，但本适配器**还没处理多坑**；"
            f"取第一行等于静默丢掉其余的坑，故在这里拒绝。", file=file)

    return {"vendor_version": version, "table": TABLE, "rows": rows}


PAYLOAD_KEY = "rows"
