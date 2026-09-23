"""纬地 HintTF `.tsf` → 段 ``earthwork_transfer``（土石方调配过程）。

★ **本适配器的输入不是厂商原始文件**，而是 `tools/tsf2txt.py` 摊出来的**文本**
（`.tsftxt`）—— 与同目录的 `tsf.py` 完全一样，原因见那个模块的模块 docstring。

★★ 为什么是**另一个模块**而不是 `tsf.py` 里加个函数：
   `_PARSERS[seg]` 是按**段**取解析器，而一个模块只能有**一个** `SEGMENT` /
   `PAYLOAD_KEY`。`.tsftxt` 一个文件要出**两个段**（earthwork_factor +
   earthwork_transfer），所以必须两个模块。
   `build_ir` 的循环是 `for seg in CAPABILITIES:` + 各段查**自己的**后缀 ——
   两个段共用 `.tsftxt` 这个后缀**本来就支持**，不需要泛化 `SEGMENT_FILES`。

★★ 本段与 `earthwork_factor` 的区别：**一个是输入、一个是成果**。
   earthwork_factor 是用户在设计软件里设的压实系数（设计**输入**）；
   本段是软件算出来的 27 次调配（设计**成果**）。
   所以 DDL 里它们分属 L 节 / M 节，不并在一起。

★ 桩号单位：本解析器对外一律用**米**（``*_m``），与其余适配器一致；
  入库时由 design_import 换算成 km（DDL 列是 ``*_km``）。
"""

from __future__ import annotations

from typing import Any

from adapters.errors import SourceInvalid

# ★ 复用 tsf.py 已验证的那套机器（魔数、表定位、表头抠列名）——
#   同一个文件格式，**不复制**。复制的话两份会各自漂移。
from adapters.weidi import tsf as _base

MAGIC_RE = _base.MAGIC_RE
SEGMENT = "earthwork_transfer"
TABLE = "过程"

# ★ 逐列**显式映射**，不用位置。位置映射在源列序变化时会**静默错位**
#   （把运距当方量存进去，谁也不会报错）；显式映射会当场炸。
#   源列名「用土1/2/3 + 用石4/5/6」数字 1–6 连续，正好对上
#   earthwork_composition.pct_1..6 的六分类（松土/普通土/硬土/软石/次坚石/坚石）。
COLUMN_MAP: dict[str, str] = {
    "GCID": "transfer_no",
    "分段编号": "section_seq",
    "取土段S": "cut_start_m",
    "取土段E": "cut_end_m",
    "弃土段S": "fill_start_m",
    "弃土段E": "fill_end_m",
    "用土": "used_soil_m3",
    "用石": "used_rock_m3",
    "用土(压实)": "used_soil_compacted_m3",
    "用石(压实)": "used_rock_compacted_m3",
    "用土1": "used_class_1_m3",
    "用土2": "used_class_2_m3",
    "用土3": "used_class_3_m3",
    "用石4": "used_class_4_m3",
    "用石5": "used_class_5_m3",
    "用石6": "used_class_6_m3",
    "用土1(压实)": "used_class_1_compacted_m3",
    "用土2(压实)": "used_class_2_compacted_m3",
    "用土3(压实)": "used_class_3_compacted_m3",
    "用石4(压实)": "used_class_4_compacted_m3",
    "用石5(压实)": "used_class_5_compacted_m3",
    "用石6(压实)": "used_class_6_compacted_m3",
    "土方运距": "haul_soil_m",
    "石方运距": "haul_rock_m",
    "土方1运距": "haul_class_1_m",
    "土方2运距": "haul_class_2_m",
    "土方3运距": "haul_class_3_m",
    "石方4运距": "haul_class_4_m",
    "石方5运距": "haul_class_5_m",
    "石方6运距": "haul_class_6_m",
    "坑": "source_kind",
}

# ★ 这两列在 schema 里声明的是 **integer**（序号），不是 number。
#   不显式转的话会变成 1.0 / 27.0，schema 校验当场失败 —— 那是好事（不会静默存成浮点）。
_INT_COLS = frozenset({"transfer_no", "section_seq"})

detect = _base.detect


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.tsftxt` → ``{"vendor_version":"6.00","table":"过程","rows":[{…}]}``

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
                rec[key] = int(v) if key in _INT_COLS else float(v)
            except ValueError:
                raise SourceInvalid(
                    f"列「{c}」的值不是合法{'整数' if key in _INT_COLS else '数字'}：{v!r}",
                    file=file) from None
        rows.append(rec)

    if not rows:
        raise SourceInvalid(f"表「{TABLE}」一行数据都没有", file=file)

    # ★★ 源里 `坑=1` 时取土段起止**相等**（取土坑退化成一个点）—— 这不是错，
    #   是纬地的表示法。但 `坑=0` 时若起止相等，那是**数据本身有问题**：
    #   「路段内调运」必须有一个长度不为零的取土段。这里只告警不拒绝 ——
    #   告警走顶层 notes（不进 payload，见 build_ir 的说明）。
    notes: list[str] = []
    odd = [r["transfer_no"] for r in rows
           if r.get("source_kind") == 0 and r.get("cut_start_m") == r.get("cut_end_m")]
    if odd:
        notes.append(
            f"调配序号 {odd} 标着 source_kind=0（路段内调运）但取土段起止相等 —— "
            f"「路段内调运」应当有一个长度不为零的取土段，请核对源文件")

    return {"vendor_version": version, "table": TABLE, "rows": rows, "notes": notes}


PAYLOAD_KEY = "rows"
