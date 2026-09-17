"""纬地 HintCAD `.DMX` 纵断面地面线文件解析器（契约⑤ 第 4 个适配器）。

文件格式（实测自 052201341 毕设工程，333 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD5.83_DMX_SHUJU          ← 魔数，含厂商版本号
    第 2 行起 `%12.3f\t%10.6f`                ← 桩号(米) TAB 地面高程(米)
    行尾 CRLF

实测：332 条数据，0.000 → 5805.421 米，与同工程的 `.STA` **桩号逐条相同**。

**注意 `.DMX` 没有行数行**（`.ZDM` 与 `.STA` 都在数据前有一行计数/序号，
`.DMX` 直接就是数据）。所以"读了几条"只能靠与 `.STA` 对账来验 —— 这也是
本解析器把逐条对齐留给上游（`build_ir` / 落库器）而不是自己拍胸脯的原因。

为什么地面线要单独一张表、且锚在**桩号**上
-------------------------------------------------------------------------------
`profile_ground_point` 是 GE 域里少数锚 `station_id`（而非 `section_id`）的表。
原因就在文件形状：`.DMX` 是**逐桩**的（332 行 ↔ 332 个桩号），不是逐路段的。
实测确认的锚定差异不是设计疏漏，而是数据本身的形状差异 ——
把它当成"锚定列不统一"去抹平，就会把逐桩数据错挂到路段上。
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..errors import SourceInvalid

# HINTCAD5.83_DMX_SHUJU —— 版本号捕获出来存进 IR 的 source.vendor_version
MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_DMX_SHUJU$")

SEGMENT = "profile_ground_point"
FILE_KIND = "纵断面地面线文件"

#: 高程合理性区间（米）。地面高程可以为负（海平面以下），但不该是 1e9 这种值。
#: 这个界限的作用是**抓列错位**：两列读反时，桩号会跑进高程列，
#: 数值立刻超出任何地面高程的可能范围。不设界限则错位会静默入库。
ELEV_MIN_M = -500.0     # 死海岸边约 -430 m
ELEV_MAX_M = 9000.0     # 珠峰 8849 m


def detect(text: str) -> bool:
    """格式探测：这个文件像不像纬地 `.DMX`？只认魔数，不靠扩展名。"""
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.DMX` → ``{"vendor_version": "5.83", "points": [{"station_m":0.0,"ground_elev_m":57.262}, …]}``

    校验策略与 `.STA` 一致：**宁可拒绝，不要猜。**
    地面高程是"填挖方、纵坡、排水"这一切判断的输入，读错一列不会报错、
    只会让整条路的地形反着来。所以每种畸形都直接抛 `SourceInvalid`。
    """
    # 注意：空字符串 split 之后是 [''] 而不是 []，所以 `if not lines` 永远不成立——
    # 那个写法（sta.py 里也有）对空文件是个**死分支**。这里按内容判空。
    if not text.strip():
        raise SourceInvalid("空文件", file=file)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_DMX_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1,
        )
    version = m.group(1)

    points: list[dict[str, Any]] = []
    for i, raw in enumerate(lines[1:], start=2):
        # 允许文件末尾空行（导出工具常留一行），但**不允许中间空行**——
        # 中间空行意味着漏读了一段地形，是静默的数据丢失。
        if raw.strip() == "":
            if all(l.strip() == "" for l in lines[i:]):
                break
            raise SourceInvalid("文件中间出现空行（疑似漏读一段地面线）", file=file, line_no=i)

        parts = raw.split("\t") if "\t" in raw else raw.split()
        if len(parts) != 2:
            raise SourceInvalid(
                f"字段数应为 2（桩号 TAB 地面高程），实为 {len(parts)}：{raw.strip()[:40]!r}",
                file=file, line_no=i,
            )
        s_txt, e_txt = parts[0].strip(), parts[1].strip()
        try:
            station_m = float(s_txt)
        except ValueError:
            raise SourceInvalid(f"桩号不是合法数字：{s_txt!r}", file=file, line_no=i) from None
        try:
            ground_elev_m = float(e_txt)
        except ValueError:
            raise SourceInvalid(f"地面高程不是合法数字：{e_txt!r}", file=file, line_no=i) from None

        if station_m < 0:
            raise SourceInvalid(f"桩号为负：{station_m}", file=file, line_no=i)
        if not math.isfinite(ground_elev_m):
            raise SourceInvalid(f"地面高程不是有限数：{ground_elev_m}", file=file, line_no=i)
        if not (ELEV_MIN_M <= ground_elev_m <= ELEV_MAX_M):
            raise SourceInvalid(
                f"地面高程 {ground_elev_m} m 超出合理区间 "
                f"[{ELEV_MIN_M}, {ELEV_MAX_M}]（疑似列错位：桩号读进了高程列？）",
                file=file, line_no=i,
            )
        if points and station_m < points[-1]["station_m"]:
            raise SourceInvalid(
                f"桩号倒退：{points[-1]['station_m']} → {station_m}（桩号必须单调不减）",
                file=file, line_no=i,
            )
        points.append({"station_m": station_m, "ground_elev_m": ground_elev_m})

    # 单点构不成"地面线"
    if len(points) < 2:
        raise SourceInvalid(f"有效数据行不足 2 条（实为 {len(points)} 条），构不成地面线",
                            file=file)

    return {"vendor_version": version, "points": points}


def check_against_stations(points: list[dict[str, Any]],
                           stations: list[dict[str, Any]]) -> list[str]:
    """把地面线与桩号序列逐条对账，返回**告警**（不抛异常：可疑 ≠ 非法）。

    为什么必须有这一步：`.DMX` 自己**没有计数行**，行数天然不可自证。
    而 `profile_ground_point` 是锚在 `station_id` 上的 —— 逐桩对不上就会错位，
    且错位不会报错。所以对账只能靠 `.STA`：

      · 条数不同  → 大概率漏读/多读
      · 桩号不同  → 大概率两份文件不是同一次导出的

    两种都算"可疑"，交给预检报告展示，由用户决定要不要继续 ——
    而不是在这里替用户决定"应该能对上，那就当它对上了"。
    """
    warn: list[str] = []
    if len(points) != len(stations):
        warn.append(f"地面线 {len(points)} 条与桩号序列 {len(stations)} 条**条数不一致**"
                    f"（地面线按桩号锚定，条数不等必然错位）")
        return warn          # 条数都不等，逐条比桩号只会刷屏
    bad = [i for i, (p, s) in enumerate(zip(points, stations), start=1)
           if abs(p["station_m"] - s["station_m"]) > 1e-6]
    if bad:
        head = "、".join(
            f"第 {i} 条 {points[i-1]['station_m']}≠{stations[i-1]['station_m']}" for i in bad[:3]
        )
        warn.append(f"{len(bad)} 条桩号与桩号序列对不上（{head}）—— "
                    f"地面线与桩号序列可能不是同一次导出的")
    return warn


# parse() 返回值里承载"该段载荷"的键名。适配器分发表据此把结果塞进 IR 的 segments[SEGMENT]。
PAYLOAD_KEY = "points"

__all__ = ["detect", "parse", "check_against_stations", "MAGIC_RE", "SEGMENT", "FILE_KIND",
           "PAYLOAD_KEY", "ELEV_MIN_M", "ELEV_MAX_M"]
