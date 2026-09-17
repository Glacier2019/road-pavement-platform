"""纬地 HintCAD `.STA` 桩号序列文件解析器（契约⑤ 的第一个适配器）。

文件格式（实测自 052201341 毕设工程，333 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD5.84_STA_SHUJU          ← 魔数，含厂商版本号
    第 2 行起 `%12.3f\t%10d`                 ← 桩号(米) TAB 序号
    行尾 CRLF

实测：332 条数据，0.000 → 5805.421 米。**并非全程等距**——
规整 20 m 间隔到 540.000，随后出现 545.874 这类不规则点。
因此解析器**不得假设等距**（假设等距会在第一个不规则点处静默错位）。

为什么这个文件是导入链路的第一站
-------------------------------------------------------------------------------
`station_sequence` 是 GE 域的**桩号锚定基准**：其余逐桩数据表全部以 FK 锚在它上面。
先把这一张表导对，才能验证「桩号一等实体」这条设计是否真的成立。
"""
from __future__ import annotations

import re
from typing import Any

from ..errors import SourceInvalid

# HINTCAD5.84_STA_SHUJU —— 版本号捕获出来存进 IR 的 source.vendor_version
MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_STA_SHUJU$")

SEGMENT = "station_sequence"
FILE_KIND = "桩号序列文件"


def detect(text: str) -> bool:
    """格式探测：这个文件像不像纬地 `.STA`？

    只认魔数，不靠扩展名——扩展名可以改，魔数不会。
    """
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.STA` → ``{"vendor_version": "5.84", "points": [{"station_m":0.0,"seq_no":1}, …]}``

    校验策略：**宁可拒绝，不要猜。**
    猜错一个字段的后果是静默写入错误桩号，而桩号是下游一切逐桩数据的对齐基准——
    错一条，挂在上面的 WIM/病害/试验数据全部错位，且**不会有任何报错**。
    所以这里对每种畸形都直接抛 `SourceInvalid`，而不是跳过或补默认值。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines:
        raise SourceInvalid("空文件", file=file)

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_STA_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1,
        )
    version = m.group(1)

    points: list[dict[str, Any]] = []
    for i, raw in enumerate(lines[1:], start=2):
        # 允许文件末尾的空行（导出工具常留一行），但**不允许中间空行**——
        # 中间空行意味着漏读了一段桩号，是静默的数据丢失。
        if raw.strip() == "":
            if all(l.strip() == "" for l in lines[i:]):
                break
            raise SourceInvalid("文件中间出现空行（疑似漏读一段桩号）", file=file, line_no=i)

        parts = raw.split("\t") if "\t" in raw else raw.split()
        if len(parts) != 2:
            raise SourceInvalid(
                f"字段数应为 2（桩号 TAB 序号），实为 {len(parts)}：{raw.strip()[:40]!r}",
                file=file, line_no=i,
            )
        s_txt, n_txt = parts[0].strip(), parts[1].strip()
        try:
            station_m = float(s_txt)
        except ValueError:
            raise SourceInvalid(f"桩号不是合法数字：{s_txt!r}", file=file, line_no=i) from None
        try:
            seq_no = int(n_txt)
        except ValueError:
            raise SourceInvalid(f"序号不是合法整数：{n_txt!r}", file=file, line_no=i) from None

        if station_m < 0:
            raise SourceInvalid(f"桩号为负：{station_m}", file=file, line_no=i)
        if points:
            prev = points[-1]
            if station_m < prev["station_m"]:
                raise SourceInvalid(
                    f"桩号倒退：{prev['station_m']} → {station_m}（桩号序列必须单调不减）",
                    file=file, line_no=i,
                )
            if seq_no <= prev["seq_no"]:
                raise SourceInvalid(
                    f"序号未递增：{prev['seq_no']} → {seq_no}",
                    file=file, line_no=i,
                )
        points.append({"station_m": station_m, "seq_no": seq_no})

    # 单点构不成"序列"——契约⑤ schema 对 station_sequence 亦要求 minItems: 2
    if len(points) < 2:
        raise SourceInvalid(f"有效数据行不足 2 条（实为 {len(points)} 条），构不成桩号序列",
                            file=file)

    return {"vendor_version": version, "points": points}


# parse() 返回值里承载"该段载荷"的键名。适配器分发表据此把结果塞进 IR 的 segments[SEGMENT]。
PAYLOAD_KEY = "points"

__all__ = ["detect", "parse", "MAGIC_RE", "SEGMENT", "FILE_KIND", "PAYLOAD_KEY"]
