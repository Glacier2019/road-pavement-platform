"""纬地 **HintTF** 土石方调配 `.tsf` 的**转换文本**（`.tsftxt`）解析器。

⚠⚠ 这个适配器和其余 13 个**不一样**，先把话说清楚
-------------------------------------------------------------------------------
其余适配器的输入是**厂商原始文件**（`.STA`/`.JD`/`.tf`/…）。**本适配器的输入不是** ——
`.tsf` 是 Microsoft Access / Jet 4 数据库（二进制），而 M2 的适配器契约是
`parse(text)`、零第三方依赖。为了不破坏这个契约，`.tsf` 先经
`tools/tsf2txt.py`（**一次性工具，不进运行时**）摊成文本，本适配器读**那个文本**。

因此：
  · 魔数 `HINTTF6.00_TSF_TXT_VER1` 是**转换器**写的，**不是纬地写的**
  · 输入的扩展名是 `.tsftxt`，不是 `.tsf`
  · **导入真工程前必须先跑转换器** —— 这是这个方案的代价，不是零成本

为什么仍然值得（用户拍板的「乙」方案）：
  ① M2 的分发表只有**一种形状**（都吃 text），不必为二进制单开一路
  ② 转换结果可入库当夹具 → 测试**不依赖二进制、不依赖 access-parser**
  ③ 转换器是离线工具，不进运行时依赖

格式（转换器定的）
-------------------------------------------------------------------------------
    HINTTF6.00_TSF_TXT_VER1
    == TABLE 土石系数 ==
    //[ 土方1 ][ 土方2 ][ 土方3 ][ 石方1 ][ 石方2 ][ 石方3 ]
    1.23\t1.16\t1.09\t0.92\t0.92\t0.92

表头照 `.tf` 的 `//[..][..]` 样式 —— 复用 `tf.py` 已验证的解析法，不另造一套。
"""
from __future__ import annotations

import re
from typing import Any

from ..errors import SourceInvalid

# HINTTF6.00_TSF_TXT_VER1 —— 厂商名 + 版本 + **格式版本**（转换器自己的版本）
MAGIC_RE = re.compile(r"^HINTTF([0-9][0-9.]*)_TSF_TXT_VER([0-9]+)$")

SEGMENT = "earthwork_factor"
FILE_KIND = "土石方调配文件（.tsf 转换文本）"

# 本适配器要的那张表（纬地自己的中文表名）
TABLE = "土石系数"

# 列名 → IR 字段名。★**逐列显式映射，不用位置** ——
# 位置映射在源文件列序变化时会静默错位；显式映射会 KeyError，当场炸。
# ⚠ 这也是为什么夹具里必须保留**表头**：没有表头就只能靠位置，那就退回"猜"了。
COLUMN_MAP: dict[str, str] = {
    "土方1": "factor_soil_1",   # 松土
    "土方2": "factor_soil_2",   # 普通土
    "土方3": "factor_soil_3",   # 硬土
    "石方1": "factor_rock_1",   # 软石
    "石方2": "factor_rock_2",   # 次坚石
    "石方3": "factor_rock_3",   # 坚石
}

_TABLE_RE = re.compile(r"^== TABLE (.+?) ==$")
_HEADER_RE = re.compile(r"^//(\[.*\])$")


def _header_names(line: str) -> list[str]:
    """从 `//[..][..]` 抠出列名。与 `tf.py` 同一套写法。"""
    return [x.strip() for x in re.findall(r"\[([^\]]*)\]", line)]


def detect(text: str) -> bool:
    """格式探测：只认魔数，不靠扩展名。"""
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def _find_table(lines: list[str], name: str, file: str | None) -> tuple[list[str], list[str]]:
    """定位 `== TABLE <name> ==` 段，返回（表头列名, 数据行）。"""
    for i, raw in enumerate(lines):
        m = _TABLE_RE.match(raw.strip())
        if not m or m.group(1) != name:
            continue
        if i + 1 >= len(lines) or not _HEADER_RE.match(lines[i + 1].strip()):
            raise SourceInvalid(
                f"表「{name}」后面没有 `//[..]` 表头行", file=file, line_no=i + 2)
        cols = _header_names(lines[i + 1].strip())
        data: list[str] = []
        for j in range(i + 2, len(lines)):
            if _TABLE_RE.match(lines[j].strip()):
                break
            if lines[j].strip():
                data.append(lines[j])
        return cols, data
    raise SourceInvalid(
        f"转换文本里没有表「{name}」—— 该表可能没被 tsf2txt.py 的 EXPORT_TABLES 导出",
        file=file)


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.tsftxt` → ``{"vendor_version":"6.00","table":"土石系数","rows":[{…}]}``

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

    cols, data = _find_table(lines, TABLE, file)
    if not cols:
        raise SourceInvalid(f"表「{TABLE}」的表头是空的", file=file)

    # ★ 未知列直接拒绝 —— 源文件多了一列而我们不认识，说明格式变了。
    unknown = [c for c in cols if c not in COLUMN_MAP]
    if unknown:
        raise SourceInvalid(
            f"表「{TABLE}」出现未知列 {unknown} —— 源格式可能变了，"
            f"已知列只有 {sorted(COLUMN_MAP)}", file=file)
    missing = [c for c in COLUMN_MAP if c not in cols]
    if missing:
        raise SourceInvalid(
            f"表「{TABLE}」缺列 {missing}", file=file)

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

    return {"vendor_version": version, "table": TABLE, "rows": rows}


PAYLOAD_KEY = "rows"

__all__ = ["detect", "parse", "MAGIC_RE", "SEGMENT", "FILE_KIND", "PAYLOAD_KEY",
           "TABLE", "COLUMN_MAP"]
