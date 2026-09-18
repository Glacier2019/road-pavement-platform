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

★「每两行为一组」照原样一行一行存，**不折叠成区间**
-------------------------------------------------------------------------------
教程说「数据每两行为一组」—— 但**每一行都有自己的桩号**，两行是"这个区间的
起、终点"。本解析器**照原样保留每一行**（`rows`），并给每行标上它是第几组
（`group_seq`）、该侧第几行（`seq_no`）。

为什么不在解析层折叠成区间（第一版就是这么做的，是错的）：
  · GE 域其余逐桩数据表**都按桩号寻址**（A9 `station_sequence` 的注释写明
    "其余逐桩数据表以 station_id FK 锚定本表"）。折叠成区间 = 在 GE 域**另立
    一套区间寻址**，查"某桩号的宽度"得做范围查询，与查横坡/高程的点查不一致。
  · 组内两行**本来就可以不同**：教程说列 4「有附加车道时上一行为"1（或 2）"，
    下一行为"0"」—— 折叠成区间**必然丢一个值**。
  · A15 `geometry_point` 要按桩号取宽度，点查才能直接对齐。

区间起终点由**同侧相邻两行推得**，不落库。

⚠ 除列 4 外，组内两行的其余五列应当一致；不一致时**哪一行算数**？
本解析器**不猜** —— 不报错（源文件说了算），但产出一条 `notes` 告警，
说明"这两行不一致，请人工确认"。

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
PAYLOAD_KEY = "rows"

#: 数据行的字段数。多一列少一列都说明格式与预期不符，宁可拒绝也不猜列义。
FIELD_COUNT = 7

