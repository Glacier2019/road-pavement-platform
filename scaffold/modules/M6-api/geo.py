"""M6 区域帧 —— 桩号 ↔ 经纬度。

本模块做三件事，别的都不做：

    1. **适配层**：把落库形状的 `alignment_element` 转成 `geom` 要的形状。
       这一步不是改名，是三个会静默出错的坑（见下面「三条纪律」）。
    2. **投影**：CGCS2000 高斯-克吕格正算与反算。手写，不引 pyproj。
    3. **业务**：单个桩号 / 整条路线的换算，带包围盒。

==================== 这一层为什么值得单独写 ====================
`geom.point_at` 能把桩号算成平面坐标，**但它算出来的不是经纬度**。
平面坐标是设计坐标系，与大地基准无关；把它放到地球上需要投影带号。
带号错了位置差几百公里，而在任何一个局部视图里都看不出来 —— 它只在
把模型放到地球上那一刻暴露。所以这一步被单独拎出来，并有独立判据。

★ 本模块**不含任何线形数学**。桩号落在哪个线元、方位角、坐标，全部委托
  `m2geom`（M2 的 `adapters.geom` 的逐字节副本）。复算一遍必然漂移。

==================== 三条纪律（违反会静默出错）====================
1. `turn_flag` **不是落库列**。`geom.curvature_at` 靠它决定曲率正负；
   丢了它，右转的曲线会朝反方向弯。`geom` 的原话是
   「**实测终点差 98 m，而且不报错**」。本模块从**方位角变化方向**还原它
   （`:func:`turn_sign`），与 `geometry_solver` 同一规则。

2. `radius_start_m = None` 表示**无穷大半径**（κ=0），**不是缺值**。
   缓和曲线起点正是这样。不要"帮忙"把它当 0。

3. `x` 是**北坐标**、`y` 是**东坐标**（库约定）。读反了会把路放到
   太平洋上（实测 4.54°N / 137.16°E）。所以本模块的投影函数参数叫
   `north` / `east`，**不叫 x / y** —— 让读反这件事在调用点就写不出来。

==================== 为什么手写投影而不引 pyproj ====================
三条独立证据（见 specs/004-region-frame/research.md 的 R1）：
往返闭合 **0.457 mm**；东偏推算 **+34.55 km** 对上 evidence 里的 +34.6 km；
落点包围盒 4.552 × 3.106 km 与 5.805 km 的路线长度相称。
再加一条对照：把中央子午线改成 114°E，落点检查**必须**失败（SC-008）。
"""
from __future__ import annotations

import math
import os
import pathlib
from typing import Any, Mapping, Sequence

from m2geom import geom

__all__ = [
    "CrsError", "StationOutsideAlignment",
    "IMPLEMENTED_FAMILIES", "require_family",
    "load_crs", "crs_path",
    "turn_sign", "as_adapter_element", "as_adapter_elements",
    "inverse", "forward",
    "point_north_east", "station_to_lonlat", "route_lonlat", "reverse_lonlat",
    "section_positions", "classify_install_mode",
]


# ============================================================ 异常

class CrsError(RuntimeError):
    """坐标基准配置不可用。**显式失败**，不猜默认值。

    ★ 为什么强调这一点：一个「找不到配置就用 117°E」的兜底会让 99% 的
      调用方正常工作，只在换基准时静默算错。错带号的特征是纬度不变、
      经度整体平移一个带宽 —— 谁也不会觉得奇怪。
    """


#: 本模块的手写换算**实现了哪些投影族**（005 / T043）。
#:
#: ★★★ 这个集合是**自述**，不是能力声明 —— 它必须与文件里真实存在的那套公式一致。
#:     现在它是 `{"tmerc"}`，因为下面那四十行就是 3 度带高斯-克吕格。
#:     **加一个族，就要同时加一份对拍**（见 `tests/contract/test_region_frame.py` §19）
#:     —— 否则新族就是一份**没有独立核对**的实现，而那正是 §19 存在的理由。
#:
#: ★★ 为什么需要这个集合：数据模型按宪法 §VI **接受任意 SRID**，
#:    而 `crs_epsg` 一旦被填成 `2154`（Lambert，`+proj=lcc`），
#:    旧代码会把它喂进下面的高斯-克吕格公式，**算出一个看着正常的错值**。
#:    「看着正常的错值比缺失更危险」—— 这正是 004 立下的那条教训。
#:
#: ★ 为什么不在这里写一张「支持的 SRID」白名单：
#:    · 白名单要手维护，加一个带号就得改代码；族是 PROJ 说的客观事实，不用我们记
#:    · 白名单会漏。漏掉的 SRID **不报错**，它只是不做检查 ——
#:      和「不得连库」那份文件名单是**同一种失效形状**（见 §4b 的教训）
#:    · `4548` 与 `4524` 是同一个族，列两遍就是两份会漂的真相
#:    ⇒ **只声明族，不声明 SRID。**
IMPLEMENTED_FAMILIES = frozenset({"tmerc"})


