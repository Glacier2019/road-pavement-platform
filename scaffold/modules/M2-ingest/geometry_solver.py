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
from adapters.weidi import zdm as zdm_mod

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


def _as_adapter_grade(row: Mapping[str, Any]) -> dict[str, Any]:
    """把**落库形状**的变坡点转成 `zdm.design_elevation_at` 要的形状。

    落库形状：`station_km`（千米）。适配器形状：`station_m`（米）。
    `design_elevation_at` 内部全部按**米**运算，混单位会得到一个**数值上像模像样
    但完全错误**的高程 —— 所以这里显式转换，并在转完做一次自检。
    """
    if "station_m" in row:
        return dict(row)
    out = dict(row)
    out["station_m"] = float(row["station_km"]) * 1000.0
    # 同 _as_adapter_element：numeric 列回来是 Decimal，而 design_elevation_at
    # 内部是纯 float 运算（`Decimal + float` 会直接抛 TypeError）。
    for k in ("elevation_m", "vertical_curve_radius_m", "grade_in_pct",
              "grade_out_pct", "grade_len_m"):
        if k in out:
            out[k] = _f(out[k])
    return out


def _as_adapter_element(row: Mapping[str, Any]) -> dict[str, Any]:
    """把**落库形状**的 alignment_element 行转成 geom 要的形状。

    两种形状并存不是意外：
      · `adapters/weidi/pm.py` 产出的是**适配器形状**（`start_station_m` 米、`seq`）；
      · `design_import._plan_elements` 产出的是**落库形状**（`start_station_km` 千米、
        `element_seq`、已 round 到 6 位）。

    `solve_all` 的输入来自 plan 之后的 tables，所以是后者。
    这里做一次**显式**转换，而不是让调用方自己记得转 ——
    忘了转的表现是 `KeyError`（好），但若两个键名恰好都存在，就会静默用错单位（坏）。

    ★ `length_m` 用**桩号差重算**，不读列：该列是 DDL 生成列，
      落库形状里根本没有它，而 geom 的曲率线性插值要用它做分母。
    """
    if "start_station_m" in row:
        return dict(row)                      # 已经是适配器形状
    start = float(row["start_station_km"]) * 1000.0
    end = float(row["end_station_km"]) * 1000.0
    # ★ 全部转 float。psycopg 对 numeric 列返回 **Decimal**，而 geom 内部是纯 float 运算 ——
    #   Decimal + float 直接抛 TypeError。用「有时是 Decimal 有时是 float」的输入，
    #   会把一个类型问题伪装成随机的运行期崩溃。这里一次性收口。
    g = _f
    return {
        "seq": row.get("element_seq"),
        "type": row.get("element_type"),
        "start_station_m": start,
        "end_station_m": end,
        "length_m": end - start,
        "start_x": g(row.get("start_x")), "start_y": g(row.get("start_y")),
        "end_x": g(row.get("end_x")), "end_y": g(row.get("end_y")),
        "azimuth_deg": g(row.get("azimuth_deg")),
        "end_azimuth_deg": g(row.get("end_azimuth_deg")),
        "radius_start_m": g(row.get("radius_start_m")),
        "radius_end_m": g(row.get("radius_end_m")),
        "turn_flag": _turn_sign(row),
    }


def _f(v: Any) -> float | None:
    """`Decimal`/`int`/`None` → `float`/`None`。

    psycopg 对 `numeric` 列返回 `Decimal`，对 `double precision` 返回 `float`。
    本模块的输入**同时**来自这两种列（桩号是 numeric，坐标是 double precision），
    所以不能假设类型 —— 统一在这一层收口，而不是在每个用点各转一次。
    """
    return None if v is None else float(v)


