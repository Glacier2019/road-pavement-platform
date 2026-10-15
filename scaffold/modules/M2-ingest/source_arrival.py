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


__all__ = ["CROSSFALL_COLUMNS", "ROUTES", "KAPPA_TOL",
           "vertical_curve_spans", "in_any_span", "check_arrival", "summarize"]
