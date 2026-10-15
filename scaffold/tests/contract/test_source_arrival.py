"""契约：三路到齐检查（source_arrival）的契约测试。

运行（**离线可跑**，不起容器、不连库）：
  cd /data/cy/shujuku/scaffold
  python3 tests/contract/test_source_arrival.py

钉住的是 `geometry_point` 表注释那句承诺：

    κ 来自 .PM+.JD，G 来自 .ZDM，E 来自 .SUP，三者对齐到 station_sequence 同一基准

**为什么要单独一套**：这个检查最大的风险不是报错，是**永远不报错**。
「列可以为空」这句话只要写成无条件的，这一列就拿到了永久豁免 ——
null 判合法，非 null 也判合法，于是检查恒绿，而恒绿与没在看
在输出上完全一样。所以本文件里**每一条豁免都配一条元测试**：
把前提做反，看它会不会红。见了红才算这条测试真的在测东西。

本套件与 test_coverage_check.py **不是一回事**：那套管「导入文件声明覆盖了
哪些段」，本套管「声明要落的三路是否真的落进了逐桩表」。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in ("M2-ingest",):
    _d = str(ROOT / "modules" / _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import source_arrival as SA  # noqa: E402

_fails: list[str] = []


def ok(what: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    print(f"  {mark} {what}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        _fails.append(what)


# ── 夹具 ────────────────────────────────────────────────────────────────
# 两个变坡点造一条竖曲线：桩号 1000 m，i1=-1% i2=+3%，R=2000
#   L = |3-(-1)|/100*2000 = 80 m   → 区间 [960, 1040]
_VPI = [
    {"station_m": 0.0,   "elevation_m": 10.0, "grade_in_pct": None, "grade_out_pct": -1.0,
     "vertical_curve_radius_m": 0.0},
    {"station_m": 1000.0, "elevation_m": 0.0, "grade_in_pct": -1.0, "grade_out_pct": 3.0,
     "vertical_curve_radius_m": 2000.0},
    {"station_m": 2000.0, "elevation_m": 70.0, "grade_in_pct": 3.0, "grade_out_pct": None,
     "vertical_curve_radius_m": 0.0},
]
#: 竖曲线内（κ 有值也应如此）／竖曲线外
IN_CURVE, OUT_CURVE = 1000.0, 500.0


def row(station: float, *, kappa: float = 0.0, h_radius=10.0,
        v_radius=2000.0, grade=1.0, crossfalls=(3.0, 2.0, 2.0, 2.0, 2.0, 3.0),
        plane=True, profile=True, drop=()) -> dict:
    """造一条逐桩行。默认三路齐全。`drop` 里的列强制置空。"""
    r = {"station_m": station}
    if plane:
        r.update({"x_coord": 1.0, "y_coord": 2.0, "azimuth_deg": 30.0,
                  "curvature_1pm": kappa, "h_radius_m": h_radius})
    if profile:
        r.update({"design_elev_m": 5.0, "grade_pct": grade, "v_radius_m": v_radius})
    for c, v in zip(SA.CROSSFALL_COLUMNS, crossfalls):
        r[c] = v
    for c in drop:
        r[c] = None
    return r


def run(rows: list[dict]) -> dict:
    return SA.check_arrival(rows, design_points=_VPI)


def cols(report: dict) -> set[str]:
    return {f["column"] for f in report["findings"] if f["kind"] == "column_missing"}


def main() -> int:
    print("契约：三路到齐检查（source_arrival）")

    # ── 0. 竖曲线区间推导，与 zdm 对质（防两处公式各走各的）──────────────
    print("0. 竖曲线区间与 zdm 同源")
    spans = SA.vertical_curve_spans(_VPI)
    ok("变坡点 3 个 → 竖曲线 1 段（首尾无曲线）", len(spans) == 1, str(spans))
    ok("区间 = [960, 1040]", spans and abs(spans[0][0] - 960.0) < 1e-9
       and abs(spans[0][1] - 1040.0) < 1e-9, str(spans))
    try:
        # ★ 走包路径，不走文件路径：zdm.py 用的是包内相对导入
        #   （``from ..errors import SourceInvalid``），按文件加载会 ImportError。
        import adapters.weidi.zdm as _zdm  # noqa: E402
        vc = _zdm.vertical_curve_of(_VPI[1])
        ok("与 zdm.vertical_curve_of 的 bvc/evc 一致",
           vc is not None
           and abs(vc["bvc_station_m"] - spans[0][0]) < 1e-9
           and abs(vc["evc_station_m"] - spans[0][1]) < 1e-9,
           f"zdm={vc}")
    except Exception as exc:  # pragma: no cover
        ok("与 zdm.vertical_curve_of 对质", False, f"导入失败 {exc!r}")

    # ── 1. 三路齐全 → 一条 finding 都不该有 ──────────────────────────────
    print("1. 三路齐全 → 无 finding")
    rep = run([row(IN_CURVE), row(OUT_CURVE, v_radius=None)])
    ok("无 finding", not rep["findings"], str(rep["findings"]))

    # ── 2. 纵断面路：grade_pct 整列缺 → 必须报（真实缺陷的形状）──────────
    print("2. grade_pct 整列缺 → 必须报")
    rep = run([row(OUT_CURVE, grade=None) for _ in range(3)])
    ok("报出 grade_pct", cols(rep) == {"grade_pct"}, str(cols(rep)))
    ok("3 行各报一次", rep["by_column"].get("grade_pct") == 3, str(rep["by_column"]))

    # ── 3. h_radius_m 豁免：★ 前提当场核 ────────────────────────────────
    print("3. h_radius_m 豁免 —— 前提必须当场核")
    rep = run([row(OUT_CURVE, kappa=0.0, h_radius=None)])
    ok("κ=0 且空 → 合法，不报", not rep["findings"], str(rep["findings"]))
    rep = run([row(OUT_CURVE, kappa=1.0 / 260.0, h_radius=None)])
    ok("★ 元测试：κ≠0 且空 → 必须报", cols(rep) == {"h_radius_m"}, str(rep["findings"]))

    # ── 4. v_radius_m 豁免：★ 前提当场核 ────────────────────────────────
    print("4. v_radius_m 豁免 —— 前提必须当场核")
    rep = run([row(OUT_CURVE, v_radius=None)])
    ok("曲线外且空 → 合法，不报", not rep["findings"], str(rep["findings"]))
    rep = run([row(IN_CURVE, v_radius=None)])
    ok("★ 元测试：曲线内且空 → 必须报", cols(rep) == {"v_radius_m"}, str(rep["findings"]))

    # ── 5. 横坡六列：单列空合法，六列全空是整路缺席 ──────────────────────
    print("5. 横坡六列 —— 单列空 vs 全空")
    rep = run([row(OUT_CURVE, crossfalls=(3.0, None, 2.0, 2.0, 2.0, 3.0))])
    ok("★ 单列空 → 合法哨兵，不报", not rep["findings"], str(rep["findings"]))
    rep = run([row(OUT_CURVE, crossfalls=(None,) * 6)])
    kinds = {f["kind"] for f in rep["findings"]}
    ok("★ 元测试：六列全空 → route_absent", kinds == {"route_absent"}, str(rep["findings"]))
    ok("判给了 superelev 这一路",
       all(f["route"] == "superelev" for f in rep["findings"]), str(rep["findings"]))

    # ── 6. 整路缺席：三种都要认出来 ─────────────────────────────────────
    print("6. 整路缺席 —— 平面 / 纵断面 / 超高")
    for name, kw in (("plane", {"plane": False}), ("profile", {"profile": False})):
        rep = run([row(OUT_CURVE, **kw)])
        ok(f"{name} 全空 → route_absent",
           {f["kind"] for f in rep["findings"]} == {"route_absent"},
           str(rep["findings"]))
    rep = run([row(OUT_CURVE, plane=False, profile=False, crossfalls=(None,) * 6)])
    ok("三路全空 → 3 条 route_absent", len(rep["findings"]) == 3, str(len(rep["findings"])))

    # ── 7. 空输入 ───────────────────────────────────────────────────────
    print("7. 边界")
    rep = run([])
    ok("空输入 → 0 行 0 finding", rep["stations"] == 0 and not rep["findings"])
    ok("summarize 可调用", isinstance(SA.summarize(rep), list))

    print()
    if _fails:
        print(f"✗ {len(_fails)} 项失败：")
        for f in _fails:
            print(f"    - {f}")
        return 1
    print("✓ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
