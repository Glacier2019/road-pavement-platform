"""纬地 HintCAD `.WID` 路幅宽度数据文件解析器（契约⑤ 第 7 个适配器）。

文件格式（**教程 §13.4 逐列定义** + 实测自 052201341 毕设工程，9 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD<版本>_WID_SHUJU     ← 魔数，含厂商版本号（教程示例 5.8，实测 6.00）
    分段标记行 表示其后是左侧还是右侧
    数据行     `%12.3f\t` × 7，**每两行为一组**

    列 1  起（终）点桩号
    列 2  中央分隔带宽度
    列 3  半侧路面（行车道＋内侧路缘带）宽度
    列 4  有无附加车道的判断标识
    列 5  硬路肩宽度
    列 6  土路肩宽度（折线法标注时输入楔形端部鼻端半径）
    列 7  有附加车道时的项目文件名（没有时输 "0"）

教程原文（§13.4）：

    「此文件描述了整个路线（或匝道）路幅左右侧的分段变化情况，特别是加宽变化。
      文件第一行为文件的版本及文件类型名称的信息，以下各行填写路幅变化的特征位置数据。
      一行"Z"字母或一行"Y"分别表示其后跟随的是描述左侧或右侧路幅变化的数据。
      ……数据每两行为一组，说明路基一侧某个桩号区间内的路幅宽度变化情况。
      ……此文件桩号区间必须成对出现，桩号区间要连续。」

★ 分段标记有两种写法，本解析器**两种都认** —— 这是唯一一处教程与实测不一致的地方
-------------------------------------------------------------------------------
  · 教程 §13.4（5.8 代）：一行 `Z`（左侧）或一行 `Y`（右侧）。教程示例写的是
    **一整行 z**（`zzzzzzzz…`），不是单个字母。
  · **实测 6.00 版**：`[LEFT]` / `[RIGHT]` 两个段标题行。
  · 教程**全文没有** `[LEFT]`/`[RIGHT]` 这种写法（grep 0 命中），所以这是**升级后
    未更新的文档**，不是我们读错。两种都认，才既支持老工程也支持新工程。

★「每两行为一组」收成一行一个区间
-------------------------------------------------------------------------------
教程说「数据每两行为一组」—— 两行是**同一个区间**的起点桩号与终点桩号，
其余六列在两组内重复。落库时（`roadbed_width` 表）把起终点收成
`start_station_km`/`end_station_km` 两列，一行一个区间，比"两行一组"好查。

⚠ 既然六列在两行里重复，就必须**核对它们真的一致**：不一致时哪一行算数？
本解析器**不猜** —— 取值以**组内第一行**为准（教程说"上一行为 1 或 2、下一行为 0"
时两行本就有意不同，见下），并对其它列的不一致**告警**。

★ 列 4 是**有意**允许两行不同的
-------------------------------------------------------------------------------
教程：「有无附加车道的判断标识（有附加车道时上一行为"1（或 2）"，下一行为"0"
（如果与上一行的 2 对应则是主线外侧路缘带宽度）"）」
—— 即列 4 的"上一行 1/2、下一行 0"是**格式的一部分**，不是矛盾。
故列 4 单独处理：区间取**第一行**的值（它才是"这个区间有没有附加车道"），
第二行的 0 不触发告警。

这是**设计输入**，不是派生量
-------------------------------------------------------------------------------
路幅宽度**本来就随桩号变**（加宽段、匝道、交叉口、变速车道）。本文件是它的真源；
`section_design_attr.roadway_width_m` 是由它导出的**派生标量**。
故本解析器只忠实搬运分段，**不**去算路基总宽（那是跨左右两行的派生量）。
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..errors import SourceInvalid

# HINTCAD6.00_WID_SHUJU / HINTCAD5.8_WID_SHUJU —— 版本号捕获出来存进 IR
MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_WID_SHUJU$")

SEGMENT = "roadbed_width"
FILE_KIND = "路幅宽度数据文件"
PAYLOAD_KEY = "intervals"

#: 数据行的字段数。多一列少一列都说明格式与预期不符，宁可拒绝也不猜列义。
FIELD_COUNT = 7

#: 分段标记：教程 §13.4 的 `Z`/`Y` 行（示例是一整行 z），实测 6.00 的 `[LEFT]`/`[RIGHT]`。
_SIDE_Z_RE = re.compile(r"^[zZ]+$")
_SIDE_Y_RE = re.compile(r"^[yY]+$")
_SIDE_BRACKET_RE = re.compile(r"^\[\s*(left|right|左|右)\s*\]$", re.IGNORECASE)

#: 六列宽度在 ``widths`` 里的下标（0 起，桩号已单独取出）。**用名字，不用字面数字** ——
#: 本解析器第一版把"附加车道标识"写成下标 3（实际是 2），并按下标 range(6) 取值
#: （实际只有 5 个宽度），两处都越界。列序来自教程 §13.4，错一位就会静默读错宽度。
IDX_MEDIAN = 0          # 中央分隔带宽度
IDX_HALF_CARRIAGEWAY = 1  # 半侧路面（行车道＋内侧路缘带）宽度
IDX_EXTRA_LANE_FLAG = 2   # 有无附加车道的判断标识
IDX_HARD_SHOULDER = 3     # 硬路肩宽度
IDX_EARTH_SHOULDER = 4    # 土路肩宽度
WIDTH_COUNT = 5           # 除桩号外的宽度列数

#: 桩号合理性上限（米）。实测路线 5805 m；取 10⁷ 是"大得不合理"，用来抓列错位。
STATION_MAX_M = 1.0e7

#: 单侧宽度上限（米）。实测半侧路面 3.5、路肩 0.75；取 100 是"大得不合理"。
WIDTH_MAX_M = 100.0


def detect(text: str) -> bool:
    """格式探测：这个文件像不像纬地 `.WID`？只认魔数，不靠扩展名。"""
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def _side_of(line: str) -> str | None:
    """这一行是不是分段标记？是则返回 ``'left'``/``'right'``，否则 ``None``。"""
    t = line.strip()
    if not t:
        return None
    m = _SIDE_BRACKET_RE.match(t)
    if m:
        v = m.group(1).lower()
        return "left" if v in ("left", "左") else "right"
    if _SIDE_Z_RE.match(t):                      # 教程写法：一行 Z
        return "left"
    if _SIDE_Y_RE.match(t):                      # 教程写法：一行 Y
        return "right"
    return None


def _to_float(txt: str, what: str, *, file: str | None, line_no: int) -> float:
    try:
        v = float(txt)
    except ValueError:
        raise SourceInvalid(f"{what}不是合法数字：{txt!r}",
                            file=file, line_no=line_no) from None
    if not math.isfinite(v):
        raise SourceInvalid(f"{what}不是有限数：{v}", file=file, line_no=line_no)
    return v


def _width(txt: str, what: str, *, file: str | None, line_no: int) -> float:
    v = _to_float(txt, what, file=file, line_no=line_no)
    if v < 0:
        raise SourceInvalid(f"{what}为负：{v}（宽度不可能为负）", file=file, line_no=line_no)
    if v > WIDTH_MAX_M:
        raise SourceInvalid(
            f"{what} = {v} m 超出合理区间 [0, {WIDTH_MAX_M:g}]（疑似列错位）",
            file=file, line_no=line_no)
    return v


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.WID` → ``{"vendor_version": "6.00", "intervals": [...]}``

    ``intervals`` 每项：``{side, interval_seq, start_station_m, end_station_m,
    median_width_m, half_carriageway_width_m, extra_lane_flag,
    hard_shoulder_width_m, earth_shoulder_width_m, extra_lane_file}``。

    校验策略与其余适配器一致：**宁可拒绝，不要猜。**
    路幅宽度决定路面面积与车道布置，读错一列不会报错，只会让整条路的宽度组成
    静默错位（例如把"半侧路面 3.5"读成"硬路肩 3.5"）。
    """
    if not text.strip():
        raise SourceInvalid("空文件", file=file)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_WID_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1,
        )
    version = m.group(1)

    intervals: list[dict[str, Any]] = []
    notes: list[str] = []                                # 组内两行不一致的说明，见下
    side: str | None = None
    pending: tuple[list[float], int] | None = None      # 组内第一行（起桩号行）
    seen_sides: set[str] = set()

    for i, raw in enumerate(lines[1:], start=2):
        if raw.strip() == "":
            continue                                     # 组间空行：教程示例就有，放行

        s = _side_of(raw)
        if s is not None:
            if pending is not None:
                raise SourceInvalid(
                    f"上一个桩号区间只写了起点（第 {pending[1]} 行），缺少终点行，"
                    f"就遇到了新的分段标记 —— 教程 §13.4 要求「桩号区间必须成对出现」",
                    file=file, line_no=i)
            side = s
            seen_sides.add(s)
            continue

        if side is None:
            raise SourceInvalid(
                "数据行出现在任何分段标记之前 —— 教程 §13.4 要求先用一行 Z/Y"
                "（实测 6.00 为 [LEFT]/[RIGHT]）指明后面是左侧还是右侧数据",
                file=file, line_no=i)

        parts = raw.split("\t") if "\t" in raw else raw.split()
        if len(parts) != FIELD_COUNT:
            raise SourceInvalid(
                f"字段数应为 {FIELD_COUNT}（桩号/中央分隔带/半侧路面/附加车道标识/"
                f"硬路肩/土路肩/附加车道文件名），实为 {len(parts)}：{raw.strip()[:40]!r}",
                file=file, line_no=i,
            )

        station = _to_float(parts[0].strip(), "桩号", file=file, line_no=i)
        if station < 0:
            raise SourceInvalid(f"桩号为负：{station}", file=file, line_no=i)
        if station > STATION_MAX_M:
            raise SourceInvalid(
                f"桩号 {station} 超出合理区间 [0, {STATION_MAX_M:g}]（疑似列错位）",
                file=file, line_no=i)
        widths = [_width(parts[k].strip(), f"第 {k + 1} 列", file=file, line_no=i)
                  for k in range(1, 6)]

        if pending is None:
            pending = ([station, *widths], i)             # 起点行：连行号一起存，报错时指得到
            continue

        # 组内第二行：终点桩号 + 重复的六列
        start_vals, start_line = pending
        pending = None
        start_station, *start_widths = start_vals
        end_station, *end_widths = [station, *widths]

        if end_station <= start_station:
            raise SourceInvalid(
                f"区间终点桩号 {end_station} 不大于起点 {start_station}"
                f"（第 {start_line}–{i} 行）", file=file, line_no=i)

        # 附加车道标识**有意**允许两行不同（教程：「有附加车道时上一行为"1（或 2）"，
        # 下一行为"0"」），故排除它；其余四列两行应当一致。
        _WIDTH_NAMES = {IDX_MEDIAN: "中央分隔带", IDX_HALF_CARRIAGEWAY: "半侧路面",
                        IDX_HARD_SHOULDER: "硬路肩", IDX_EARTH_SHOULDER: "土路肩"}
        diffs = [k for k in range(WIDTH_COUNT)
                 if k != IDX_EXTRA_LANE_FLAG and abs(start_widths[k] - end_widths[k]) > 1e-9]
        if diffs:
            notes.append(
                f"{'左' if side == 'left' else '右'}侧第 "
                f"{sum(1 for x in intervals if x['side'] == side) + 1} 个区间"
                f"（{start_station:.3f}–{end_station:.3f} m）两行的"
                + "、".join(_WIDTH_NAMES[k] for k in diffs)
                + "不一致，已取第一行值")

        intervals.append({
            "side": side,
            "interval_seq": sum(1 for x in intervals if x["side"] == side) + 1,
            "start_station_m": start_station,
            "end_station_m": end_station,
            "median_width_m": start_widths[IDX_MEDIAN],
            "half_carriageway_width_m": start_widths[IDX_HALF_CARRIAGEWAY],
            "extra_lane_flag": int(round(start_widths[IDX_EXTRA_LANE_FLAG])),
            "hard_shoulder_width_m": start_widths[IDX_HARD_SHOULDER],
            "earth_shoulder_width_m": start_widths[IDX_EARTH_SHOULDER],
            "extra_lane_file": parts[6].strip() if parts[6].strip() not in ("0", "0.0") else None,
        })

    if pending is not None:
        raise SourceInvalid(
            f"最后一个桩号区间只写了起点（第 {pending[1]} 行），缺少终点行 —— "
            f"教程 §13.4 要求「桩号区间必须成对出现」", file=file)
    if not intervals:
        raise SourceInvalid("没有任何桩号区间（只有魔数）", file=file)

    # ★ `notes` 在**顶层**，不放进 interval 里：interval 会被原样塞进契约⑤ 的 IR，
    #   而 IR 的 roadbed_interval 是 additionalProperties: false —— 带个 `_note`
    #   进去会被 schema 直接拒（本适配器第一版正是这样翻车的，契约当场抓住）。
    #   顶层 notes 由 build_ir 收进 IR 根部的 warnings，与 zdm.derive_grades 同一路数。
    return {"vendor_version": version, "intervals": intervals,
            "sides": sorted(seen_sides), "notes": notes}