#: 每几行为一组（教程 §13.4「数据每两行为一组」）。
ROWS_PER_GROUP = 2

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
    """解析 `.WID` → ``{"vendor_version": "6.00", "rows": [...]}``

    ``rows`` 每项（**一行源数据行 = 一个桩号**）：
    ``{side, seq_no, group_seq, station_m, median_width_m, half_carriageway_width_m,
    extra_lane_flag, hard_shoulder_width_m, earth_shoulder_width_m, extra_lane_file}``。

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

    rows: list[dict[str, Any]] = []
    notes: list[str] = []                                # 组内两行不一致的说明，见下
    side: str | None = None
    seen_sides: set[str] = set()
    last_station: dict[str, float] = {}                  # 每侧上一个桩号（必须严格递增）

    for i, raw in enumerate(lines[1:], start=2):
        if raw.strip() == "":
            continue                                     # 组间空行：教程示例就有，放行

        s_side = _side_of(raw)
        if s_side is not None:
            if rows and rows[-1]["side"] == side and rows[-1]["seq_no"] % ROWS_PER_GROUP:
                raise SourceInvalid(
                    f"上一组只写了 {rows[-1]['seq_no'] % ROWS_PER_GROUP} 行就遇到了新的"
                    f"分段标记 —— 教程 §13.4 要求「桩号区间必须成对出现」",
                    file=file, line_no=i)
            side = s_side
            seen_sides.add(s_side)
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
        seq = sum(1 for r in rows if r["side"] == side) + 1
        # 桩号递增规则：**组内严格递增，组边界允许相等**。
        # 因为教程 §13.4 要求「桩号区间要连续」—— 上一组的**终点**桩号必然等于
        # 下一组的**起点**桩号，那个桩号会**出现两次**。一刀切"严格递增"会把
        # 合法的连续区间判成错误（本适配器第一版就是这样，被自己的测试抓到）。
        is_group_start = (seq - 1) % ROWS_PER_GROUP == 0
        if side in last_station:
            prev = last_station[side]
            if station < prev or (station == prev and not is_group_start):
                raise SourceInvalid(
                    f"{'左' if side == 'left' else '右'}侧桩号未递增："
                    f"{prev} → {station}（第 {i} 行）。组内必须严格递增；"
                    f"只有新一组的起点行才允许与上一组的终点桩号相同"
                    f"（教程 §13.4「桩号区间要连续」）", file=file, line_no=i)
        last_station[side] = station

        widths = [_width(parts[k].strip(), f"第 {k + 1} 列", file=file, line_no=i)
                  for k in range(1, 6)]
        rows.append({
            "side": side,
            "seq_no": seq,
            "group_seq": (seq - 1) // ROWS_PER_GROUP + 1,
            "station_m": station,
            "median_width_m": widths[IDX_MEDIAN],
            "half_carriageway_width_m": widths[IDX_HALF_CARRIAGEWAY],
            "extra_lane_flag": int(round(widths[IDX_EXTRA_LANE_FLAG])),
            "hard_shoulder_width_m": widths[IDX_HARD_SHOULDER],
            "earth_shoulder_width_m": widths[IDX_EARTH_SHOULDER],
            "extra_lane_file": parts[6].strip() if parts[6].strip() not in ("0", "0.0") else None,
        })

    if rows and rows[-1]["seq_no"] % ROWS_PER_GROUP:
        raise SourceInvalid(
            f"最后一个桩号区间只写了起点（{rows[-1]['station_m']:.3f} m），缺少终点行 —— "
            f"教程 §13.4 要求「桩号区间必须成对出现」", file=file)
    if not rows:
        raise SourceInvalid("没有任何桩号数据（只有魔数）", file=file)

    # 组内两行（除列 4 外）应当一致 —— 不一致不报错，但要让人知道。
    _WIDTH_NAMES = {IDX_MEDIAN: "中央分隔带", IDX_HALF_CARRIAGEWAY: "半侧路面",
                    IDX_HARD_SHOULDER: "硬路肩", IDX_EARTH_SHOULDER: "土路肩"}
    for s2 in sorted(seen_sides):
        rs = [r for r in rows if r["side"] == s2]
        for a, b in zip(rs[0::2], rs[1::2]):
            diffs = [k for k in _WIDTH_NAMES
                     if abs(_row_width(a, k) - _row_width(b, k)) > 1e-9]
            if diffs:
                notes.append(
                    f"{'左' if s2 == 'left' else '右'}侧第 {a['group_seq']} 组"
                    f"（{a['station_m']:.3f}–{b['station_m']:.3f} m）两行的"
                    + "、".join(_WIDTH_NAMES[k] for k in diffs)
                    + "不一致 —— 源文件如此，请人工确认以哪一行为准")

    # ★ `notes` 在**顶层**，不放进 row 里：row 会被原样塞进契约⑤ 的 IR，
    #   而 IR 的 roadbed_point 是 additionalProperties: false —— 带内部键进去
    #   会被 schema 直接拒（本适配器第一版正是这样翻车的，契约当场抓住）。
    #   顶层 notes 由 build_ir 收进 IR 根部的 warnings，与 zdm.derive_grades 同一路数。
    return {"vendor_version": version, "rows": rows,
            "sides": sorted(seen_sides), "notes": notes}


def _row_width(row: dict[str, Any], idx: int) -> float | None:
    """按列下标取宽度（用于组内两行比对）。"""
    key = {IDX_MEDIAN: "median_width_m",
           IDX_HALF_CARRIAGEWAY: "half_carriageway_width_m",
           IDX_HARD_SHOULDER: "hard_shoulder_width_m",
           IDX_EARTH_SHOULDER: "earth_shoulder_width_m"}[idx]
    return row.get(key)


def check_stations(rows: list[dict[str, Any]]) -> list[str]:
    """桩号区间的**连续性**检查。返回**告警**（可疑 ≠ 非法），不抛异常。

    教程 §13.4：「此文件桩号区间必须成对出现，**桩号区间要连续**。」

    这里查的是：同一侧**上一组的终点行**桩号 == **下一组的起点行**桩号。
    每组两行（`group_seq` 相同的两行 = 起、终点），所以是"第 2n 行 == 第 2n+1 行"。

    ⚠ 只查**该侧内相邻组是否首尾相接**，**不**要求覆盖整条路线 ——
    实测该工程 `.WID` 覆盖 0.000–5701.461 m，而路线是 0.000–5805.421 m，
    **最后约 104 m 没有路幅宽度数据**。那是源文件的真实缺口（属 `gaps` 一类），
    不是本文件格式错误，所以由 `check_against_stations` 单独报，不在这里误报。
    """
    warn: list[str] = []
    for sd in ("left", "right"):
        rs = sorted((r for r in rows if r["side"] == sd), key=lambda r: r["station_m"])
        ends = [r for r in rs if r["seq_no"] % ROWS_PER_GROUP == 0]
        starts = [r for r in rs if r["seq_no"] % ROWS_PER_GROUP == 1]
        for a, b in zip(ends, starts[1:]):
            if abs(b["station_m"] - a["station_m"]) > 1e-6:
                warn.append(
                    f"{'左' if sd == 'left' else '右'}侧区间不连续：第 {a['group_seq']} 组"
                    f"止于 {a['station_m']:.3f} m，但第 {b['group_seq']} 组"
                    f"起于 {b['station_m']:.3f} m（教程 §13.4 要求桩号区间要连续）")
    return warn


def check_against_stations(rows: list[dict[str, Any]],
                           stations: list[dict[str, Any]]) -> list[str]:
    """路幅宽度是否覆盖整条路线。返回告警（不抛异常）。

    ⚠ 与 `.ZDM` 的同类检查**不同**：`.ZDM` 的设计线不覆盖全线是**错误**（设计没做到头），
    而 `.WID` 不覆盖全线**是实测事实**（本工程最后约 104 m 无路幅宽度数据）——
    所以这里报的是"**源文件缺这一段**"，不是"文件格式不对"。
    下游按 `station_km` 取宽度时，落在缺口里的桩号**取不到值**，必须知道这件事。
    """
    warn: list[str] = []
    if not rows or not stations:
        return warn
    lo, hi = stations[0]["station_m"], stations[-1]["station_m"]
    starts = min(r["station_m"] for r in rows)
    ends = max(r["station_m"] for r in rows)
    if starts > lo + 1e-6:
        warn.append(f"路幅宽度起点 {starts:.3f} m 晚于路线起点 {lo:.3f} m，"
                    f"前 {starts - lo:.3f} m 没有路幅宽度数据")
    if ends < hi - 1e-6:
        warn.append(f"路幅宽度终点 {ends:.3f} m 早于路线终点 {hi:.3f} m，"
                    f"后 {hi - ends:.3f} m 没有路幅宽度数据"
                    f"（实测该工程正是如此，属源文件缺口）")
    return warn


def width_at(rows: list[dict[str, Any]], station_m: float, *,
             side: str = "left", column: str = "half_carriageway_width_m") -> float | None:
    """某桩号某侧的某个宽度。**分段常量**：取"不晚于该桩号的最后一个变化点"的值。

    与 A16 `sup.superelev_at` **同一个模型**：文件里写的是**变化点**，
    值自该桩号起保持到同侧下一个变化点（纬地用相邻变化点的不同常值表达加宽，
    过渡发生在变化点上）——所以这是**查变化点**，不是插值。
    与 `zdm.design_elevation_at` 的线性插值**刻意不同**。

    早于该侧第一个变化点 → 返回 ``None``（**不外推**）。
    """
    rs = [r for r in rows if r["side"] == side]
    if not rs:
        return None
    # ⚠ 超出源文件**该侧最后一个变化点**就返回 None，不外推 ——
    #   本工程 .WID 只到 5701.461 m，而路线到 5805.421 m：最后 103.960 m 源文件
    #   根本没写宽度。沿用最后一个值是个**假设**，不是数据，所以不给。
    #   （缺口本身由 check_against_stations 报出来。）
    #   与 sup.superelev_at 的"范围外返回 None"保持同一行为。
    if station_m > max(r["station_m"] for r in rs):
        return None
    best: dict[str, Any] | None = None
    for r in rs:
        if r["station_m"] <= station_m and (best is None or r["station_m"] > best["station_m"]):
            best = r
    return best.get(column) if best else None


__all__ = ["detect", "parse", "check_stations", "check_against_stations", "width_at",
           "MAGIC_RE", "SEGMENT", "FILE_KIND", "PAYLOAD_KEY", "FIELD_COUNT",
           "STATION_MAX_M", "WIDTH_MAX_M", "IDX_MEDIAN", "IDX_HALF_CARRIAGEWAY",
           "IDX_EXTRA_LANE_FLAG", "IDX_HARD_SHOULDER", "IDX_EARTH_SHOULDER",
           "WIDTH_COUNT", "ROWS_PER_GROUP"]
