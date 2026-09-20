"""纬地 HintCAD `.ZDM` 纵断面设计文件解析器（契约⑤ 第 5 个适配器）。

文件格式（实测自 052201341 毕设工程，14 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD5.83_ZDM_SHUJU          ← 魔数，含厂商版本号
    第 2 行   `%12d`                          ← **变坡点个数**
    第 3 行起 `%12.3f\t%14.8f\t%14.8f\t%10.3f\t%10.8f`
              桩号(米)  设计高程(米)  竖曲线半径(米)  ?           ?
    行尾 CRLF

实测：计数行 = 12，数据行 = 12，0.000 → 5805.421 米。

这是一个可以直接**自我对账**的文件：计数行是它自己的声明，
数据行是它的实际内容，两者必须相等。`.STA`/`.DMX` 都没有这一行，
只能靠外部对账；这个有，就别浪费。

第 4、5 个字段 = 标高错台（含义已由教程 §13.3 确定，不再是"未知"）
-------------------------------------------------------------------------------
这两列一度按"含义未知"处理（`field4_raw`/`field5_raw`），理由是实测 12 行
**全为 0**，样本里恒为 0 的列既证明不了也证伪不了含义。

**但教程 §13.3 写得很清楚**（说明书正文，不是猜）：
  「第三行开始每行中前三项数据分别为变坡点桩号，变坡点的设计标高，竖曲线的
    半径（第一个变坡点和最后一个变坡点只能为 0）。其中**最后两项**数据是针对
    互通立交匝道上出现**标高错台**现象而设置的，分别表示**标高错台位置的桩号**
    及**错台的标高差值**（向上错开输正值，向下为负值，单位为米）。如果没有错台
    现象或当前项目为一般公路主线时，这两项数据**同时输为 0** 即可。」

所以：
  · 第 4 项 = 错台位置桩号（米）        → `offset_station_m`
  · 第 5 项 = 错台标高差（米，上正下负）→ `offset_elev_m`
  · **全 0 不是"没信息"，而是"本项目没有错台"** —— 本工程是一般公路主线，
    正是教程所说的全 0 情形。原来的"告警"是把**正常**当异常报，已删除。

附带得到一条**免费的不变量**：教程说首末变坡点的竖曲线半径**只能为 0**
（竖曲线不能伸出路线之外）。本文件首末恰为 0，故据此**拒绝**非 0 的首末半径。

竖曲线几何是**派生**的
-------------------------------------------------------------------------------
文件只给三样：变坡点桩号、设计高程、竖曲线半径。纵坡与竖曲线长度都不给，
需要推：

    grade_in_pct   = (E_i − E_{i−1}) / (S_i − S_{i−1}) × 100     ← 进入该变坡点的坡
    grade_out_pct  = (E_{i+1} − E_i) / (S_{i+1} − S_i) × 100     ← 离开该变坡点的坡
    grade_len_m    = R × |grade_out − grade_in| / 100            ← 竖曲线全长 L = R·|i₂−i₁|

与交点的 T/α/E 同一类：都是"能由输入唯一确定、但源文件不写"的量。
首尾变坡点只有一个邻坡，故 grade_in/out 为 NULL —— 不是 0（0 是个坡度的值，
NULL 才是"没有这个坡"）。R = 0 时 L = 0：确实没有竖曲线，这是真值不是缺值。
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..errors import SourceInvalid

# HINTCAD5.83_ZDM_SHUJU —— 版本号捕获出来存进 IR 的 source.vendor_version
MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_ZDM_SHUJU$")

SEGMENT = "profile_grade_point"
FILE_KIND = "纵断面设计文件"

#: 设计高程合理性区间（米）。与 .DMX 同一套界限，理由也相同：
#: 抓的是**量级错位**（桩号串进高程列），抓不了"像高程其实是别的数"。
ELEV_MIN_M = -500.0
ELEV_MAX_M = 9000.0

#: 竖曲线半径上限（米）。公路竖曲线半径实测在 10³–10⁵ 量级；
#: 取 10⁷ 是"大得不合理"，用来抓列错位。
RADIUS_MAX_M = 1.0e7

#: 数据行的字段数。多一列少一列都说明格式与预期不符，宁可拒绝也不猜列义。
FIELD_COUNT = 5

#: 错台标高差上限（米）。错台是匝道上几厘米级的标高突变，取 1 m 已是"大得不合理"，
#: 用来抓第 4/5 列错位（那两列一旦串位，数值会完全不像错台）。
OFFSET_ELEV_MAX_M = 1.0


def detect(text: str) -> bool:
    """格式探测：这个文件像不像纬地 `.ZDM`？只认魔数，不靠扩展名。"""
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.ZDM` → ``{"vendor_version": "5.83", "points": [...]}``

    ``points`` 每项：``{vpi_seq, station_m, elevation_m, vertical_curve_radius_m,
    offset_station_m, offset_elev_m}``（后两个是标高错台，见模块 docstring）。

    校验策略与 `.STA`/`.DMX` 一致：**宁可拒绝，不要猜。**
    设计高程直接决定填挖方与路面结构厚度，读错一列不会报错，只会让整条路的设计
    反着来。所以每种畸形都抛 `SourceInvalid`。
    """
    if not text.strip():
        raise SourceInvalid("空文件", file=file)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_ZDM_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1,
        )
    version = m.group(1)

    if len(lines) < 2 or not lines[1].strip():
        raise SourceInvalid("缺少计数行（第 2 行应为变坡点个数）", file=file, line_no=2)
    try:
        declared = int(lines[1].strip())
    except ValueError:
        raise SourceInvalid(f"计数行不是整数：{lines[1].strip()[:30]!r}",
                            file=file, line_no=2) from None
    if declared < 0:
        raise SourceInvalid(f"计数行为负：{declared}", file=file, line_no=2)

    points: list[dict[str, Any]] = []
    for i, raw in enumerate(lines[2:], start=3):
        # 末尾空行放行（导出工具常留一行），中间空行意味着漏读一段设计线。
        # 注意：空行不计入"数据行数"，所以下面的计数核对不受末尾空行影响。
        if raw.strip() == "":
            if all(l.strip() == "" for l in lines[i:]):
                break
            raise SourceInvalid("文件中间出现空行（疑似漏读一段设计线）", file=file, line_no=i)

        parts = raw.split("\t") if "\t" in raw else raw.split()
        if len(parts) != FIELD_COUNT:
            raise SourceInvalid(
                f"字段数应为 {FIELD_COUNT}（桩号/设计高程/竖曲线半径/两列未知），"
                f"实为 {len(parts)}：{raw.strip()[:40]!r}",
                file=file, line_no=i,
            )

        def _f(idx: int, what: str) -> float:
            txt = parts[idx].strip()
            try:
                v = float(txt)
            except ValueError:
                raise SourceInvalid(f"{what}不是合法数字：{txt!r}",
                                    file=file, line_no=i) from None
            if not math.isfinite(v):
                raise SourceInvalid(f"{what}不是有限数：{v}", file=file, line_no=i)
            return v

        station_m = _f(0, "桩号")
        elevation_m = _f(1, "设计高程")
        radius_m = _f(2, "竖曲线半径")
        offset_station_m = _f(3, "错台位置桩号")
        offset_elev_m = _f(4, "错台标高差")

        if station_m < 0:
            raise SourceInvalid(f"桩号为负：{station_m}", file=file, line_no=i)
        if not (ELEV_MIN_M <= elevation_m <= ELEV_MAX_M):
            raise SourceInvalid(
                f"设计高程 {elevation_m} m 超出合理区间 [{ELEV_MIN_M}, {ELEV_MAX_M}]"
                f"（疑似列错位）", file=file, line_no=i)
        if radius_m < 0:
            raise SourceInvalid(f"竖曲线半径为负：{radius_m}", file=file, line_no=i)
        if radius_m > RADIUS_MAX_M:
            raise SourceInvalid(
                f"竖曲线半径 {radius_m} m 超出合理区间 [0, {RADIUS_MAX_M:g}]"
                f"（疑似列错位）", file=file, line_no=i)
        if points and station_m <= points[-1]["station_m"]:
            # 变坡点必须**严格**递增：两个同桩号的变坡点在几何上无法解释
            raise SourceInvalid(
                f"桩号未严格递增：{points[-1]['station_m']} → {station_m}"
                f"（同桩号的两个变坡点无法解释）", file=file, line_no=i)

        # 错台：教程 §13.3 说"没有错台现象或当前项目为一般公路主线时，这两项
        # 同时输为 0"。两个 0 是**正常**（本项目就是这样），不是缺值。
        # 但"一个 0 一个非 0"自相矛盾 —— 错台要么有桩号有高差，要么都没有。
        if (offset_station_m == 0.0) != (offset_elev_m == 0.0):
            raise SourceInvalid(
                f"错台两列只有一项为 0（桩号 {offset_station_m} / 高差 {offset_elev_m}）"
                f"—— 错台要么有位置有高差，要么都没有，半截数据无法解释",
                file=file, line_no=i)
        if offset_station_m < 0:
            raise SourceInvalid(f"错台位置桩号为负：{offset_station_m}", file=file, line_no=i)
        if abs(offset_elev_m) > OFFSET_ELEV_MAX_M:
            raise SourceInvalid(
                f"错台标高差 {offset_elev_m} m 超出合理区间 "
                f"[{-OFFSET_ELEV_MAX_M}, {OFFSET_ELEV_MAX_M}]（疑似列错位）",
                file=file, line_no=i)

        points.append({
            "vpi_seq": len(points) + 1,
            "station_m": station_m,
            "elevation_m": elevation_m,
            "vertical_curve_radius_m": radius_m,
            "offset_station_m": offset_station_m,
            "offset_elev_m": offset_elev_m,
        })

    # 自带计数行是一件礼物：拿它对自己的内容，不对外部文件。
    if declared != len(points):
        raise SourceInvalid(
            f"计数行声明 {declared} 个变坡点，实际读到 {len(points)} 个"
            f"（文件自我矛盾，说明漏读或多读）", file=file)
    if len(points) < 2:
        raise SourceInvalid(f"变坡点不足 2 个（实为 {len(points)} 个），构不成纵断面",
                            file=file)

    # ★ 教程 §13.3：「竖曲线的半径（第一个变坡点和最后一个变坡点**只能为 0**）」
    #   —— 竖曲线不能伸出路线之外，首末变坡点只能是一侧切线。这不是"没写"，
    #   是**规定必须为 0**；非 0 说明文件与规范不符（或列错位），宁可拒绝。
    for p in (points[0], points[-1]):
        if p["vertical_curve_radius_m"] != 0.0:
            raise SourceInvalid(
                f"{'首' if p is points[0] else '末'}个变坡点（桩号 "
                f"{p['station_m']}）的竖曲线半径应为 0（教程 §13.3），"
                f"实为 {p['vertical_curve_radius_m']}",
                file=file)

    return {"vendor_version": version, "points": points}