def require_family(gf: Mapping[str, Any], *, origin: str) -> str:
    """核对 `gf["projection_family"]` 是否在 :data:`IMPLEMENTED_FAMILIES` 里。

    返回该族；算不了就抛 :class:`CrsError`，**消息里必须说出是哪个族**。

    ★★ 为什么「族缺失」也**报错**，而不是放行
    ----------------------------------------
    放行等于说「没告诉我族 = 我算得了」。这和下面两种情况是同一件事：
    一个没配基准的路段静默用 117°E、一个没进名单的文件静默不检查。
    **「不知道」和「支持」必须分开** —— 前者不是通过的证据。

    ★ 与宪法 §VI 第②条的关系（这条我专门对过）：
      那一条说的是「国际标准标识符（EPSG/SRID/ISO）为权威，**本地记法**可空；
      可空字段缺失时 MUST NOT 报错」。`projection_family` **不是本地记法** ——
      它是从 `spatial_ref_sys.proj4text` 里由 PROJ 自己维护的 `+proj=`
      取出来的，是那个国际标识符的**派生物**。所以它不适用「缺失不报错」。
      ★ 而且这里真正能缺的那个（EPSG 本身）在 `assumed: true` 时确实缺 ——
        那条路径由调用方显式**声明** `tmerc`（假定值只用带号+中央子午线表达，
        而那套表达就是高斯-克吕格），所以它也不是「不声明就放行」。
    """
    fam = gf.get("projection_family")
    if fam is None:
        raise CrsError(
            f"{origin} 没有声明 `projection_family`，"
            "所以无法判断本实现算不算得了它。"
            "**「不知道」不是「支持」** —— 不声明一律拒绝，不猜。"
        )
    fam = str(fam)
    if fam not in IMPLEMENTED_FAMILIES:
        raise CrsError(
            f"{origin} 的投影族是 `{fam}`，而本实现只实现了 "
            f"{sorted(IMPLEMENTED_FAMILIES)}。"
            "拒绝计算 —— 拿别的族的参数套高斯-克吕格公式，"
            "会算出一个量级正常、位置却错掉几百米的坐标，"
            "而它看起来完全正常。"
        )
    return fam


class StationOutsideAlignment(ValueError):
    """桩号落在线元链之外。

    `geom.locate` 对链外桩号返回 `None`，**不外推**。本模块遇到 `None`
    一律抛这个异常并**指名桩号** —— 悄悄外推出一段路上没有的坐标，
    比报错危险得多。
    """


# ============================================================ 椭球与投影常数

# CGCS2000 椭球。与 crs.yaml 的 `ellipsoid: CGCS2000` 对应。
_A = 6378137.0                      # 长半轴 m
_INV_F = 298.257222101              # 扁率倒数
_F = 1.0 / _INV_F
_E2 = _F * (2.0 - _F)               # 第一偏心率平方
_EP2 = _E2 / (1.0 - _E2)            # 第二偏心率平方
_FALSE_EASTING = 500000.0           # 假东偏（Y 无带号前缀 ⇒ 500000，不是 39500000）
_K0 = 1.0                           # 尺度因子。3 度带不带尺度改正。


def _meridian_arc(phi: float) -> float:
    """子午线弧长 M(φ)。"""
    e2 = _E2
    return _A * (
        (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
        - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * phi)
        + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * phi)
        - (35 * e2 ** 3 / 3072) * math.sin(6 * phi)
    )


def inverse(north: float, east: float, central_meridian_deg: float) -> tuple[float, float]:
    """高斯-克吕格反算：平面 (北, 东) → (纬度, 经度)，单位度。

    ★ 参数名是 `north` / `east`，不是 x / y。库把北坐标存在 `x` 列、
      东坐标存在 `y` 列 —— 这是设计坐标系的历史约定，与测量学的
      (x=东, y=北) **正好相反**。用 x/y 命名会让"读反"在调用点看不出来。

    实测往返闭合 ≤ 0.457 mm。
    """
    lam0 = math.radians(central_meridian_deg)
    e2, ep2, a = _E2, _EP2, _A

    # 1) 底点纬度：由子午线弧长反解
    mu = north / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu
            + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu)
            + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))

    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    c1 = ep2 * cos1 ** 2
    t1 = tan1 ** 2
    n1 = a / math.sqrt(1 - e2 * sin1 ** 2)
    r1 = a * (1 - e2) / (1 - e2 * sin1 ** 2) ** 1.5
    d = (east - _FALSE_EASTING) / (n1 * _K0)

    phi = phi1 - (n1 * tan1 / r1) * (
        d ** 2 / 2
        - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720
    )
    lam = lam0 + (
        d
        - (1 + 2 * t1 + c1) * d ** 3 / 6
        + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120
    ) / cos1
    return math.degrees(phi), math.degrees(lam)


