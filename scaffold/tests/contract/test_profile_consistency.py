"""契约：设计高程 ↔ 设计纵坡 的一致性（离线，不打库、不起容器）。

运行：
  cd /data/cy/shujuku/scaffold
  python3 tests/contract/test_profile_consistency.py

钉住的性质只有一条：

    **高程沿桩号的变化率，必须等于设计纵坡。**

为什么要单独一条门：`geometry_point` 的 `design_elev_m` 是有值的，
`grade_pct` 是空的。「有值」这件事已经由「三路到齐」那条门管了；
**「值对不对」没有任何东西管** —— 这一条门补的就是它。

★ 两边必须**独立**
--------------------------------------------------------------------------------
  左边 = `design_elev_m` 的实测差（纯数值，不用任何公式）
  右边 = 由 .ZDM 变坡点算出的解析积分 ∫ g ds

若拿同一个函数既算高程又算纵坡再互比，那是**自证**，什么都验不出来。
所以本文件里 `grade_at` 与 `design_elevation_at` **要先被互相对质**
（§1 用数值微分把两者钉在一起），之后才允许拿它当参考。

★ 容差从**列声明精度**推，不从实测残差反推
--------------------------------------------------------------------------------
「差多少算对」如果由实现反推，这门永远绿。故 `PRECISION` 里每一项都
对应一个 `information_schema` 实测的 numeric 标度，§5 专门钉这件事。

★ 元测试纪律（同 test_source_arrival.py）
--------------------------------------------------------------------------------
每一条判定都配一条元测试：故意写错，看它会不会红。见了红才算在测东西。
另外 §4 还有一条**反向**元测试 —— 证明这门**不会瞎红**：
整体平移高程不改变任何高差，故必须**不报**。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
_d = str(ROOT / "modules" / "M2-ingest")
if _d not in sys.path:
    sys.path.insert(0, _d)

import adapters.weidi.zdm as zdm  # noqa: E402
import source_arrival as SA  # noqa: E402

_fails: list[str] = []


def ok(what: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    print(f"  {mark} {what}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        _fails.append(what)


# ── 夹具：3 条竖曲线，凸凹都有，两端为切线 ─────────────────────────────
#  VPI2 300 m:  +0.520 → −0.971  R=6000  ⇒ ω=−1.491%  L=89.46   T=44.73（凸）
#  VPI3 790 m:  −0.971 → +2.500  R=3000  ⇒ ω=+3.471%  L=104.13  T=52.07（凹）
#  VPI4 1400 m: +2.500 → −1.000  R=5000  ⇒ ω=−3.500%  L=175.00  T=87.50（凸）
_VPI = [
    {"vpi_seq": 1, "station_m": 0.0, "elevation_m": 57.2620, "grade_in_pct": None,
     "grade_out_pct": 0.520, "vertical_curve_radius_m": 0.0},
    {"vpi_seq": 2, "station_m": 300.0, "elevation_m": 58.8220, "grade_in_pct": 0.520,
     "grade_out_pct": -0.971, "vertical_curve_radius_m": 6000.0},
    {"vpi_seq": 3, "station_m": 790.0, "elevation_m": 54.0641, "grade_in_pct": -0.971,
     "grade_out_pct": 2.500, "vertical_curve_radius_m": 3000.0},
    {"vpi_seq": 4, "station_m": 1400.0, "elevation_m": 69.3141, "grade_in_pct": 2.500,
     "grade_out_pct": -1.000, "vertical_curve_radius_m": 5000.0},
    {"vpi_seq": 5, "station_m": 2000.0, "elevation_m": 63.3141, "grade_in_pct": -1.000,
     "grade_out_pct": None, "vertical_curve_radius_m": 0.0},
]
#: 20 m 一个桩号，与真实库同密度
_STATIONS = [float(s) for s in range(0, 2001, 20)]


def z_of(s: float, vpi=None) -> float:
    return zdm.design_elevation_at(vpi or _VPI, s)


def run(rows=None, fn=None, vpi=None) -> dict:
    pts = rows if rows is not None else [(s, z_of(s, vpi)) for s in _STATIONS]
    return SA.check_profile_consistency(pts, fn or zdm.grade_at,
                                        design_points=vpi or _VPI)


def main() -> int:
    print("契约：设计高程 ↔ 设计纵坡 一致性")

    # ── 1. ★ 先把两个函数互相对质：grade_at 必须是 design_elevation_at 的导数 ──
    print("1. ★ 参考函数必须先被对质（否则是自证）")
    probes = [100.0, 300.0, 320.0, 790.0, 800.0, 1400.0, 1360.0, 1800.0]
    worst = 0.0
    for s in probes:
        h = 1e-3
        num = (z_of(s + h) - z_of(s - h)) / (2 * h) * 100.0   # 中心差分 → %
        ana = zdm.grade_at(_VPI, s)
        worst = max(worst, abs(num - ana))
    ok("★ 数值微分 == grade_at（抛物线中心差分是精确的）", worst < 1e-9,
       f"最大差 {worst:.3e} 个百分点")
    ok("★ 元测试：取和式差分（非中心），必须**不再相等**",
       abs((z_of(320.0 + 1e-3) - z_of(320.0)) / 1e-3 * 100.0
           - zdm.grade_at(_VPI, 320.0)) > 1e-6)
    c2 = zdm.vertical_curve_of(_VPI[1])
    ok("BVC 处纵坡 = 入坡", abs(zdm.grade_at(_VPI, c2["bvc_station_m"]) - 0.520) < 1e-9)
    ok("EVC 处纵坡 = 出坡", abs(zdm.grade_at(_VPI, c2["evc_station_m"]) - (-0.971)) < 1e-9)
    ok("VPI 处纵坡 = 两坡中点",
       abs(zdm.grade_at(_VPI, 300.0) - (0.520 + (-0.971)) / 2) < 1e-9)
    ok("范围外返回 None（不外推）", zdm.grade_at(_VPI, -1.0) is None
       and zdm.grade_at(_VPI, 2001.0) is None)

    # ── 2. 一致的数据 → 无 finding ──────────────────────────────────────
    print("2. 一致的数据 → 无 finding")
    r = run()
    ok("区间全部核到，一个不跳", r["checked"] == len(_STATIONS) - 1 and r["skipped"] == 0,
       f"checked={r['checked']} skipped={r['skipped']}")
    ok("无 finding", not r["findings"], str(r["findings"][:2]))
    ok("容差占用极小（合成数据无存储舍入）", r["max_ratio"] < 1e-6,
       f"max_ratio={r['max_ratio']:.3e}")

    # ── 3. ★ 元测试：改坏高程，必须红 ───────────────────────────────────
    print("3. ★ 元测试：高程被动过 → 必须报")
    bad = [(s, z_of(s)) for s in _STATIONS]
    i = 50
    bad[i] = (bad[i][0], bad[i][1] + 0.010)          # 一个桩号抬高 10 mm
    r = run(bad)
    ok("★ 单点抬高 10 mm → 报出 2 个区间（该点两侧）",
       len(r["findings"]) == 2, f"报出 {len(r['findings'])} 个")
    ok("报的桩号正是改动点两侧",
       {f["station_to_m"] for f in r["findings"]} | {f["station_from_m"] for f in r["findings"]}
       == {bad[i - 1][0], bad[i][0], bad[i + 1][0]},
       str([(f["station_from_m"], f["station_to_m"]) for f in r["findings"]]))
    # ★ 抬高一个点，两侧的高差**必然反号**：前一个区间变大、后一个变小。
    #   我原先写成「都同号」—— 那是我预期错了，不是检查器错。
    errs = sorted(f["err_m"] for f in r["findings"])
    ok("★ 两侧误差反号（前区间 +、后区间 −）", errs[0] < 0 < errs[1], str(errs))
    ok("★ 两侧误差等大（改动量同为一个 0.010 m）",
       abs(abs(errs[0]) - abs(errs[1])) < 1e-9, str(errs))
    # 容差 ≈ 0.2 mm；0.010 m 的改动 ⇒ 占用 ≈ 50 倍。断言取 >10 倍即"远离噪声"。
    ok("★ 元测试有量级：0.01 m 改动 → 容差占用 >10 倍（实际约 50 倍）",
       all(f["ratio"] > 10 for f in r["findings"]),
       str([round(f["ratio"], 1) for f in r["findings"]]))
    ok("★ 顺带钉住容差量级：≈ 0.2 mm（不是 2 mm、也不是 2 µm）",
       1e-4 < r["findings"][0]["tol_m"] < 1e-3,
       f"tol={r['findings'][0]['tol_m']:.3e} m")

    # ── 4. ★ 反向元测试：证明它不会瞎红 ─────────────────────────────────
    print("4. ★ 反向元测试：不该红的不能红")
    shifted = [(s, z + 1000.0) for s, z in ((s, z_of(s)) for s in _STATIONS)]
    ok("★ 整体抬高 1000 m → 必须**不报**（核的是高差，不是绝对值）",
       not run(shifted)["findings"])
    ok("★ 只给两个桩号（一对）也能核", run([(0.0, z_of(0.0)), (100.0, z_of(100.0))])["checked"] == 1)
    ok("桩号重复/逆序的被跳过，不报假红", run([(0.0, 1.0), (0.0, 1.0)])["findings"] == [])

    # ── 5. ★ 元测试：容差必须由精度推出，不由残差反推 ───────────────────
    print("5. ★ 容差从列声明精度推，不从残差反推")
    tol0 = SA.tolerance_of(zdm.grade_at, _VPI, 300.0, 320.0, SA.breakpoints_of(_VPI))
    coarse = dict(SA.PRECISION)
    coarse["grade_pct"] = SA.PRECISION["grade_pct"] * 100.0      # 纵坡精度粗 100 倍
    tol1 = SA.tolerance_of(zdm.grade_at, _VPI, 300.0, 320.0, SA.breakpoints_of(_VPI),
                           precision=coarse)
    ok("★ 纵坡精度粗 100 倍 → 容差必须变宽", tol1 > tol0 * 2, f"{tol0:.3e} → {tol1:.3e}")
    zc = dict(SA.PRECISION)
    zc["design_elev_m"] = SA.PRECISION["design_elev_m"] * 10.0   # 高程精度粗 10 倍
    tol2 = SA.tolerance_of(zdm.grade_at, _VPI, 300.0, 320.0, SA.breakpoints_of(_VPI),
                           precision=zc)
    ok("★ 高程精度粗 10 倍 → 容差单调不减", tol2 >= tol0, f"{tol0:.3e} → {tol2:.3e}")
    ok("容差是有限的正常量级（不是 inf/NaN）",
       tol0 < 1e-2 and tol0 > 0, f"{tol0:.3e} m")
    ok("★ 元测试：容差调大到荒谬 → 真缺陷也报不出来（说明容差本身就是判据）",
       not run(bad, vpi=_VPI)["findings"] if False else
       len(SA.check_profile_consistency(
           bad, zdm.grade_at, design_points=_VPI,
           precision={**SA.PRECISION, "design_elev_m": 1.0})["findings"]) == 0)

    # ── 6. 参考函数被换掉 → 必须红 ──────────────────────────────────────
    print("6. ★ 元测试：参考函数整个反号 → 必须报")
    r = run(fn=lambda pts, s: (-zdm.grade_at(pts, s)
                               if zdm.grade_at(pts, s) is not None else None))
    ok("★ 纵坡全部反号 → 每个区间都报", len(r["findings"]) == r["checked"],
       f"{len(r['findings'])}/{r['checked']}")

    # ── 7. 断点 ─────────────────────────────────────────────────────────
    print("7. 折点")
    br = SA.breakpoints_of(_VPI)
    ok("折点 = 5 个变坡点 + 6 个 BVC/EVC", len(br) == 11, str(len(br)))
    ok("折点含 BVC 255.27 与 EVC 344.73",
       any(abs(x - 255.27) < 0.01 for x in br) and any(abs(x - 344.73) < 0.01 for x in br))

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