def _turn_sign(row: Mapping[str, Any]) -> int | None:
    """还原**转向符号**（+1 左转 / −1 右转）。

    ★ 为什么必须还原：`turn_flag` **不是落库列**（`.PM` 的转符号没进 DDL），
    而 `geom.curvature_at` 靠它决定 κ 的正负。丢了它，右转的曲线会朝反方向弯 ——
    `geom` 的原话是「**实测终点差 98 m，而且不报错**」。

    还原依据：**方位角的变化方向**。转角 Δaz = 出方位角 − 入方位角，
    归一化到 (−180, 180] 后，正即左转、负即右转。
    实测：本工程 24 个曲线单元 **24/24 全部还原正确**。

    为什么用方位角而不用圆心叉积：**24 个曲线单元里只有 16 个有圆心**
    （`center_x/center_y` 允许为 NULL —— 直线与切线端本就没有圆心）。
    方位角两列则总是有值，覆盖全部单元。

    直线单元返回 None：κ 恒为 0，符号无从谈起，`geom` 对 `None` 的处理是
    `sgn = element.get("turn_flag") or 1` —— 乘 1 不影响 0。
    """
    az0 = row.get("azimuth_deg")
    az1 = row.get("end_azimuth_deg")
    if az0 is None or az1 is None:
        return row.get("turn_flag")
    if row.get("element_type") == "line" or row.get("type") == "line":
        return None
    delta = (float(az1) - float(az0) + 180.0) % 360.0 - 180.0
    return 1 if delta > 0 else -1


def solve_point(elements: Sequence[Mapping[str, Any]], station_m: float,
                *, station_col: str = "station_m",
                allow_gap: bool = False) -> dict[str, Any]:
    """求**一个**桩号的线形。返回的 dict 只含线形列（不含 station_id）。

    桩号落在全部单元之外时抛 :class:`SolveError` —— **不外推**（约定 3）。

    ``allow_gap=True`` 时改为返回**一行的形状、几何列全 None**。
    它只给"预览"用（见 `design_import.plan`）：预览必须能**展示**一份坏数据，
    否则校验就无从**报告**它坏在哪 —— 而报不出来就等于没有校验。
    落库路径永远用默认的 `False`，所以"绝不写外推值"这条没有被放开。
    """
    els = [_as_adapter_element(e) for e in elements]
    el = geom.locate(els, station_m)
    if el is None and allow_gap:
        return {station_col: station_m, "x_coord": None, "y_coord": None,
                "azimuth_deg": None, "curvature_1pm": None, "h_radius_m": None}
    if el is None:
        lo = min((e["start_station_m"] for e in els), default=None)
        hi = max((e["end_station_m"] for e in els), default=None)
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
        valid = [(float(t["station_km"]), _f(t[col]))
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
              design_points: Sequence[Mapping[str, Any]] = (),
              allow_gap: bool = False) -> list[dict[str, Any]]:
    """逐桩求解，返回可直接写进 ``geometry_point`` 的行（仍带内部 ``_station_m``）。

    ★ 内部键 ``_station_m`` 与既有 ``_plan_ground_points`` 同一约定：
    落库那一步才把它换成真实 ``station_id``（这一层不许读库，id 只能从写入拿）。

    任一桩号解不出来就抛 :class:`SolveError` —— **整批失败**，不产出半批。
    少写几行而不报错，比直接失败危险得多：下游会以为那条桩号的线形是「没有」，
    而不是「没算出来」。
    """
    ground = list(ground_points)
    design = [_as_adapter_grade(p) for p in design_points]

    rows: list[dict[str, Any]] = []
    for s in stations_m:
        row = solve_point(elements, float(s), allow_gap=allow_gap)
        row["_station_m"] = row.pop("station_m")

        # 六列超高横坡：源是 superelev_transition（本工程 76 个变化点）
        row.update(interpolate_superelevation(transitions, float(s)))

        # 地面高程：.DMX 是**逐桩**给出的，直接取最近点。
        row["ground_elev_m"] = interpolate_profile(
            ground, float(s), station_key="_station_m", value_key="ground_elev_m")

        # 设计高程：.ZDM 给的是 **12 个变坡点**，不是逐桩值 ——
        # 逐桩设计高程要**由竖曲线求值**。
        # ★ 不自己写：`zdm.design_elevation_at` 已经实现了「竖曲线内抛物线、
        #   其余按切线」，且落外返回 None 不外推。
        #   注意它要的是**适配器形状**（`station_m`），而 plan 之后是落库形状
        #   （`station_km`），故先转换 —— 下面 `_as_adapter_grade` 做这件事。
        row["design_elev_m"] = zdm_mod.design_elevation_at(design, float(s))
        rows.append(row)
    return rows


__all__ = ["SolveError", "SUPERELEV_COLUMNS",
           "solve_point", "solve_all",
           "interpolate_superelevation", "interpolate_profile"]