def forward(lat_deg: float, lon_deg: float,
            central_meridian_deg: float) -> tuple[float, float]:
    """高斯-克吕格正算：(纬度, 经度) → 平面 (北, 东)，单位 m。

    与 :func:`inverse` 共用同一套椭球常数与假东偏。
    """
    phi = math.radians(lat_deg)
    dlam = math.radians(lon_deg - central_meridian_deg)
    e2, ep2, a = _E2, _EP2, _A

    sin_p, cos_p, tan_p = math.sin(phi), math.cos(phi), math.tan(phi)
    n = a / math.sqrt(1 - e2 * sin_p ** 2)
    t = tan_p ** 2
    c = ep2 * cos_p ** 2
    aa = dlam * cos_p

    m = _meridian_arc(phi)
    north = m + _K0 * n * tan_p * (
        aa ** 2 / 2
        + (5 - t + 9 * c + 4 * c ** 2) * aa ** 4 / 24
        + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * aa ** 6 / 720
    )
    east = _FALSE_EASTING + _K0 * n * (
        aa
        + (1 - t + c) * aa ** 3 / 6
        + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * aa ** 5 / 120
    )
    return north, east


# ============================================================ 适配层

def _f(v: Any) -> float | None:
    """`Decimal`/`int`/`None` → `float`/`None`。

    psycopg 对 `numeric` 列返回 `Decimal`，对 `double precision` 返回 `float`。
    本模块的输入同时来自这两种列，而投影是纯 float 运算 ——
    `Decimal + float` 直接抛 `TypeError`。在这里一次性收口，
    而不是在每个用点各转一次。
    """
    return None if v is None else float(v)


def _pick(row: Mapping[str, Any], *names: str) -> Any:
    """按顺序取第一个存在的键。

    ★ **为什么需要这个**：同一个字段在两条取数路径上**叫不同的名字**。

      · 原始表 `alignment_element` 的列名是 `azimuth_deg`；
      · 而 M3 的 `ELEMENTS_SQL` 把它 `AS start_azimuth_deg` 了。

    M2 的 `geometry_solver` 吃的是前者，M6 的 `geo` 吃的是后者 ——
    结果就是：**契约测试全绿，容器里 500**。因为夹具记的是原始表行，
    而真实 DAO 返回的是改过名的形状。

    ⇒ 本函数两条路都认。只认一个名字，就等于把「从哪张表来的」这个
      实现细节变成了隐式契约，而那正是这次出错的形状。
    """
    for n in names:
        if n in row:
            return row[n]
    return None


def turn_sign(row: Mapping[str, Any]) -> int | None:
    """还原**转向符号**（+1 左转 / −1 右转）。直线返回 `None`。

    ★ 为什么必须还原：`turn_flag` **不是落库列**（`.PM` 的转符号没进 DDL），
      而 `geom.curvature_at` 靠它决定 κ 的正负。丢了它，右转的曲线会朝
      反方向弯 —— `geom` 的原话是「**实测终点差 98 m，而且不报错**」。

    还原依据：**方位角的变化方向**。Δaz = 出方位角 − 入方位角，归一化到
    (−180, 180] 后，正即左转、负即右转。实测本工程 24 个曲线单元 24/24 正确。

    ★ 为什么用方位角而不用圆心叉积：24 个曲线单元里只有 16 个有圆心
      （`center_x/center_y` 允许为 NULL）。方位角两列则总是有值。

    ★ 本函数与 `M2-ingest/geometry_solver.py::_turn_sign` 是同一规则。
      `tests/contract/test_region_frame.py` 用**真数据输出相等**守着这一对。

    ★ 入方位角两个名字都认，理由见 `_pick`。
    """
    az0 = _pick(row, "azimuth_deg", "start_azimuth_deg")
    az1 = _pick(row, "end_azimuth_deg")
    if az0 is None or az1 is None:
        return row.get("turn_flag")
    if row.get("element_type") == "line" or row.get("type") == "line":
        return None
    delta = (float(az1) - float(az0) + 180.0) % 360.0 - 180.0
    return 1 if delta > 0 else -1


def as_adapter_element(row: Mapping[str, Any]) -> dict[str, Any]:
    """落库形状的 `alignment_element` 行 → `geom` 要的形状。

    ★ `length_m` 用**桩号差重算**，不读列：该列是导出产物，而 `geom` 的
      曲率线性插值要用它做分母。两者若不一致（比如库里的列被改过），
      读列会得到一条与桩号链对不上的曲率。

    ★ 单位：落库是**千米**，适配器是**米**。混单位会得到一个数值上像模像样
      但完全错误的结果 —— 这一点在 `geometry_solver` 里已经踩过
      （`np.interp` 拿公里当米插值，结果是一串常数）。

    ★ 本函数与 `geometry_solver.py::_as_adapter_element` 是同一规则，
      由契约测试用真数据输出相等守着。

    ★ 入方位角两个名字都认（`azimuth_deg` / `start_azimuth_deg`），
      理由见 `_pick`。**这一条不是防御性编程**：M3 的 `ELEMENTS_SQL`
      确实把它改名了，只认一个名字的版本在容器里直接 500。
    """
    if "start_station_m" in row:
        return dict(row)                      # 已经是适配器形状
    start = float(row["start_station_km"]) * 1000.0
    end = float(row["end_station_km"]) * 1000.0
    return {
        "seq": row.get("element_seq"),
        "type": row.get("element_type"),
        "start_station_m": start,
        "end_station_m": end,
        "length_m": end - start,
        "start_x": _f(row.get("start_x")), "start_y": _f(row.get("start_y")),
        "end_x": _f(row.get("end_x")), "end_y": _f(row.get("end_y")),
        "azimuth_deg": _f(_pick(row, "azimuth_deg", "start_azimuth_deg")),
        "end_azimuth_deg": _f(row.get("end_azimuth_deg")),
        "radius_start_m": _f(row.get("radius_start_m")),
        "radius_end_m": _f(row.get("radius_end_m")),
        "turn_flag": turn_sign(row),
    }


