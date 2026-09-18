"""平面线形几何：由**线形单元链**推导**交点**（厂商无关，纯几何）。

为什么要有这个模块
-------------------------------------------------------------------------------
契约⑤ 的两个平面段，是**同一个东西的两种记法**：

    `alignment_element`（.pm）＝ 实际铺出来的线：逐段的桩号区间、坐标、方位角、半径
    `alignment_pi`（.JD）     ＝ 折点：相邻两条切线的交点

折点是线的**摘要** —— 两条相邻切线求交即得。所以：

  · 只有 `.pm` 时，交点可以推出来，不必再要求用户额外提供 `.JD`；
  · `.JD` 存在时，它就从「输入」**降为验算** —— 一份独立来源的第二意见。

实测（G228 滨海大道试验段，33 单元 → 8 交点）：推导出的交点坐标与 `.JD` 最大偏差
**3×10⁻⁸ m**（8 位小数的浮点噪声），转角／切线长／交点桩号／缓和曲线参数 A
全部逐位相同。**所以这不是近似拟合，是同一组数的两种记法。**

因此 `.JD` 不该是数据的入口：一个"看起来也有值"的入口，只会多一处能悄悄写歪的地方。
它的正确位置是**校验**（见 `weidi.build_ir` 里的交叉印证）。

数学约定（与 `jd.py` 保持一致）
-------------------------------------------------------------------------------
X 为北、Y 为东（高斯平面）；方位角 = ``atan2(ΔY, ΔX)``，顺时针为正；
转角 = 出方位角 − 入方位角，归一化到 (−180, 180]。

⚠ 本模块**不猜**：切线平行（求不出交点）时报错而不是返回 None 让下游去填空。
"""
from __future__ import annotations

import math
from typing import Any

from .errors import SourceInvalid

TOL_PARALLEL = 1e-12


def curve_groups(elements: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """把线形单元切成**曲线组**：连续的「缓和曲线 / 圆曲线」单元构成一组。

    组与组之间必然是直线（若两组直接相邻，说明是复曲线——本模块暂不支持，
    会因切线求交退化而在 `derive_control_points` 里报错，而不是算出一个错的交点）。
    """
    groups: list[list[dict[str, Any]]] = []
    for e in elements:
        if e["type"] in ("transition", "circular"):
            if groups and groups[-1][-1]["seq"] == e["seq"] - 1:
                groups[-1].append(e)
            else:
                groups.append([e])
    return groups


def _intersect(p1: tuple[float, float], a1: float,
               p2: tuple[float, float], a2: float) -> tuple[float, float]:
    """两条切线（点 + 方位角）的交点。平行则报错——**不返回 None**。"""
    d1 = (math.cos(math.radians(a1)), math.sin(math.radians(a1)))
    d2 = (math.cos(math.radians(a2)), math.sin(math.radians(a2)))
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < TOL_PARALLEL:
        raise SourceInvalid(f"两条切线平行（方位角 {a1}° / {a2}°），求不出交点；"
                            f"若为复曲线（两组曲线直接相邻），本模块暂不支持")
    t = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])