def check_intervals(intervals: list[dict[str, Any]]) -> list[str]:
    """桩号区间的**连续性**检查。返回**告警**（可疑 ≠ 非法），不抛异常。

    教程 §13.4：「此文件桩号区间必须成对出现，**桩号区间要连续**。」

    ⚠ 这里只做**该侧内相邻区间是否首尾相接**这一件事，**不**要求覆盖整条路线 ——
    实测该工程 `.WID` 覆盖 0.000–5701.461 m，而路线是 0.000–5805.421 m，
    **最后约 104 m 没有路幅宽度数据**。那是源文件的真实缺口（属 `gaps` 一类），
    不是本文件格式错误，所以由 `check_against_stations` 单独报，不在这里误报为"不连续"。
    """
    warn: list[str] = []
    for s in ("left", "right"):
        rows = sorted((r for r in intervals if r["side"] == s),
                      key=lambda r: r["start_station_m"])
        for a, b in zip(rows, rows[1:]):
            if abs(b["start_station_m"] - a["end_station_m"]) > 1e-6:
                warn.append(
                    f"{'左' if s == 'left' else '右'}侧区间不连续："
                    f"{a['interval_seq']} 止于 {a['end_station_m']:.3f} m，"
                    f"但 {b['interval_seq']} 起于 {b['start_station_m']:.3f} m"
                    f"（教程 §13.4 要求桩号区间要连续）")
    return warn