def as_adapter_elements(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """整链转换，并按 `element_seq` **排序**。

    ★ 排序不是修饰：`geom.locate` 按顺序找第一个覆盖桩号的线元，
      顺序错了会命中错误的那一段，而且**不报错**。
    """
    ordered = sorted(rows, key=lambda r: (r.get("element_seq") is None,
                                          r.get("element_seq")))
    return [as_adapter_element(r) for r in ordered]


# ============================================================ 基准配置

def crs_path() -> pathlib.Path:
    """`crs.yaml`（**派生产物**）的位置。

    ★★ 2026-10-12（005 / Q3 裁决）：库成为坐标基准的权威，本文件降为
      **派生产物**。因此 ——

        **M6 自己不再读这个文件。** `load_crs()` 已改为收一个解析好的 dict。
        这个函数留着，是因为"派生产物放在哪"仍然是一个必须能回答的问题：
        其他四个消费者读它，漂移守卫（T027/T034）要拿它和库比对。

    ⚠ 留着的代价要说清楚：`M6-api` 镜像里那个 `COPY … /app/crs.yaml`
      （Dockerfile L46）现在是**给别的用途留的，不是给运行时用的**。
      默认相对路径 `crs.yaml` 也因此只对 `WORKDIR /app` 成立。
      可用环境变量 `RP_CRS_CONFIG` 覆盖。
    """
    return pathlib.Path(os.getenv("RP_CRS_CONFIG", "crs.yaml"))


def load_crs(doc: Mapping[str, Any], *,
             origin: str = "传入的基准文档") -> dict[str, Any]:
    """校验 `geographic_frame` 段并返回它。**不读文件。**

    ★★ 签名变更（005 / Q3 裁决）：由「收一个路径、自己去读文件」改为
      「**收一个已经解析好的 dict**」。
      为什么：库是权威，文件是派生产物。一个既读文件又读库的模块，
      在两者不一致时**不知道该信谁** —— 而这种不确定不会报错，
      只会让某一部分请求静默用错带号。宁可让它**读不到文件**。

    ★ 校验逻辑**逐字保留 004 立下的那两条**（由本文件 §8 守着）：
      · `assumed: false` ⇒ `epsg_after_confirm` 必须非空
      · `assumed: true`  ⇒ `epsg_after_confirm` 必须为空

    ★ 为什么要校验：`assumed` 与 `epsg_after_confirm` 是两个字段，
      分开写就会分开漂。一个"猜的值配上已确认的 EPSG"是最坏的状态 ——
      它看起来是确认过的。

    文档结构不对或缺必填键，一律抛 :class:`CrsError`，**不返回默认值**。

    参数
    ----
    doc     已经解析好的文档（含 `geographic_frame` 段）。
    origin  出错时用在消息里的**来源说明**（例如「路段 6 的生效基准」）。
            ★ 它取代了旧签名里的那个路径 —— 不读文件了，就没有路径可印，
              但"这个值从哪来"仍然必须说清楚。少了这一句，
              报错会变成"某个地方的基准有问题"，而调用方无从下手。
    """
    gf = doc.get("geographic_frame") if isinstance(doc, Mapping) else None
    if not isinstance(gf, dict):
        raise CrsError(f"{origin} 里没有 geographic_frame 段。")

    missing = [k for k in ("zone_number", "central_meridian_deg", "assumed")
               if k not in gf]
    if missing:
        raise CrsError(f"{origin} 的 geographic_frame 缺少字段：{missing}")

    assumed = bool(gf["assumed"])
    epsg = gf.get("epsg_after_confirm")
    if assumed and epsg is not None:
        raise CrsError(
            f"{origin}：assumed 为 true（值是猜的）却填了 epsg_after_confirm={epsg}。"
            "猜的时候不许填 EPSG —— 那会让一个未确认的值看起来已经确认。"
        )
    if not assumed and not epsg:
        raise CrsError(
            f"{origin}：assumed 为 false（值已确认）却没填 epsg_after_confirm。"
            "已确认就该能说出是哪个 EPSG 编码。"
        )

    # ★★ 005 / T043：最后一关 —— **这个基准算不算得了**。
    #    前面两条查的是「配置自不自洽」，这条查的是「本实现能不能算」。
    #    ★ 顺序有意放在最后：一个自相矛盾的配置，比一个算不了的配置更该先报，
    #      因为前者说明**数据录入就错了**，后者可能只是平台还没实现那个族。
    #    ★★ 它是 `load_crs` 的一部分，不是调用方的事 —— 放在调用方，
    #      就会有第二个调用方忘记加（而忘记加的后果是静默算错）。
    require_family(gf, origin=origin)
    return gf


# ============================================================ 业务

def point_north_east(elements: Sequence[Mapping[str, Any]],
                     station_m: float) -> tuple[float, float]:
    """桩号 → 平面 **(北坐标, 东坐标)**，单位 m。

    ★ 两件事必须一起做，少一件就错：
      ① `geom.locate` 先定位线元。`geom.point_at` **只认单个线元**，
         直接把它喂给整条链会 `KeyError`；
      ② `locate` 返回 `None`（桩号在链外）时**抛异常，不外推**。
         `point_at` 对 `d <= 0` 的桩号直接返回该线元的起点坐标 ——
         那不是"这段路上有这个点"，那是"这个点在这段路的起点旁边"。
         两者在链内桩号上数值相同，在链外桩号上差一整段路。
    """
    e = geom.locate(list(elements), station_m)
    if e is None:
        lo = min(x["start_station_m"] for x in elements)
        hi = max(x["end_station_m"] for x in elements)
        raise StationOutsideAlignment(
            f"桩号 {station_m:.3f} m 落在线元链之外"
            f"（链覆盖 {lo:.3f} .. {hi:.3f} m）。不外推。"
        )
    north, east = geom.point_at(e, station_m)
    return north, east


def station_to_lonlat(elements: Sequence[Mapping[str, Any]],
                      station_m: float,
                      gf: Mapping[str, Any]) -> dict[str, Any]:
    """桩号 → 经纬度，含来源标记与所用基准。

    ★ 输出**必须回带 `epsg`**：39 带有两个 EPSG 编码（4548 无带号前缀 /
      4527 有），取错东坐标差 39,000,000 m。只回经纬度的接口让调用方
      无法判断自己拿到的东坐标是哪一种，也就无法发现错误。

    ★ `source` 固定为 `"computed_from_station"`。这是**刻意的措辞**：
      本平台把桩号换算成经纬度，**不声称**这条路真的在这里。
      理由见 specs/004-region-frame/research.md 的 R7 —— 实测五带扫描里
      没有一个带的落点与设计方地面线形状相关（r 全部接近 0），
      坐标是学生自定的局部坐标、还是地形被学生生成过，两种读法都还开着。
      所以这里只用「换算」这个词，不用「定位」。
    """
    north, east = point_north_east(elements, station_m)
    cm = float(gf["central_meridian_deg"])
    lat, lon = inverse(north, east, cm)
    return {
        "station_m": station_m,
        "north_m": north,
        "east_m": east,
        "lat_deg": lat,
        "lon_deg": lon,
        "epsg": gf.get("epsg_after_confirm"),
        "central_meridian_deg": cm,
        "zone_number": gf.get("zone_number"),
        "source": "computed_from_station",
    }


def route_lonlat(elements: Sequence[Mapping[str, Any]],
                 gf: Mapping[str, Any],
                 step_m: float = 20.0) -> dict[str, Any]:
    """整条路线 → 经纬度折线 + 包围盒。

    ★ 采样点取自**每个线元的端点加等分内点**，不是"从 0 开始每 step_m 一个"。
      后者在缓和曲线里会让样点密度与曲率无关，而本 feature 的判据之一
      （SC-003「曲线是弯的」）正是要看曲率大处的折线是否够密。

    包围盒是 SC-002 的落点检查所用的量。
    """
    if step_m <= 0:
        raise ValueError(f"step_m 必须为正，收到 {step_m}")
    cm = float(gf["central_meridian_deg"])
    epsg = gf.get("epsg_after_confirm")

    per_element: list[dict[str, Any]] = []
    all_pts: list[tuple[float, float, float]] = []      # (station, lat, lon)

    for e in elements:
        s0 = float(e["start_station_m"])
        s1 = float(e["end_station_m"])
        span = s1 - s0
        n = max(2, int(math.ceil(span / step_m)) + 1)
        pts = []
        for i in range(n):
            s = s0 + span * i / (n - 1)
            north, east = point_north_east(elements, s)
            lat, lon = inverse(north, east, cm)
            pts.append({"station_m": s, "lat_deg": lat, "lon_deg": lon,
                        "north_m": north, "east_m": east})
            all_pts.append((s, lat, lon))
        per_element.append({
            "element_seq": e.get("seq"),
            "element_type": e.get("type"),
            "start_station_m": s0,
            "end_station_m": s1,
            "turn_flag": e.get("turn_flag"),
            "points": pts,
        })

    lats = [p[1] for p in all_pts]
    lons = [p[2] for p in all_pts]
    return {
        "epsg": epsg,
        "central_meridian_deg": cm,
        "zone_number": gf.get("zone_number"),
        "source": "computed_from_station",
        "step_m": step_m,
        "point_count": len(all_pts),
        "bbox": {"lat_min": min(lats), "lat_max": max(lats),
                 "lon_min": min(lons), "lon_max": max(lons)},
        "elements": per_element,
    }


# ================================================================ 残差分带（004 收尾）
#
# ★★★ 这一节补的是一道**被设计过、写进 docstring、却从来没实现**的闸。
#
# 起因：quickstart 第 3 步的验收动作声称「经纬度落在带外 → 报错」，
# 实测**任何经度都返回 200**（一路扫到 130°E，离中央子午线 13°）。
# 于是 `spec.md` 的 Edge Cases、`data-model.md` §5、`reverse_lonlat` 的
# docstring 三处写着同一句话，代码里一个字都没有。
#
# ★★ 为什么最终**不按"带外就拒"**来做（平台方 2026-10-11 裁决，读法 B）：
#
#   实测 `lon=117.36°E`（**完全在 39 带内**，离中央子午线只有 0.36°）
#   得到的 `offset_m = 371.7 m` —— 这个点**根本不在路上**，
#   但带号检查会**放过它**。
#
#   ⇒ 「带外」只是「这个点不在路上」的一种成因。用带号当闸，
#     管住的是一种成因，放过的是同一类错误的其余全部。
#     而 `offset_m` 直接量「离路线多远」—— 它对成因不敏感，
#     所以它才是那道闸。
#
# ★ 阈值不是拍出来的，是**数据自己给的**（2026-10-11 实测）：
#
#   · 设计路基半宽 = 土路肩 0.750 + 硬路肩 0.750 + 分隔带 0.000 + 车道 3.500
#                   = **5.000 m**（`roadbed_design_point`，section_id=6；全路幅 10.000 m）
#   · 实测地面点横向覆盖 = 0.019 .. **50.000 m**（`cross_section_ground_point` 2215 点）
#                   ⇒ **勘察走廊就是 ±50 m**，整数不是巧合
#
#   ⇒ 三带，边界都来自上表，没有一个是估计的。
#
_ON_ROADBED_MAX_M = 5.0      # 设计路基半宽
_IN_CORRIDOR_MAX_M = 50.0    # 实测勘察走廊半宽

BAND_ON_ROADBED = "on_roadbed"    # 在路基范围内 —— 桩号就是这个点的桩号
BAND_IN_CORRIDOR = "in_corridor"  # 在勘察走廊内 —— 附近，但不是路上
BAND_OFF_CORRIDOR = "off_corridor"  # 走廊之外 —— 残差就是全部答案


def offset_band(offset_m: float) -> str:
    """把「离路线多远」分到三带之一。**三值且穷尽。**

    ★ 为什么分带而不是一个布尔「在不在路上」：
      布尔会把 5.1 m 和 264 857 m 归成一类，而这两个答案
      在工程上的含义差着四个数量级（一个在路肩外，一个在另一个地级市）。
      调用方需要知道的是**差多远**，不是一个"否"。

    ★ 边界用 `<=`：边界值归**更靠近路线**的那一带。
      理由与 `geom.locate` 的「共享边界归前一个元素」无关 ——
      那一条是**位置歧义**（同一个点属于哪个线元），
      这一条是**分类保守性**（差 5.000 m 整，算路上还是路肩外）。
      两者恰好都用了含左的写法，但原因不同，不要互相援引。
    """
    if offset_m <= _ON_ROADBED_MAX_M:
        return BAND_ON_ROADBED
    if offset_m <= _IN_CORRIDOR_MAX_M:
        return BAND_IN_CORRIDOR
    return BAND_OFF_CORRIDOR


def reverse_lonlat(elements: Sequence[Mapping[str, Any]],
                   lat_deg: float, lon_deg: float,
                   gf: Mapping[str, Any],
                   *, coarse_m: float = 10.0) -> dict[str, Any]:
    """经纬度 → 最近的桩号。**最近点，不是精确解。**

    ★ 为什么是"最近"而不是"解出来"：把平面坐标反投影成经纬度是闭式的，
      但从 (经, 纬) 回到**桩号**要在这条折线上找最近点 —— 那是一个搜索，
      没有闭式。本函数先粗扫（`coarse_m` 一档）再在邻域细化。

    ★★ `station_m` 是**最近点的桩号**，不是"这个点的桩号" ——
      当查询点不在路上时（`offset_band != "on_roadbed"`），
      它只是"离你最近的那个位置在哪"，**不能当成查询点的位置用**。
      `station_is_nearest` 把这个区别显式说出来。

    ★★★ 回答一句前任 docstring 留下的问题（原文：「超过 `max_offset_m`
      一律抛错，不给一个远在天边的答案。」）——
      **`max_offset_m` 这个参数从来没有存在过，代码也没抛过错。**

      平台方 2026-10-11 的裁决是**不抛错**：把残差如实分带报出来。
      理由是同一道闸要能挡住"带号错"以外的成因 ——
      实测 `lon=117.36°E`（带内）残差已达 371.7 m，带号检查会放过它。
      详见本文件上方「残差分带」一节的实测数据。

      ⇒ 现在 docstring 与代码一致：**不拒绝，但绝不隐藏。**
    """
    if coarse_m <= 0:
        raise ValueError(f"coarse_m 必须为正，收到 {coarse_m}")
    cm = float(gf["central_meridian_deg"])
    north_q, east_q = forward(lat_deg, lon_deg, cm)

    lo = min(e["start_station_m"] for e in elements)
    hi = max(e["end_station_m"] for e in elements)

    def dist(s: float) -> float:
        n, e = point_north_east(elements, s)
        return math.hypot(n - north_q, e - east_q)

    best_s, best_d = lo, dist(lo)
    s = lo
    while s <= hi:
        d = dist(s)
        if d < best_d:
            best_s, best_d = s, d
        s += coarse_m
    if dist(hi) < best_d:
        best_s, best_d = hi, dist(hi)

    # 细化：在 best_s ± coarse_m 内二分到 1 mm
    a, b = max(lo, best_s - coarse_m), min(hi, best_s + coarse_m)
    for _ in range(40):
        m1 = a + (b - a) / 3.0
        m2 = b - (b - a) / 3.0
        if dist(m1) <= dist(m2):
            b = m2
        else:
            a = m1
    best_s = (a + b) / 2.0
    best_d = dist(best_s)

    north, east = point_north_east(elements, best_s)
    band = offset_band(best_d)
    return {
        "lat_deg": lat_deg,
        "lon_deg": lon_deg,
        "north_m": north,
        "east_m": east,
        "station_m": best_s,
        # ★★ 说清 `station_m` 是什么：它是**最近点**的桩号。
        #    只有在 on_roadbed 时，它才同时也是"这个点的桩号"。
        "station_is_nearest": band != BAND_ON_ROADBED,
        "offset_m": best_d,
        "offset_band": band,
        "offset_bands_m": {"on_roadbed": _ON_ROADBED_MAX_M,
                           "in_corridor": _IN_CORRIDOR_MAX_M},
        "epsg": gf.get("epsg_after_confirm"),
        "central_meridian_deg": cm,
        "zone_number": gf.get("zone_number"),
        # ★★★ 原先这里写的是 "computed_from_station" —— **方向说反了**。
        #     本函数的输入是经纬度，桩号才是推出来的。
        #     "computed_from_station" 是 station_to_lonlat 的措辞
        #     （那里桩号是输入）。两个方向共用一个字符串，
        #     等于一个溯源自称了一件与事实相反的事。
        "source": "computed_from_lonlat",
    }


# ================================================================ US3 沿线设施
#
# ★★★ 这一节解决的是一件事：**一个断面的位置是怎么来的，必须说得出来。**
#
# 三种来源，穷尽：
#   · "stored"                —— 库里有经纬度（实测，比如 GNSS 测量），直接用
#   · "computed_from_station" —— 库里没有，**由桩号换算**
#   · "unavailable"           —— 换算不出来，**给出理由**
#
# ★ 为什么三种都要有，而不是"能算就算、算不出就跳过"：
#   跳过 = 缺失变成静默。FR-010 要挡的正是这个形状 ——
#   NULL 被读成 0 时，断面会一起堆到几内亚湾外的 (0°,0°)，**而不报错**。
#   「跳过」和「读成 0」是同一个错误的两种表现：都把"没有位置"当成了一个位置。
#
# ★ 商用平台上这三种是常态：老路段有实测坐标、新路段只有桩号、
#   还有的路段连数字化线形都没有。接口能把这三种说清楚，才谈得上通用。

# `monitor_cross_section.station_km` 的单位是**公里**（K2+900 → 2.900000）。
# ★ 与 `alignment_element.start_station_km` 同病：列名带 km，用的人当米。
_STATION_KM_TO_M = 1000.0

# ★ 安装方式的分类用**白名单**，不用黑名单。
#   仿真：本部署实测出现过。
#   实体：本部署实测出现过（sensor 1 = 埋入式，sensor 2 = 表面式）。
#   其余一律 `None` = **未分类**，在界面上显式标出来。
#
# ★★ 为什么不写成"不是仿真就是实体"：那样新出现一种安装方式（比如
#    "半实物仿真"）会被**静默当成实体资产** —— 正是 `SIMWIM01` 那条
#    溯源约定要防的事。白名单的代价是多一行"安装方式未分类"，
#    黑名单的代价是把仿真数据冒充成实体资产。
_SIMULATED_INSTALL_MODES = frozenset({"仿真"})
_PHYSICAL_INSTALL_MODES = frozenset({"埋入式", "表面式"})


def classify_install_mode(mode: Any) -> bool | None:
    """安装方式 → 是不是仿真资产。

    `True` 仿真 / `False` 实体 / `None` **未分类（不猜，界面上标出来）**。

    ★ `test_region_frame.py` 有一条守卫：**夹具里出现过的每一种
      `install_mode` 都必须被分类**（不得为 `None`）。将来数据里多出一种
      新的安装方式，那条会红 —— 逼人来把分类写明确。
    """
    if mode is None:
        return None
    s = str(mode).strip()
    if not s:
        return None
    if s in _SIMULATED_INSTALL_MODES:
        return True
    if s in _PHYSICAL_INSTALL_MODES:
        return False
    return None


def _device_view(row: Mapping[str, Any]) -> dict[str, Any]:
    """一台设备 → 带**溯源**的视图。

    ★ `simulated` 与 `install_mode` **两个都给**：前者是判断，后者是依据。
      只给判断，读的人没法复核；只给依据，每个读的人都要自己判断一遍。
    """
    mode = row.get("install_mode")
    return {
        "id": row.get("id"),
        "serial_no": row.get("serial_no"),
        "sensor_type_code": row.get("sensor_type_code"),
        "sensor_model": row.get("sensor_model"),
        "manufacturer": row.get("manufacturer"),
        "install_mode": mode,
        "simulated": classify_install_mode(mode),
        "simulated_basis": "sensor_install.install_mode",
    }


def section_positions(elements: list[dict[str, Any]],
                      cross_sections: list[dict[str, Any]],
                      sensors: list[dict[str, Any]],
                      gf: Mapping[str, Any]) -> dict[str, Any]:
    """每个监测断面的位置，**连同它的来源**。

    ★★★ FR-010「不得把 `NULL` 当成 `(0, 0)`」在这里是**结构性**保证的：
      `lat_deg` / `lon_deg` 两个键**只在真有位置时才出现**。
      无条件取用的人拿到的是 `KeyError`（Python）/ `undefined`（JS），
      **而不是 `(0, 0)`** —— 也就是说，这个错误**无法静默发生**。
      这比"约定大家要判空"强：约定会被绕过，缺键不会。

    ★ 返回顶层还有一个 `counts_by_source`。它让"有几个断面没算出来"成为一个
      **一眼可见的数**，而不是要靠人数一遍列表才知道。
    """
    by_xsec: dict[Any, list[dict[str, Any]]] = {}
    for s in sensors:
        by_xsec.setdefault(s.get("cross_section_id"), []).append(s)

    out: list[dict[str, Any]] = []
    counts = {"stored": 0, "computed_from_station": 0, "unavailable": 0}

    for xs in cross_sections:
        lat_stored, lon_stored = xs.get("lat"), xs.get("lon")
        row: dict[str, Any] = {
            "id": xs.get("id"),
            "station_text": xs.get("station_text"),
            "station_m": (None if xs.get("station_km") is None
                          else float(xs["station_km"]) * _STATION_KM_TO_M),
            "lane_no": xs.get("lane_no"),
            "purpose": xs.get("purpose"),
            "status": xs.get("status"),
            # ★ 原样回报两列。NULL 就是 None，绝不写成 0.0。
            "stored_lat": lat_stored,
            "stored_lon": lon_stored,
            "devices": [_device_view(d) for d in by_xsec.get(xs.get("id"), [])],
        }

        # ★ 下面四个分支是**穷尽**的，且各自把 position_source 写死成字面量。
        #   计数在分支之后统一累加（见 `counts[row["position_source"]] += 1`）——
        #   散在各分支里写 += 1，迟早会和分支对不上。
        #
        # ---- ① 库里有：用它
        if lat_stored is not None and lon_stored is not None:
            row["position_source"] = "stored"
            row["lat_deg"] = float(lat_stored)
            row["lon_deg"] = float(lon_stored)
            row["north_m"] = row["east_m"] = None      # 实测坐标没有平面帧分量
        # ---- ② 只填了一半：NULL 的一种，必须说清楚，不能补 0
        elif lat_stored is not None or lon_stored is not None:
            row["position_source"] = "unavailable"
            row["unavailable_reason"] = (
                "经纬度只填了一半：经度和纬度必须同时有值才能当位置用")
        # ---- ③ 全空、且该路段没有线形：位置**不存在**，不是"算出来是空"
        elif not elements:
            row["position_source"] = "unavailable"
            row["unavailable_reason"] = (
                "该路段没有线形单元（alignment_element 无行），桩号无从换算 —— "
                "线形是地理坐标的唯一来源")
        # ---- ④ 全空、有线形：由桩号换算
        elif row["station_m"] is None:
            row["position_source"] = "unavailable"
            row["unavailable_reason"] = "该断面没有桩号（station_km 为空），无从换算"
        else:
            try:
                p = station_to_lonlat(elements, row["station_m"], gf)
            except StationOutsideAlignment:
                row["position_source"] = "unavailable"
                row["unavailable_reason"] = (
                    f"桩号 {row['station_m']:.3f} m 超出该路段线形范围")
            else:
                row["position_source"] = "computed_from_station"
                row["lat_deg"] = p["lat_deg"]
                row["lon_deg"] = p["lon_deg"]
                row["north_m"] = p["north_m"]
                row["east_m"] = p["east_m"]

        counts[row["position_source"]] += 1
        out.append(row)

    return {
        "cross_sections": out,
        "counts_by_source": counts,
        "position_rule": "stored > computed_from_station > unavailable",
        "epsg": gf.get("epsg_after_confirm"),
        "central_meridian_deg": gf.get("central_meridian_deg"),
        "zone_number": gf.get("zone_number"),
    }
