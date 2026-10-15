"""三路到齐检查 —— .PM+.JD / .ZDM / .SUP 是否都落到了逐桩表上。

**为什么单独一个模块**

`geometry_point` 的表注释承诺它是 κ(s)/G(s)/E(s) 三路对齐的结果：

    逐桩号线形＝κ(s)/G(s)/E(s) 函数库；κ 来自 .PM+.JD，G 来自 .ZDM，
    E 来自 .SUP，三者对齐到 station_sequence 同一基准。

但"承诺"与"实际"之间没有检查。列可以是 NULL，而 NULL 在读的时候
和"没有这一路"长得一模一样 —— 少写一列不会报错，下游只会以为
那条桩号的这一路是"没有"，而不是"没算出来"。

本模块把三路拆开、逐列判，只产出三种判断：

    present              有值
    legitimately_absent  没有值，但**规则允许**，且规则的前提**当场核过**
    missing              没有值，规则不允许   ← 唯一的 finding

★ **为什么"规则允许"必须当场验前提**

只写"这一列可以为空"，就等于给这一列发了**永久豁免** ——
那种检查永远不会红，而"永远不红"与"根本没在看"在输出上完全一样。

所以每条豁免都带一个前提，检查时现算：

    h_radius_m  可以为空 —— **仅当** κ = 0（直线段或缓和曲线起点）
    v_radius_m  可以为空 —— **仅当** 该桩号不在任何竖曲线内
    横坡六列    可以为空 —— 源文件 9999 哨兵：该通道在此位置不参与约束

若前提不成立，豁免不生效，判 missing。

★ **路线级缺席与列级缺失分开**

六列横坡**单列**为空是合法哨兵，**六列同时为空**是整条超高路没到。
两种都要报，但不能混成一个 —— 前者不是缺陷，后者是。
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

#: 超高横坡六列 —— 教程 §13.5 对 .SUP 的原样命名，左右对称。
CROSSFALL_COLUMNS: tuple[str, ...] = (
    "earth_shoulder_left_pct",
    "hard_shoulder_left_pct",
    "lane_left_pct",
    "lane_right_pct",
    "hard_shoulder_right_pct",
    "earth_shoulder_right_pct",
)

#: 三路：路线名 → (源文件, 该路必须落到逐桩表的列)
ROUTES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("plane", ".PM+.JD",
     ("x_coord", "y_coord", "azimuth_deg", "curvature_1pm", "h_radius_m")),
    ("profile", ".ZDM",
     ("design_elev_m", "grade_pct", "v_radius_m")),
    ("superelev", ".SUP", CROSSFALL_COLUMNS),
)

#: 曲率小于此值即视作直线段（κ 为 1/m，直线段落库为精确 0）。
KAPPA_TOL = 1e-12


def vertical_curve_spans(design_points: Iterable[Mapping[str, Any]]) -> list[tuple[float, float]]:
    """由变坡点算出**竖曲线**覆盖的桩号区间（单位：米）。

    公式与 ``adapters/weidi/zdm.py::vertical_curve_of`` 同源：
    ``L = |i2-i1|/100 * R``，曲线以变坡点为中心，故半长 ``T = L/2``。

    首尾两个变坡点没有曲线（缺 grade_in 或 grade_out），返回时跳过。

    ★ 本函数与 zdm 的那一份是**同一公式的两处实现**。契约测试
    ``test_source_arrival.py`` 用元测试把两者对质，防止它们各走各的。
    """
    out: list[tuple[float, float]] = []
    for p in design_points:
        i1, i2 = p.get("grade_in_pct"), p.get("grade_out_pct")
        r = p.get("vertical_curve_radius_m")
        s = p.get("station_m")
        if i1 is None or i2 is None or r is None or s is None:
            continue
        r = float(r)
        if r == 0.0:
            continue
        half = abs(float(i2) - float(i1)) / 200.0 * r
        if half <= 0.0:
            continue
        out.append((float(s) - half, float(s) + half))
    return sorted(out)


def in_any_span(station_m: float, spans: Sequence[tuple[float, float]]) -> bool:
    """桩号是否落在任一竖曲线区间内（闭区间）。"""
    for lo, hi in spans:
        if lo <= station_m <= hi:
            return True
    return False


def _waiver(column: str, row: Mapping[str, Any], spans: Sequence[tuple[float, float]]) -> str | None:
    """这一列**此刻**可以合法为空吗？可以则返回理由，不可以则返回 ``None``。

    ★ 每条豁免都现算前提，不无条件放行。
    """
    if column == "h_radius_m":
        k = row.get("curvature_1pm")
        if k is not None and abs(float(k)) <= KAPPA_TOL:
            return "κ=0（无有限平曲线半径）"
        return None
    if column == "v_radius_m":
        s = row.get("station_m")
        if s is not None and not in_any_span(float(s), spans):
            return "该桩号不在任何竖曲线内"
        return None
    if column in CROSSFALL_COLUMNS:
        # 9999 哨兵：该通道在此位置不参与约束，过渡照常继续。
        # 单列为空合法；六列同时为空是**路线级**缺席，由 check_arrival 单独判。
        return "源文件 9999 哨兵（该通道此处不参与约束）"
    return None


def check_arrival(rows: Iterable[Mapping[str, Any]], *,
                  design_points: Iterable[Mapping[str, Any]] = (),
                  curve_spans: Sequence[tuple[float, float]] | None = None) -> dict[str, Any]:
    """逐桩逐列判三路是否到齐。

    ``rows`` 每行须含 ``station_m`` 及三路的列（缺键等同 NULL）。

    返回::

        {"findings": [...], "by_route": {...}, "by_column": {...},
         "stations": n, "curve_spans": [...]}

    ``findings`` 每项::

        {"station_m", "route", "route_source", "column", "kind", "detail"}

    ``kind``：``route_absent``（整路没到）或 ``column_missing``（该列该有值却空）。
    合法缺席**不进 findings**。
    """
    spans = list(curve_spans) if curve_spans is not None else vertical_curve_spans(design_points)
    findings: list[dict[str, Any]] = []
    by_route: dict[str, dict[str, int]] = {}
    by_column: dict[str, int] = {}
    n = 0

    for row in rows:
        n += 1
        s = row.get("station_m")
        for route, source, columns in ROUTES:
            present = [c for c in columns if row.get(c) is not None]
            cnt = by_route.setdefault(route, {"present": 0, "legit": 0, "missing": 0,
                                              "route_absent": 0})
            if not present:
                # 整路没到：一行都没有。此时不再逐列报，避免一条原因刷六行。
                cnt["route_absent"] += 1
                findings.append({"station_m": s, "route": route, "route_source": source,
                                 "column": None, "kind": "route_absent",
                                 "detail": f"{source} 这一路的 {len(columns)} 列全为空"})
                continue
            cnt["present"] += 1
            for c in columns:
                if row.get(c) is not None:
                    continue
                why = _waiver(c, row, spans)
                if why is None:
                    cnt["missing"] += 1
                    by_column[c] = by_column.get(c, 0) + 1
                    findings.append({"station_m": s, "route": route, "route_source": source,
                                     "column": c, "kind": "column_missing",
                                     "detail": "该列按规则必须有值，实际为空"})
                else:
                    cnt["legit"] += 1

    return {"findings": findings, "by_route": by_route, "by_column": by_column,
            "stations": n, "curve_spans": spans}


def summarize(report: Mapping[str, Any]) -> list[str]:
    """把报告压成人能读的几行。"""
    out: list[str] = []
    out.append(f"  逐桩行 {report['stations']}；竖曲线区间 {len(report['curve_spans'])} 段")
    for route, _src, _cols in ROUTES:
        c = report["by_route"].get(route, {})
        out.append(f"    {route:<10} 到齐 {c.get('present',0):>4}  "
                   f"合法缺席 {c.get('legit',0):>4}  缺列 {c.get('missing',0):>4}  "
                   f"整路缺席 {c.get('route_absent',0):>4}")
    if report["by_column"]:
        out.append("  按列：")
        for col, k in sorted(report["by_column"].items(), key=lambda kv: -kv[1]):
            out.append(f"    {col:<26} 缺 {k}")
    else:
        out.append("  按列：无缺列")
    return out


# ── 第二件事：值对不对 ─────────────────────────────────────────────────
# 上面管「有没有值」，下面管「值对不对」。两件事，两组判据。
#
# 被核的对象是 design_elev_m（纵断面设计高程）。判据只有一条：
#
#     高程沿桩号的变化率，必须等于设计纵坡。
#
# ★ 关键在于**两边独立**：
#     左边 = design_elev_m 的实测差（纯数值，不用任何公式）
#     右边 = 由 .ZDM 变坡点算出的解析积分（∫ g ds）
#   若拿同一个函数既算高程又算纵坡再互比，那是自证，什么都验不出来。
#
# ★ 容差不是拍出来的，是从**各列的声明精度**推出来的（见 PRECISION）。
#   「差多少算对」如果由实现反推，那这门永远绿。

#: 各列声明精度（`information_schema.columns` 实测的 numeric 标度）
PRECISION: dict[str, float] = {
    "design_elev_m": 1e-4,   # numeric(10,4)  → 0.1 mm
    "station_m": 1e-3,       # station_absolute_km numeric(12,6) → 1 mm
    "grade_pct": 1e-3,       # numeric(6,3)，**单位是百分数** → 0.001%
    "v_radius_m": 1e-4,      # vertical_curve_radius_m numeric(12,4)
}

#: 浮点噪声底
FLOAT_EPS = 1e-12


def breakpoints_of(design_points: Iterable[Mapping[str, Any]]) -> list[float]:
    """纵坡函数 g(s) 的**全部折点**：变坡点桩号 + 各竖曲线的 BVC/EVC。

    g(s) 是**连续**的分段线性函数（变坡点前一条的 grade_out 等于后一条的
    grade_in，实测 12 个变坡点全部相符）。所以只要积分区间里含全部折点，
    梯形法就是**精确**的，不引入任何离散误差。
    """
    br: set[float] = set()
    for p in design_points:
        s = p.get("station_m")
        if s is not None:
            br.add(float(s))
    for lo, hi in vertical_curve_spans(design_points):
        br.add(lo)
        br.add(hi)
    return sorted(br)


def integrate_grade(grade_fn, design_points, sa: float, sb: float,
                    breaks: Sequence[float]) -> float | None:
    """``∫ g ds / 100`` —— 由 sa 到 sb 的设计高差（米）。梯形法，折点处断开。

    ``grade_fn(design_points, s)`` 须返回该桩号的纵坡（%）；返回 ``None`` 视为不可求值。
    桩号会被**夹到路线范围内**：扰动分析会把端点推到范围外，夹回来对应
    「解析值不外推」这同一约定。
    """
    pts = sorted({sa, sb} | {x for x in breaks if sa < x < sb})
    lo = min(float(p["station_m"]) for p in design_points if p.get("station_m") is not None)
    hi = max(float(p["station_m"]) for p in design_points if p.get("station_m") is not None)
    tot = 0.0
    for a, b in zip(pts, pts[1:]):
        ga = grade_fn(design_points, min(max(a, lo), hi))
        gb = grade_fn(design_points, min(max(b, lo), hi))
        if ga is None or gb is None:
            return None
        tot += (ga + gb) / 2.0 * (b - a)
    return tot / 100.0


def tolerance_of(grade_fn, design_points, sa: float, sb: float,
                 breaks: Sequence[float], *,
                 precision: Mapping[str, float] = PRECISION) -> float:
    """这一个区间上「差多少还算对」—— **由声明精度逐项扰动推出**。

    三项：
      · 两端的 ``design_elev_m`` 各舍入 ≤ 半步 —— 这是**实测值**的误差；
      · 两端桩号各舍入 ≤ 半步 —— 桩号错一点，高差就错 ``g·δs``；
      · 每个变坡点的 ``grade_in/out`` 与 ``v_radius`` 各扰动 ±半步 —— 参考值本身的误差。

    ★ 不设「经验系数」。加一项就要说得出它对应哪一列、哪条精度。
    """
    base = integrate_grade(grade_fn, design_points, sa, sb, breaks)
    if base is None:
        return float("inf")
    worst = 0.0
    pts = [dict(p) for p in design_points]
    for idx, p in enumerate(pts):
        for key, q in (("grade_in_pct", precision["grade_pct"] / 2),
                       ("grade_out_pct", precision["grade_pct"] / 2),
                       ("vertical_curve_radius_m", precision["v_radius_m"] / 2)):
            if p.get(key) is None:
                continue
            for sgn in (1.0, -1.0):
                pert = [dict(x) for x in pts]
                pert[idx][key] = p[key] + sgn * q
                v = integrate_grade(grade_fn, pert, sa, sb, breaks)
                if v is not None:
                    worst = max(worst, abs(v - base))
    half_s = precision["station_m"] / 2.0
    gs = abs(grade_fn(design_points, sa) or 0.0) + abs(grade_fn(design_points, sb) or 0.0)
    worst = max(worst, half_s * gs / 100.0)
    return 2.0 * (precision["design_elev_m"] / 2.0) + worst + FLOAT_EPS


def check_profile_consistency(stations: Iterable[Sequence[float]], grade_fn, *,
                              design_points: Iterable[Mapping[str, Any]] = (),
                              precision: Mapping[str, float] = PRECISION,
                              ) -> dict[str, Any]:
    """核 ``design_elev_m`` 与 ``profile_grade_point``（.ZDM）是否一致。

    ``stations`` 是 ``(桩号 m, 设计高程 m)`` 的序列（按桩号升序）。
    ``grade_fn(points, station_m) -> float|None`` 由调用方注入（见 zdm.grade_at）。

    返回::

        {"findings": [...], "intervals": n, "checked": n, "skipped": n,
         "max_ratio": float, "max_finding": {...}|None, "total_observed_m": …,
         "total_expected_m": …}

    每个区间一个判定：``|实测高差 − 解析高差| ≤ 容差``。
    容差由 ``PRECISION`` 里各列的声明精度推出，**不由实测残差反推**。
    """
    dp = list(design_points)
    breaks = breakpoints_of(dp)
    pts = sorted((float(s), float(z)) for s, z in stations)
    findings: list[dict[str, Any]] = []
    checked = skipped = 0
    max_ratio = 0.0
    max_finding: dict[str, Any] | None = None
    for (sa, za), (sb, zb) in zip(pts, pts[1:]):
        if sb <= sa:
            skipped += 1
            continue
        exp = integrate_grade(grade_fn, dp, sa, sb, breaks)
        if exp is None:
            skipped += 1
            continue
        checked += 1
        obs = zb - za
        tol = tolerance_of(grade_fn, dp, sa, sb, breaks, precision=precision)
        err = obs - exp
        ratio = abs(err) / tol if tol else float("inf")
        if ratio > max_ratio:
            max_ratio = ratio
            max_finding = {"station_from_m": sa, "station_to_m": sb,
                           "observed_m": obs, "expected_m": exp,
                           "err_m": err, "tol_m": tol, "ratio": ratio}
        if abs(err) > tol:
            findings.append({"station_from_m": sa, "station_to_m": sb,
                             "observed_m": obs, "expected_m": exp,
                             "err_m": err, "tol_m": tol, "ratio": ratio,
                             "kind": "elevation_grade_mismatch"})
    return {"findings": findings, "intervals": len(pts) - 1,
            "checked": checked, "skipped": skipped,
            "max_ratio": max_ratio, "max_finding": max_finding,
            "total_observed_m": pts[-1][1] - pts[0][1] if pts else 0.0,
            "total_expected_m": (integrate_grade(grade_fn, dp, pts[0][0], pts[-1][0], breaks)
                                 if pts else 0.0)}


__all__ = ["CROSSFALL_COLUMNS", "ROUTES", "KAPPA_TOL", "PRECISION", "FLOAT_EPS",
           "vertical_curve_spans", "in_any_span", "check_arrival", "summarize",
           "breakpoints_of", "integrate_grade", "tolerance_of",
           "check_profile_consistency"]