def check_against_stations(intervals: list[dict[str, Any]],
                           stations: list[dict[str, Any]]) -> list[str]:
    """路幅宽度是否覆盖整条路线。返回告警（不抛异常）。

    ⚠ 与 `.ZDM` 的同类检查**不同**：`.ZDM` 的设计线不覆盖全线是**错误**（设计没做到头），
    而 `.WID` 不覆盖全线**是实测事实**（本工程最后约 104 m 无路幅宽度数据）——
    所以这里报的是"**源文件缺这一段**"，不是"文件格式不对"。
    下游按 `station_km` 取宽度时，落在缺口里的桩号**取不到值**，必须知道这件事。
    """
    warn: list[str] = []
    if not intervals or not stations:
        return warn
    lo, hi = stations[0]["station_m"], stations[-1]["station_m"]
    starts = min(r["start_station_m"] for r in intervals)
    ends = max(r["end_station_m"] for r in intervals)
    if starts > lo + 1e-6:
        warn.append(f"路幅宽度起点 {starts:.3f} m 晚于路线起点 {lo:.3f} m，"
                    f"前 {starts - lo:.3f} m 没有路幅宽度数据")
    if ends < hi - 1e-6:
        warn.append(f"路幅宽度终点 {ends:.3f} m 早于路线终点 {hi:.3f} m，"
                    f"后 {hi - ends:.3f} m 没有路幅宽度数据"
                    f"（实测该工程正是如此，属源文件缺口）")
    return warn


def width_at(intervals: list[dict[str, Any]], station_m: float, *,
             side: str = "left", column: str = "half_carriageway_width_m") -> float | None:
    """某桩号某侧的某个宽度。落在区间之外返回 ``None``，**不外推**。

    区间是**分段常量**（不是渐变）：纬地用相邻区间不同的常值来表达加宽，
    过渡发生在区间边界上。所以这里是**查区间**，不是插值 ——
    与 `zdm.design_elevation_at` / `sup.superelev_at` 的线性插值**刻意不同**。
    """
    for r in intervals:
        if r["side"] == side and r["start_station_m"] <= station_m <= r["end_station_m"]:
            return r.get(column)
    return None


__all__ = ["detect", "parse", "check_intervals", "check_against_stations", "width_at",
           "MAGIC_RE", "SEGMENT", "FILE_KIND", "PAYLOAD_KEY", "FIELD_COUNT",
           "STATION_MAX_M", "WIDTH_MAX_M", "IDX_MEDIAN", "IDX_HALF_CARRIAGEWAY",
           "IDX_EXTRA_LANE_FLAG", "IDX_HARD_SHOULDER", "IDX_EARTH_SHOULDER",
           "WIDTH_COUNT"]
