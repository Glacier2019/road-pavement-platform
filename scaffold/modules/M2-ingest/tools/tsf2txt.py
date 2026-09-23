#!/usr/bin/env python3
"""`.tsf` → `.tsftxt` 转换器（**一次性工具，不进 M2 运行时**）。

为什么要这个转换器
-------------------------------------------------------------------------------
M2 现有的 13 个适配器**全部**是 `parse(text)` —— 吃文本、零第三方依赖。
`.tsf` 是 **Microsoft Access / Jet 4 数据库**（二进制），要读它就得有 `access-parser`。
把那个依赖塞进 M2 运行时，会让分发表出现**两种形状**的适配器
（有的吃 text、有的吃 bytes），而契约⑤ 的整个设计是建立在"一个文件 → 一个段、
解析器只认文本"之上的。

所以：**转换在 M2 之外做**。本工具把 `.tsf` 摊成一个**自描述的文本**
（`.tsftxt`），适配器仍然只吃文本，M2 保持零依赖。

⚠ 代价要说清楚：**导入一个真工程时，必须先对本工具跑一遍**。这不是零成本的。
   好处是转换结果可以入库当夹具，测试不依赖二进制、不依赖 access-parser。

格式（本工具自己定的，不是纬地的格式）
-------------------------------------------------------------------------------
    HINTTF6.00_TSF_TXT_VER1                    ← 魔数：厂商名 + 版本 + 格式版本
    == TABLE 土石系数 ==                        ← 表名（纬地自己的中文表名，原样）
    //[ 土方1 ][ 土方2 ][ 土方3 ][ 石方1 ] …   ← 表头：照 `.tf` 的 `//[..][..]` 样式
    1.2300\t1.1600\t1.0900\t0.9200\t…          ← 数据：TAB 分隔，一行一条记录

    ⚠ 为什么表头照 `.tf` 抄：`.tf` 是纬地唯一**自带完整表头**的文本文件，M2 的
      `tf.py` 已经有一套 `//[..][..]` 的解析（`_header_names`）。**复用已验证的样式**，
      比另造一套好 —— 而且人打开文件就能看懂列是什么。

用法
-------------------------------------------------------------------------------
    uv run --quiet --with access-parser python3 tools/tsf2txt.py <输入.tsf> [输出.tsftxt]

不带输出参数时，写到同目录同名 + `.tsftxt`。
"""
from __future__ import annotations

import sys
from pathlib import Path

MAGIC = "HINTTF6.00_TSF_TXT_VER1"

# 表名 → 是否导出。**只导用户确认要的表**，不整库照搬 ——
# ⚠ 本工具**故意不是"一键全导"**：`.tsf` 有 20 张表，其中
#   · `Tmp调配过程表`(562 行) 是中间结果，不该进平台
#   · `土石计算中间表` 与 `土石计算` 列完全相同（137 列），是副本
#   · `调配过程表`(27 行) 数值**全是 0**（空壳），真账在 `过程`(27 行)
#   全导会把"中间结果"和"真结果"混在一起，而平台的分不清。
#   故这里是一张**显式白名单**，加一张就是一行、可审计的决定。
EXPORT_TABLES: tuple[str, ...] = (
    "土石系数",     # 1 行 6 列 —— 压实系数（本版落地）
)


def _fmt(v: object) -> str:
    """值的文本化。**保真优先**：float 用 repr 的 17 位有效数字。"""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return repr(v)
    return str(v)


def convert(src: Path) -> str:
    from access_parser import AccessParser

    db = AccessParser(str(src))
    out = [MAGIC]
    for name in EXPORT_TABLES:
        d = db.parse_table(name)
        cols = list(d.keys())
        n = len(d[cols[0]]) if cols else 0
        out.append(f"== TABLE {name} ==")
        out.append("//" + "".join(f"[ {c} ]" for c in cols))
        for i in range(n):
            out.append("\t".join(_fmt(d[c][i]) for c in cols))
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().split("用法")[1].strip(), file=sys.stderr)
        return 10
    src = Path(argv[1])
    if not src.is_file():
        print(f"输入不存在：{src}", file=sys.stderr)
        return 10
    dst = Path(argv[2]) if len(argv) > 2 else src.with_suffix(src.suffix + "txt")
    text = convert(src)
    dst.write_text(text, encoding="utf-8")
    print(f"  {src.name} → {dst}")
    print(f"  {len(text)} 字符 / {len(text.splitlines())} 行 / "
          f"{len(EXPORT_TABLES)} 张表：{'、'.join(EXPORT_TABLES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