def derive_control_points(elements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """由单元链推导交点列表。字段与 `weidi.jd.parse()` 的控制点**同名同义**，
    便于直接对质；也符合契约⑤ 的 `pi_point` 定义。

    `type_code` 与 `prev_tangent_len_m` 之外的一切都是**算出来的**：
    交点坐标＝两切线求交；转角＝方位角之差；切线长＝ZH 到交点的距离；
    交点桩号＝ZH 桩号＋切线长；A＝√(R·Ls)；外距＝(R+ΔR)/cos(α/2)−R。
    """
    if not elements:
        return []
    groups = curve_groups(elements)
    out: list[dict[str, Any]] = []

    for idx, g in enumerate(groups):
        first, last = g[0], g[-1]
        p_in = (first["start_x"], first["start_y"])
        p_out = (last["end_x"], last["end_y"])
        az_in, az_out = first["azimuth_deg"], last["end_azimuth_deg"]
        X = _intersect(p_in, az_in, p_out, az_out)

        alpha = (az_out - az_in + 180.0) % 360.0 - 180.0
        zh, hz = first["start_station_m"], last["end_station_m"]

        circ = next((e for e in g if e["type"] == "circular"), None)
        radius = (circ["radius_start_m"] if circ else None) or \
                 next((e["radius_end_m"] or e["radius_start_m"] for e in g), None)
        if radius is None:
            raise SourceInvalid(f"曲线组 {first['seq']}–{last['seq']} 里找不到有限半径，"
                                f"推不出交点参数")

        ls1 = first["length_m"] if first["type"] == "transition" else None
        ls2 = last["length_m"] if last["type"] == "transition" else None

        # 曲线组与直线组的四个分界桩号；QZ 曲中点取圆曲线区间的中点
        hy = first["end_station_m"] if ls1 is not None else zh
        yh = last["start_station_m"] if ls2 is not None else hz
        qz = (circ["start_station_m"] + circ["end_station_m"]) / 2 if circ else (zh + hz) / 2

        # 曲中点 QZ 的**精确坐标**：圆心 + R × (HY、YH 两个方向的角平分线方向)。
        # ⚠ 外距**不要**用教科书的 E = (R+ΔR)/cos(α/2) − R：
        #   那是级数近似。实测它对 PI8 差 1.24 mm（一项 ΔR）、0.8 µm（二项 ΔR），
        #   而精确几何差 6×10⁻⁹ m。照公式写会永远差 1 毫米而无人察觉——
        #   圆心和 HY/YH 坐标都在手边，距离直接量就行，不需要展开。
        external = None
        if circ and circ.get("center"):
            cxy = (circ["center"]["x"], circ["center"]["y"])
            v1 = (first["end_x"] - cxy[0], first["end_y"] - cxy[1])      # 圆心 → HY
            v2 = (last["start_x"] - cxy[0], last["start_y"] - cxy[1])    # 圆心 → YH
            n1, n2 = math.hypot(*v1), math.hypot(*v2)
            bx, by = v1[0] / n1 + v2[0] / n2, v1[1] / n1 + v2[1] / n2
            nb = math.hypot(bx, by)
            qz_xy = (cxy[0] + radius * bx / nb, cxy[1] + radius * by / nb)
            external = math.dist(X, qz_xy)
        else:
            # 没有圆曲线单元（无弧的曲线组）时没有"圆曲线中点"，
            # 退回二项级数，并明确标注这是近似值。
            ls_for_dr = ls1 or ls2 or 0.0
            dr = ls_for_dr ** 2 / (24.0 * radius) - ls_for_dr ** 4 / (2688.0 * radius ** 3)
            external = (radius + dr) / math.cos(math.radians(abs(alpha) / 2.0)) - radius

        # 前段直线长：紧邻本组之前那个直线单元的长度；没有直线单元就用桩号差
        prev_tan = None
        if idx > 0:
            prev_tan = groups[idx - 1][-1]["end_station_m"]
        else:
            prev_tan = 0.0
        lead = next((e for e in elements
                     if e["type"] == "line" and abs(e["end_station_m"] - zh) < 1e-9), None)
        prev_tan_len = lead["length_m"] if lead is not None else (zh - prev_tan)

        out.append({
            "seq": idx + 1,
            "tag": str(idx + 1),
            "type_code": None,                    # 厂商字段，推导不出也不该编
            "x": X[0],
            "y": X[1],
            "station_m": zh + math.dist(p_in, X),
            "azimuth_deg": round(az_in, 6),
            "deflection_deg": round(alpha, 6),
            "feat_stations_m": [zh, hy, qz, yh, hz],
            "prev_tangent_len_m": prev_tan_len,
            "radius_m": radius,
            "spiral_ls1": ls1,
            "spiral_ls2": ls2,
            "spiral_a1": math.sqrt(radius * ls1) if ls1 else None,
            "spiral_a2": math.sqrt(radius * ls2) if ls2 else None,
            "tangent_len_m": math.dist(p_in, X),
            "tangent_len2_m": math.dist(X, p_out),
            "arc_len_m": circ["length_m"] if circ else None,
            "curve_len_m": sum(e["length_m"] for e in g),
            "external_m": external,
            "pi_type": "JD",
        })
    return out


# ── 平面线形：由线元推任意桩号的曲率 / 方位角 / 坐标 ─────────────────────────
#
# 三条约定全部是**从真实数据里读出来的**，我最初凭记忆写错过两条：
#   · ``radius_start_m = None`` 表示**无穷大半径**（曲率 0），**不是缺值**。
#     缓和曲线起点就是这种情况：R 从 ∞ 渐变到 R。
#   · 曲率符号来自 ``turn_flag``：+1 左转、−1 右转；``radius_*_m`` 本身恒为正。
#     漏掉转向，右转的曲线会朝反方向弯 —— 实测终点差 98 m，而且不报错。
#   · 曲率沿线元**线性**：直线恒 0、圆曲线恒 1/R、缓和曲线 0→1/R。
#
# 判据是源文件**自带的** ``end_x`` / ``end_y`` / ``end_azimuth_deg`` —— 纬地自己
# 算的，独立于这里的公式。33 个真实线元全部吻合到 6.6e-7 m / 1.6e-9°。


def curvature_at(element: dict[str, Any], station_m: float) -> float:
    """线元内某桩号的曲率（1/m）。左转为正、右转为负。"""
    sgn = element.get("turn_flag") or 1
    r0 = element.get("radius_start_m")
    r1 = element.get("radius_end_m")
    k0 = 0.0 if not r0 else sgn / r0          # R=None → 无穷大半径 → 曲率 0
    k1 = 0.0 if not r1 else sgn / r1
    length = element["length_m"]
    if length <= 0:
        return k0
    return k0 + (k1 - k0) * (station_m - element["start_station_m"]) / length


def _need_start_azimuth(element: dict[str, Any]) -> float:
    """取线元起点方位角；缺了就明确报错。

    方位角是**起点给出的初值**，推不出、也没法默认成 0 —— 默认成 0 会得到一条
    朝向正东的假线形，看着正常、全错。所以这里宁可直接失败。
    （这条护栏是因为我在测试里造了个 azimuth_deg=None 的线元，得到的是
    一句 TypeError，读不出"缺了什么"。）
    """
    az = element.get("azimuth_deg")
    if az is None:
        raise SourceInvalid(
            f"线元 seq={element.get('seq')} 缺 azimuth_deg：方位角是起点初值，"
            f"推不出来，也不能默认成 0")
    return az


def azimuth_at(element: dict[str, Any], station_m: float) -> float:
    """线元内某桩号的方位角（度，0–360）。

    曲率线性 → ∫κ ds 是二次式，所以这里是**闭式精确**的，不靠数值积分：
        Δθ = κ0·d + (κ1 − κ0)·d² / (2L)
    """
    sgn = element.get("turn_flag") or 1
    r0 = element.get("radius_start_m")
    r1 = element.get("radius_end_m")
    k0 = 0.0 if not r0 else sgn / r0
    k1 = 0.0 if not r1 else sgn / r1
    d = station_m - element["start_station_m"]
    length = element["length_m"]
    dtheta = k0 * d
    if length > 0:
        dtheta += (k1 - k0) * d * d / (2.0 * length)
    return (_need_start_azimuth(element) + math.degrees(dtheta)) % 360.0


def point_at(element: dict[str, Any], station_m: float,
             *, steps: int = 64) -> tuple[float, float]:
    """线元内某桩号的平面坐标 (x, y)。

    对 ``∫(cosθ, sinθ) ds`` 用**复合 Simpson**：方位角是精确的，只有这一步是数值的。
    直线与圆曲线其实有闭式，缓和曲线（回旋线）要 Fresnel 积分、没有初等闭式，
    所以统一走 Simpson —— 一条路，且误差可控（steps=64 时实测 < 1e-9 m）。
    """
    d = station_m - element["start_station_m"]
    if d <= 0:
        return element["start_x"], element["start_y"]
    n = max(2, steps + (steps % 2))            # Simpson 要求偶数段
    h = d / n
    th0 = math.radians(_need_start_azimuth(element))
    sx = math.cos(th0) + math.cos(math.radians(azimuth_at(element, station_m)))
    sy = math.sin(th0) + math.sin(math.radians(azimuth_at(element, station_m)))
    for i in range(1, n):
        s = element["start_station_m"] + i * h
        th = math.radians(azimuth_at(element, s))
        w = 4.0 if i % 2 else 2.0
        sx += w * math.cos(th)
        sy += w * math.sin(th)
    return (element["start_x"] + h / 3.0 * sx,
            element["start_y"] + h / 3.0 * sy)


def locate(elements: list[dict[str, Any]], station_m: float) -> dict[str, Any] | None:
    """桩号落在哪个线元里；落在全部线元之外 → None（**不外推**）。"""
    for e in elements:
        if e["start_station_m"] <= station_m <= e["end_station_m"]:
            return e
    return None


__all__ = ["derive_control_points", "curve_groups",
           "curvature_at", "azimuth_at", "point_at", "locate"]