def derive_grades(points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """就地补出 ``grade_in_pct`` / ``grade_out_pct`` / ``grade_len_m``，返回 (points, 告警)。

    这些都是**派生量**（源文件不写，但由输入唯一确定），与交点的 T/α/E 同类。
    边界语义：
      · 首个变坡点没有"进入"的坡 → ``grade_in_pct = None``；末个反之。
      · ``R = 0`` 表示**确实没有竖曲线** → ``grade_len_m = 0.0``（真值，非缺值）。
      · 若 R > 0 却缺一个邻坡 → ``grade_len_m = None`` 并告警（不能算，也不假装是 0）。
    """
    warn: list[str] = []
    for i, p in enumerate(points):
        p["grade_in_pct"] = None
        p["grade_out_pct"] = None
        p["grade_len_m"] = None
        if i > 0:
            ds = p["station_m"] - points[i - 1]["station_m"]
            if ds <= 0:                       # parse 已挡住，这里是纵深防御
                warn.append(f"变坡点 {p['vpi_seq']} 与前点桩号未递增（{ds} m），坡度为 None")
            else:
                p["grade_in_pct"] = (p["elevation_m"] - points[i - 1]["elevation_m"]) / ds * 100.0
        if i < len(points) - 1:
            ds = points[i + 1]["station_m"] - p["station_m"]
            if ds <= 0:
                warn.append(f"变坡点 {p['vpi_seq']} 与后点桩号未递增（{ds} m），坡度为 None")
            else:
                p["grade_out_pct"] = (points[i + 1]["elevation_m"] - p["elevation_m"]) / ds * 100.0

        r = p["vertical_curve_radius_m"]
        if r == 0:
            p["grade_len_m"] = 0.0            # 没有竖曲线，长度确实是 0
        elif p["grade_in_pct"] is None or p["grade_out_pct"] is None:
            warn.append(f"变坡点 {p['vpi_seq']} 半径 R={r} 但缺一侧纵坡，竖曲线长无法推得 → None")
        else:
            p["grade_len_m"] = r * abs(p["grade_out_pct"] - p["grade_in_pct"]) / 100.0

    return points, warn


def check_against_stations(points: list[dict[str, Any]],
                           stations: list[dict[str, Any]]) -> list[str]:
    """设计线必须**覆盖整条路**：首尾桩号要与桩号序列一致。返回告警（不抛异常）。

    为什么只查首尾、不查逐条：`.ZDM` 只有十几个变坡点，本来就不该逐桩对齐；
    但设计线起终点与路线起终点必须一致 —— 短了就是设计没做到头，
    长了就是设计越出了路线范围，两种都是真问题。
    """
    warn: list[str] = []
    if not points or not stations:
        return warn
    if abs(points[0]["station_m"] - stations[0]["station_m"]) > 1e-6:
        warn.append(f"设计线起点 {points[0]['station_m']} m 与桩号序列起点 "
                    f"{stations[0]['station_m']} m 不一致")
    if abs(points[-1]["station_m"] - stations[-1]["station_m"]) > 1e-6:
        warn.append(f"设计线终点 {points[-1]['station_m']} m 与桩号序列终点 "
                    f"{stations[-1]['station_m']} m 不一致")
    return warn


def vertical_curve_of(point: dict[str, Any]) -> dict[str, Any] | None:
    """一个变坡点的竖曲线参数；没有竖曲线时返回 ``None``。

    **曲线以变坡点为中心**：BVC = 变坡点桩号 − T，EVC = 变坡点桩号 + T（L = 2T）。
    ω = i出 − i入（小数），L = R·|ω|，T = L/2，E = |ω|·L/8。

    为什么要把"以变坡点为中心"写进注释：我第一版把曲线放在
    ``[变坡点 + T, 变坡点 + T + L]``，也就是整条曲线甩在变坡点**之后**。
    那样每个桩号的高程都会整体偏 T，而且**不报任何错**。真实数据一验就露了：
    曲线在变坡点处应比切线交点低（凸）或高（凹）一个外距 E，错位版本算出来
    恰好**等于**交点高程，差值 0.0000 —— 一眼可辨。

    要求 ``derive_grades`` 已经跑过（本函数读 grade_in/out_pct）。
    """
    i1 = point.get("grade_in_pct")
    i2 = point.get("grade_out_pct")
    R = point.get("vertical_curve_radius_m")
    if i1 is None or i2 is None or not R:
        return None
    w = (i2 - i1) / 100.0
    L = R * abs(w)
    if L <= 0:                    # 前后同坡 → 没有竖曲线（不是"半径为零的曲线"）
        return None
    T = L / 2.0
    return {
        "omega": w,
        "len_m": L,
        "tangent_len_m": T,
        "bvc_station_m": point["station_m"] - T,
        "evc_station_m": point["station_m"] + T,
        "bvc_elev_m": point["elevation_m"] - (i1 / 100.0) * T,
        "external_m": abs(w) * L / 8.0,
    }


def check_vertical_curves(points: list[dict[str, Any]]) -> list[str]:
    """竖曲线之间的位置检查。返回**告警**（可疑 ≠ 非法），不抛异常。

    两条：
      · 相邻曲线不得重叠（前一条的 EVC 不能超过后一条的 BVC）；
      · 一条曲线不得把**别的变坡点**包进去（否则那个变坡点的切线交点落在曲线内部，
        几何上自相矛盾）。
    这两种情况都会让"某桩号属于哪条曲线"出现歧义，而歧义不该静默地随便挑一条。
    """
    warn: list[str] = []
    curves = [(p, vertical_curve_of(p)) for p in points]
    curves = [(p, c) for p, c in curves if c]
    for (p1, c1), (p2, c2) in zip(curves, curves[1:]):
        if c1["evc_station_m"] > c2["bvc_station_m"] + 1e-9:
            warn.append(
                f"竖曲线重叠：VPI{p1['vpi_seq']} 的 EVC={c1['evc_station_m']:.3f} m "
                f"超过了 VPI{p2['vpi_seq']} 的 BVC={c2['bvc_station_m']:.3f} m")
    for p, c in curves:
        for q in points:
            if q is p:
                continue
            if c["bvc_station_m"] < q["station_m"] < c["evc_station_m"]:
                warn.append(
                    f"竖曲线越界：VPI{p['vpi_seq']} 的曲线 "
                    f"[{c['bvc_station_m']:.3f}, {c['evc_station_m']:.3f}] "
                    f"把 VPI{q['vpi_seq']}（{q['station_m']:.3f} m）包在里面")
    return warn


def design_elevation_at(points: list[dict[str, Any]],
                        station_m: float) -> float | None:
    """某桩号的**设计高程**：竖曲线内按抛物线，其余按切线。

    抛物线用工程上通用的二次式：以 BVC 为原点，x 沿桩号增大方向，
        y = y_BVC + i入·x + (ω / (2L))·x²
    在 x = T（即变坡点处）得到 y = 交点高程 + sign(ω)·E —— 凸则低、凹则高，
    与"外距"的定义一致（这条在 tests 里被钉住）。

    桩号**落在已知范围之外时返回 None，不做外推**：外推出来的高程看着像真的，
    会被下游当成设计值用。宁可没有。
    要求 ``derive_grades`` 已经跑过。
    """
    if not points:
        return None
    if station_m < points[0]["station_m"] or station_m > points[-1]["station_m"]:
        return None
    for p in points:                       # ① 落在某条竖曲线内
        c = vertical_curve_of(p)
        if c and c["bvc_station_m"] <= station_m <= c["evc_station_m"]:
            x = station_m - c["bvc_station_m"]
            return (c["bvc_elev_m"] + (p["grade_in_pct"] / 100.0) * x
                    + (c["omega"] / (2.0 * c["len_m"])) * x * x)
    for a, b in zip(points, points[1:]):   # ② 否则按切线在两变坡点间插值
        if a["station_m"] <= station_m <= b["station_m"]:
            if a["grade_out_pct"] is None:
                return None
            return a["elevation_m"] + (a["grade_out_pct"] / 100.0) * (
                station_m - a["station_m"])
    return None


PAYLOAD_KEY = "points"

__all__ = ["detect", "parse", "derive_grades", "check_against_stations",
           "vertical_curve_of", "check_vertical_curves", "design_elevation_at", "MAGIC_RE",
           "SEGMENT", "FILE_KIND", "PAYLOAD_KEY", "FIELD_COUNT",
           "ELEV_MIN_M", "ELEV_MAX_M", "RADIUS_MAX_M"]
