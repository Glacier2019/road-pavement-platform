"""逐桩线形求解：由**线形要素**算出 `geometry_point` 的每一行。

为什么要有这个模块
-------------------------------------------------------------------------------
`geometry_point`（A15）是**派生缓存** —— 全部由 A12（`alignment_element`）
＋ A13/A14（高程）逐桩算出。DDL 第 553–556 行已写明：

    之所以「存」而不是「每次算」：缓和曲线要数值积分，逐桩重算代价高 ——
    这是合理的缓存，不是冗余。但有两条要求：
      ① 必须**可重建**（导入器要能一键重算，并断言重算结果一致）；
      ② 必须与 A12 **同批次生成**。

本模块就是那个「一键重算」的入口。

★ 它**不含任何几何数学** —— 线形求值全部委托给 `adapters.geom`：

    geom.locate       桩号落在哪个单元（落外返回 None，**不外推**）
    geom.curvature_at κ(s)
    geom.azimuth_at   θ(s)
    geom.point_at     (x, y)(s)

复算一遍是浪费，更重要的是：几何求值有一套已验证的实现（实测最大坐标偏差
3.17×10⁻⁸ m，见 `tests/contract/test_design_import.py`），另起炉灶必然漂移。

三条不得违反的约定（违反会**静默出错**，不报错但结果是错的）
-------------------------------------------------------------------------------
1. `radius_start_m = None` 表示**无穷大半径**（κ=0），**不是缺值**。
   缓和曲线起点正是这样：R 从 ∞ 渐变到 R。`geom.curvature_at` 已正确处理，
   本模块不要「帮忙」把它当 0 填进任何列。
2. 曲率符号来自 `turn_flag`（+1 左转 / −1 右转）；`radius_*_m` 恒为正。
   `geom` 内部已取符号。若绕过它自己算，记住 `geom` 的原话：
   「漏掉转向，右转的曲线会朝反方向弯 —— 实测终点差 98 m，**而且不报错**」。
3. `geom.locate` 对落在单元链外的桩号返回 `None`，**不外推**。
   本模块遇到 `None` 一律抛 :class:`SolveError` 并**指名桩号**，
   绝不跳过、绝不填默认值 —— 悄悄少一行比报错危险得多。

NULL 的语义（不是缺值，是「有意为空」）
-------------------------------------------------------------------------------
`superelev_transition` 的六列横坡里，NULL 表示源文件写了 9999「可以忽略此数据」，
即该列在此位置**不参与约束、过渡照常继续**（DDL 注释原文）。
所以插值遇到 NULL 时是**跳过该点、继续找下一个有效点**，
而不是把它当 0、也不是沿用上一个值 —— 那两种做法都会造出一个源文件里没有的坡。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from adapters import geom

# 六列横坡：物理列名 → 源列名（`superelev_transition` 与 `geometry_point` 同名同义）
SUPERELEV_COLUMNS = (
    "earth_shoulder_left_pct",
    "hard_shoulder_left_pct",
    "lane_left_pct",
    "lane_right_pct",
    "hard_shoulder_right_pct",
    "earth_shoulder_right_pct",
)


class SolveError(ValueError):
    """逐桩求解失败。继承 ValueError —— 它是数据问题，不是程序错误。"""


def solve_point(elements: Sequence[Mapping[str, Any]], station_m: float,
                *, station_col: str = "station_m") -> dict[str, Any]:
    """求**一个**桩号的线形。返回的 dict 只含线形列（不含 station_id）。

    桩号落在全部单元之外时抛 :class:`SolveError` —— **不外推**（约定 3）。
    """
    el = geom.locate(list(elements), station_m)
    if el is None:
        lo = min((e["start_station_m"] for e in elements), default=None)
        hi = max((e["end_station_m"] for e in elements), default=None)
        raise SolveError(
            f"桩号 {station_m} m 不在任何线形单元内（单元链覆盖 "
            f"{lo}~{hi} m）—— `locate` 不外推，故此处直接拒收。"
            f"若要一个链外的点，应补线形要素，而不是让求解器猜。")
    x, y = geom.point_at(el, station_m)
    return {
        station_col: station_m,
        "x_coord": x,
        "y_coord": y,
        "azimuth_deg": geom.azimuth_at(el, station_m),
        "curvature_1pm": geom.curvature_at(el, station_m),
        "h_radius_m": _radius_of(el),
    }


def _radius_of(element: Mapping[str, Any]) -> float | None:
    """该单元的平曲线半径。直线与缓和曲线起点没有有限半径 → None。

    ★ 返回 None 而**不是 0**：约定 1 说得很清楚，`None` 是「无穷大半径」，
    填 0 会读成「半径为 0 的急弯」，那是完全相反的意思。
    """
    r = element.get("radius_end_m") or element.get("radius_start_m")
    return float(r) if r else None


def interpolate_superelevation(
        transitions: Sequence[Mapping[str, Any]], station_m: float) -> dict[str, float | None]:
    """把 `superelev_transition` 的六列横坡**插值**到 `station_m`。

    规则（DDL 注释 + 实测）：
      · 变化点按桩号排序；找夹住 `station_m` 的相邻两点，线性插值。
      · 某列在端点为 **NULL（= 源文件 9999「可忽略」）**时，
        该列**跳过该点寻找下一个有效点** —— 不是缺值、不是 0、不是沿用上值。
      · 区间外（早于首点/晚于末点）→ 取最近的有效值（**不外推**，
        因为横坡是分段常量式的设计值，外推会造出源文件里没有的坡）。
    """
    out: dict[str, float | None] = {c: None for c in SUPERELEV_COLUMNS}
    if not transitions:
        return out
    pts = sorted(transitions, key=lambda t: float(t["station_km"]))

    for col in SUPERELEV_COLUMNS:
        # 只保留该列**有效**的点（NULL 不参与约束，见 docstring）
        valid = [(float(t["station_km"]), float(t[col]))
                 for t in pts if t.get(col) is not None]
        if not valid:
            continue
        out[col] = _lerp(valid, station_m)
    return out


def _lerp(points: list[tuple[float, float]], x: float) -> float:
    """在按 x 升序的有效点上线性插值；区间外取端点值。"""
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[-1][1]   # 浮点边界兜底；上面已覆盖全部区间


def interpolate_profile(
        points: Iterable[Mapping[str, Any]], station_m: float,
        *, station_key: str, value_key: str) -> float | None:
    """把一条**逐桩**序列（地面线/设计线）在 `station_m` 处取最近值。

    为什么取最近而不是插值：这两条线的源（`.DMX`/`.ZDM` 经既有 plan）本身
    就是逐桩给出的，`geometry_point` 的桩号序列与它们**同源**（都来自 `.STA`），
    所以正常情况下是**精确命中**。取最近只是对浮点误差的兜底。
    命中不到任何点 → None（**不填 0**：0 米高程会读成「海平面」，是假数据）。
    """
    best: float | None = None
    best_d = float("inf")
    for r in points:
        v = r.get(value_key)
        if v is None:
            continue
        d = abs(float(r[station_key]) - station_m)
        if d < best_d:
            best_d, best = d, float(v)
    return best


def solve_all(elements: Sequence[Mapping[str, Any]],
              stations_m: Sequence[float],
              *,
              transitions: Sequence[Mapping[str, Any]] = (),
              ground_points: Iterable[Mapping[str, Any]] = (),
              design_points: Iterable[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """逐桩求解，返回可直接写进 ``geometry_point`` 的行（仍带内部 ``_station_m``）。

    ★ 内部键 ``_station_m`` 与既有 ``_plan_ground_points`` 同一约定：
    落库那一步才把它换成真实 ``station_id``（这一层不许读库，id 只能从写入拿）。

    任一桩号解不出来就抛 :class:`SolveError` —— **整批失败**，不产出半批。
    少写几行而不报错，比直接失败危险得多：下游会以为那条桩号的线形是「没有」，
    而不是「没算出来」。
    """
    ground = list(ground_points)
    design = list(design_points)

    rows: list[dict[str, Any]] = []
    for s in stations_m:
        row = solve_point(elements, float(s))
        row["_station_m"] = row.pop("station_m")

        # 六列超高横坡：源是 superelev_transition（本工程 76 个变化点）
        row.update(interpolate_superelevation(transitions, float(s)))

        # 地面高程（.DMX 逐桩）与设计高程（.ZDM 竖曲线）
        row["ground_elev_m"] = interpolate_profile(
            ground, float(s), station_key="_station_m", value_key="ground_elev_m")
        row["design_elev_m"] = interpolate_profile(
            design, float(s), station_key="station_km", value_key="design_elev_m")
        rows.append(row)
    return rows


__all__ = ["SolveError", "SUPERELEV_COLUMNS",
           "solve_point", "solve_all",
           "interpolate_superelevation", "interpolate_profile"]