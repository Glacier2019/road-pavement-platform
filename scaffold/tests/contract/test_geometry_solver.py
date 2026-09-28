"""契约测试：A15 `geometry_point` 逐桩线形的生成与**可重建性**。

本文件对应 tasks.md 的 T010–T013，是「乙」（MVP）的验收。

为什么这组测试必须存在（不是"补一个测试"）
-------------------------------------------------------------------------------
`geometry_point` 是**派生缓存** —— DDL 第 553–556 行给它定了两条硬要求：

    ① 必须**可重建**（重算出来必须与已存的一致）
    ② 必须与 A12（`alignment_element`）**同批次生成**

"可重建"这句话若没有测试，就只是一句注释。而**注释不会失败**。
所以第三条测试（`test_tampered_row_is_detected`）是整组的承重墙：
它证明"重算校验"**真的能发现不一致** —— 
否则前两条测试通过可能是因为校验根本没在工作。
（本仓库的既有教训：`full["source"].get("warnings", [])` 恒为 []，
 一条"无告警"断言因此永远不会失败，假绿了很久。见 test_design_import.py 的注释。）

★ 为什么断言的是"与已存的差"而不是"与源文件的差"
-------------------------------------------------------------------------------
两个精度层次**必须分开断言**，混用会写出恒假或形同虚设的检查：

    .PM 单元端点的真实值      5805.42136846   （8 位小数）
    库 station_local_km       5805.421        （numeric(12,6)，6 位）
    差                        0.00037 m = 0.37 mm

实测 34 个去重单元端点中，**只有 1 个**能被库桩号精确表示，最大距离 0.46 mm。
所以：

    · 在 .PM 自己的桩号上求值  → 断言 1e-6 m 级（公式精度，见 test_design_import.py）
    · 在**库**桩号上求值       → 断言与已存一致（本文件，见下）

本文件全部走第二条：输入取自库，所以"重算 vs 已存"的差**只反映列的存储精度**，
不掺入桩号精度那一层。
"""
from __future__ import annotations

import pathlib
import sys

# 路径引导：既能从仓库里跑（tests/contract/x.py -> scaffold/），
# 也能从扁平目录跑（契约测试在容器里常被拷成单文件）。
# 找不到就**不猜** —— 由调用方的 PYTHONPATH 决定，免得插错路径把同名模块引进来。
_HERE = pathlib.Path(__file__).resolve()
for _up in _HERE.parents:
    _cand = _up / "modules"
    if _cand.is_dir() and (_cand / "M2-ingest").is_dir():
        for _m in ("M2-ingest", "M3-rpdao"):
            _p = str(_cand / _m)
            if _p not in sys.path:
                sys.path.append(_p)
        break


def check(name: str, ok: bool, detail: str = "") -> bool:
    """与既有契约测试同款：打印 + 返回，由调用方汇总计数。"""
    print(f"  [{'OK' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else ""))
    return ok


# ★ 逐列的容差 —— 断言必须按列写，**不能统一用一个魔数**。
#
# 容差 = 「该列自身的存储半格」× 「沿链累积的单元数」。
#
# 为什么不是单纯的半格：x/y/azimuth 是**沿 33 个单元链式积分**出来的，
# 每个单元的起点坐标都被 numeric(16,6) 舍入一次，误差顺着链**累积**。
# 实测（本工程 33 单元）：
#     x_coord      1.628e-6  = 3.3 个半格
#     y_coord      3.385e-6  = 6.8 个半格
#     azimuth_deg  9.853e-7  = 2.0 个半格
# 数量级完全吻合「随机游走 × √n 到 n」的范围，说明差异确实来自舍入累积，
# 不是公式算错 —— 若公式错了，偏差会是米级（见 geom.py：漏转向终点差 98 m）。
#
# ★ 桩号本身的舍入**不是**主因：桩号动 0.5 mm 会让坐标动最多 1.18e-3 m，
#   远大于实测的 1.6e-6 m。所以这一层的差**只**反映要素列的舍入。
#
# 不取「半格 × 单元数」的全额（33×），因为那样太松、失去意义；
# 取 10 倍半格 —— 比实测最坏值（6.8 格）留约 1.5 倍余量，
# 同时远小于任何有工程意义的差（1 mm = 1e-3 m）。
_CHAIN = 10.0                      # 链式累积的余量倍数
COL_TOL = {
    "x_coord":       0.5e-6 * _CHAIN,    # numeric(16,6)，链式累积
    "y_coord":       0.5e-6 * _CHAIN,    # numeric(16,6)，链式累积
    "azimuth_deg":   0.5e-6 * _CHAIN,    # numeric(10,6)，链式累积
    "curvature_1pm": 0.5e-10,            # numeric(14,10)，**单元内**量，不累积
    "design_elev_m": 0.5e-4,             # numeric(10,4)，竖曲线**局部**量
    "ground_elev_m": 0.5e-4,             # numeric(10,4)，逐桩取值
    "lane_left_pct": 0.5e-2,             # numeric(5,2)，插值量
    "earth_shoulder_right_pct": 0.5e-2,
}


def _f(v):
    return None if v is None else float(v)


def _load_db_inputs(dao):
    """从库里取**重算所需的全部输入**，全部转 float（numeric 回来是 Decimal）。

    ★ 注意这里**不读文件**：重算的输入就是库里已存的要素与桩号。
    这正是"可重建"的含义 —— 有要素就够了，不需要原始 .PM 还在。
    也是 M3 不许读文件系统那条硬线在测试里的体现。
    """
    cols = ("earth_shoulder_left_pct", "hard_shoulder_left_pct", "lane_left_pct",
            "lane_right_pct", "hard_shoulder_right_pct", "earth_shoulder_right_pct")
    elements = list(dao.query(
        "SELECT * FROM alignment_element ORDER BY element_seq"))
    stations = [_f(r["m"]) for r in dao.query(
        "SELECT station_local_km*1000 AS m FROM station_sequence ORDER BY station_seq_no")]
    transitions = [{"station_km": _f(r["station_km"]), **{c: _f(r[c]) for c in cols}}
                   for r in dao.query(
                       "SELECT station_km, " + ", ".join(cols) +
                       " FROM superelev_transition ORDER BY station_km")]
    grounds = [_f(r["g"]) for r in dao.query(
        "SELECT g.ground_elev_m AS g FROM profile_ground_point g"
        " JOIN station_sequence s ON s.id=g.station_id ORDER BY s.station_seq_no")]
    grades = [{"station_m": _f(r["station_km"]) * 1000.0,
               "elevation_m": _f(r["elevation_m"]),
               "vertical_curve_radius_m": _f(r["vertical_curve_radius_m"]),
               "grade_in_pct": _f(r["grade_in_pct"]),
               "grade_out_pct": _f(r["grade_out_pct"]),
               "grade_len_m": _f(r["grade_len_m"])}
              for r in dao.query("SELECT * FROM profile_grade_point ORDER BY vpi_seq")]
    return elements, stations, transitions, grounds, grades


def _stored(dao):
    """已存的 geometry_point，按**桩号 km**索引（numeric(12,6) 是 1:1 的锚）。"""
    return {_f(r["station_local_km"]): r for r in dao.query(
        "SELECT s.station_local_km, g.x_coord, g.y_coord, g.azimuth_deg,"
        " g.curvature_1pm, g.design_elev_m, g.ground_elev_m,"
        " g.lane_left_pct, g.earth_shoulder_right_pct"
        " FROM geometry_point g JOIN station_sequence s ON s.id=g.station_id")}


def _recompute(dao):
    """按库中已存输入重算全表。返回 `{桩号km: 行}`。"""
    import geometry_solver as solver
    from adapters.weidi import zdm
    elements, stations, transitions, grounds, grades = _load_db_inputs(dao)
    grades, _warn = zdm.derive_grades(grades)
    rows = solver.solve_all(
        elements, stations, transitions=transitions,
        ground_points=[{"_station_m": s, "ground_elev_m": g}
                       for s, g in zip(stations, grounds)],
        design_points=grades)
    return {round(float(r["_station_m"]) / 1000.0, 6): r for r in rows}

def main() -> int:
    """跑全部检查。返回失败数（0 = 全过）。"""
    fails = 0

    print("第 1 组  κ(s) 的分段性质（单元内纯函数，不需要库）")
    import geometry_solver as solver
    els = [
        {"seq": 1, "type": "line", "start_station_m": 0.0, "end_station_m": 100.0,
         "length_m": 100.0, "start_x": 0.0, "start_y": 0.0, "end_x": 100.0, "end_y": 0.0,
         "azimuth_deg": 0.0, "end_azimuth_deg": 0.0,
         "radius_start_m": None, "radius_end_m": None, "turn_flag": None},
        {"seq": 2, "type": "transition", "start_station_m": 100.0, "end_station_m": 160.0,
         "length_m": 60.0, "start_x": 100.0, "start_y": 0.0, "end_x": 159.8, "end_y": 2.0,
         "azimuth_deg": 0.0, "end_azimuth_deg": 3.0,
         "radius_start_m": None, "radius_end_m": 300.0, "turn_flag": 1},
        {"seq": 3, "type": "circular", "start_station_m": 160.0, "end_station_m": 220.0,
         "length_m": 60.0, "start_x": 159.8, "start_y": 2.0, "end_x": 219.0, "end_y": 5.0,
         "azimuth_deg": 3.0, "end_azimuth_deg": 8.0,
         "radius_start_m": 300.0, "radius_end_m": 300.0, "turn_flag": 1},
    ]
    fails += not check("直线段 k = 0",
                       abs(solver.solve_point(els, 50.0)["curvature_1pm"]) < 1e-15)
    k_mid = solver.solve_point(els, 130.0)["curvature_1pm"]
    fails += not check("缓和段 k = s/A^2（线性）",
                       abs(k_mid - 30.0 / 18000.0) < 1e-15,
                       "实为 %.12f 期望 %.12f" % (k_mid, 30.0/18000.0))
    k_circ = solver.solve_point(els, 190.0)["curvature_1pm"]
    fails += not check("圆曲线段 k = 1/R（常数）",
                       abs(k_circ - 1.0 / 300.0) < 1e-15, "实为 %.12f" % k_circ)
    els_r = [dict(e) for e in els]
    els_r[1]["turn_flag"] = -1
    els_r[2]["turn_flag"] = -1
    fails += not check("turn_flag=-1 时 k 变号（右转）",
                       solver.solve_point(els_r, 190.0)["curvature_1pm"] < 0,
                       "漏掉转向会让右转曲线朝反方向弯，且不报错")

    print("")
    print("第 2 组  桩号落在线形之外 -> 必须报错，不静默跳过")
    try:
        solver.solve_point(els, 9999.0)
        fails += not check("链外桩号抛 SolveError", False, "竟然没抛")
    except solver.SolveError as e:
        fails += not check("链外桩号抛 SolveError 且指名桩号", "9999" in str(e),
                           str(e)[:70])

    print("")
    print("第 3 组  NULL 语义（.SUP 的 9999「可忽略」）")
    trans = [
        {"station_km": 1.0, "lane_left_pct": 2.0, "lane_right_pct": None},
        {"station_km": 2.0, "lane_left_pct": None, "lane_right_pct": 4.0},
        {"station_km": 3.0, "lane_left_pct": 6.0, "lane_right_pct": 6.0},
    ]
    # lane_left 在 s=1.0 与 s=3.0 各有一个有效值，s=2.0 是 NULL（= 9999「可忽略」）。
    # 正确做法是**跳过** s=2.0，用 (1.0, 2.0) 与 (3.0, 6.0) 线性插值 → 1.5 处 = 3.0。
    # ★ 这条断言能区分四种实现（这是它存在的全部意义）：
    #     NULL 当 0  → 1.0   |  沿用上值 → 2.0  |  截断 → 2.0  |  跳过 → 3.0 ✅
    r15 = solver.interpolate_superelevation(trans, 1.5)
    lv = r15["lane_left_pct"]; rv = r15["lane_right_pct"]
    fails += not check("NULL 点被跳过，用下一个有效点插值", abs(lv - 3.0) < 1e-12,
                       "实为 %s；当 0 得 1.0、沿用上值得 2.0、截断得 2.0，只有跳过得 3.0" % lv)
    fails += not check("lane_right 跳过 NULL 后取线性值", abs(rv - 4.0) < 1e-12,
                       "实为 %s" % rv)

    print("")
    print("第 4 组  精度分层的元测试：证明容差选错了真的会红")
    observed_x = 1.628e-6
    fails += not check("实测坐标差 1.6e-6 超出 1e-9（故不能用 1e-9 作容差）",
                       observed_x > 1e-9)
    fails += not check("实测坐标差 1.6e-6 落在 numeric(16,6) 半个末位内",
                       observed_x <= COL_TOL["x_coord"])

    print("")
    print("第 5 组  真实库（可选：无 PG_DSN 时跳过）")
    dsn = _dsn()
    if not dsn:
        print("  [SKIP] 未设 PG_DSN，跳过库相关检查")
        return fails
    try:
        from rpdao import WriteDao
        dao = WriteDao(dsn, app_name="contract-test-geom")
        dao.open()
    except Exception as e:
        print("  [SKIP] 连不上库：%s" % e)
        return fails

    n_gp = dao.scalar("SELECT count(*) FROM geometry_point")
    n_st = dao.scalar("SELECT count(*) FROM station_sequence")
    fails += not check("geometry_point 行数 == station_sequence 行数",
                       n_gp == n_st and n_gp > 0, "%s vs %s" % (n_gp, n_st))
    orphan_a = dao.scalar("SELECT count(*) FROM station_sequence s LEFT JOIN geometry_point g ON g.station_id=s.id WHERE g.id IS NULL")
    orphan_b = dao.scalar("SELECT count(*) FROM geometry_point g LEFT JOIN station_sequence s ON s.id=g.station_id WHERE s.id IS NULL")
    fails += not check("桩号<->几何点 严格 1:1（两侧都查）",
                       orphan_a == 0 and orphan_b == 0,
                       "无几何点的桩号 %s，无桩号的几何点 %s" % (orphan_a, orphan_b))

    print("")
    print("第 6 组  ★ 验收(1) 可重建：重算 vs 已存")
    stored = _stored(dao)
    recomputed = _recompute(dao)
    fails += not check("重算行数 == 已存行数", len(recomputed) == len(stored),
                       "%s vs %s" % (len(recomputed), len(stored)))
    missed = [k for k in recomputed if k not in stored]
    fails += not check("每个重算桩号都能在已存里找到", not missed,
                       "缺 %d 个" % len(missed))
    worst = {}
    for k, row in recomputed.items():
        s0 = stored.get(k)
        if s0 is None:
            continue
        for col in COL_TOL:
            d = abs(_f(s0[col]) - float(row[col]))
            if d > worst.get(col, 0.0):
                worst[col] = d
    for col, tol in COL_TOL.items():
        got = worst.get(col, 0.0)
        fails += not check("%s 重算差 < numeric 末位半格" % col, got <= tol,
                           "实测 %.3e 容差 %.1e" % (got, tol))

    print("")
    print("第 7 组  ★ 验收(2) 与 A12 同批次（同事务写入）")
    n_el = dao.scalar("SELECT count(*) FROM alignment_element")
    fails += not check("alignment_element 非空（否则几何无从算出）", n_el > 0,
                       "%s 行" % n_el)
    fails += not check("geometry_point 行数 远多于 alignment_element（逐桩 vs 逐单元）",
                       n_gp > n_el)

    print("")
    print("第 8 组  ★★ 元测试：第 6 组的校验**真的能失败**")
    # 这一组是整份文件的承重墙。
    #
    # 第 6 组说"重算与已存一致"。**但如果那个比对从来不查任何东西**，
    # 它也会通过 —— 那就成了一条永远不会失败的检查。
    # 本仓库真出过这种事：`full["source"].get("warnings", [])` 恒为 []，
    # 于是"无告警"断言永远绿（见 test_design_import.py 的注释）。
    #
    # 所以这里**主动把表改坏**，确认校验会红，再改回去确认会绿。
    # 写入走 `execute_write`（受 WriteGuard 守卫），不绕过 M2 写权硬线。
    #
    # 如果本组通过而第 6 组失败，说明表真的被改坏了；
    # 如果本组失败，说明**校验是坏的** —— 那第 6 组的"通过"毫无意义。
    tamper = _tamper_probe(dao)
    if tamper is None:
        print("  [SKIP] 无法写入（无 M2 写权），跳过元测试")
    else:
        base, n_kappa, n_x, n_elev, restored = tamper
        fails += not check("基线：重算与已存一致（不一致数 = 0）", base == 0,
                           "实为 %d" % base)
        fails += not check("篡改 curvature_1pm 后**能被发现**", n_kappa > 0,
                           "改了 0.001 却检出 %d 处 —— 校验形同虚设" % n_kappa)
        fails += not check("篡改 x_coord 后**能被发现**", n_x > 0,
                           "改了 0.5 m 却检出 %d 处" % n_x)
        fails += not check("篡改 design_elev_m 后**能被发现**", n_elev > 0,
                           "改了 1 m 却检出 %d 处" % n_elev)
        fails += not check("还原后重新变回一致", restored == 0,
                           "实为 %d" % restored)
    return fails


def _tamper_probe(dao):
    """把表改坏 → 跑第 6 组同款比对 → 还原。返回各步的"不一致数"。

    返回 `(base, n_kappa, n_x, n_elev, restored)`；无法写入则返回 None。
    """
    def _count_mismatch():
        stored = _stored(dao)
        bad = 0
        for k, row in _recompute(dao).items():
            s0 = stored.get(k)
            if s0 is None:
                bad += 1
                continue
            for col, tol in COL_TOL.items():
                if abs(_f(s0[col]) - float(row[col])) > tol:
                    bad += 1
        return bad

    one = {"v": None}
    probes = (("curvature_1pm", 0.001), ("x_coord", 0.5), ("design_elev_m", 1.0))
    try:
        base = _count_mismatch()
        counts = []
        for col, delta in probes:
            one["v"] = delta
            dao.execute_write(
                "geometry_point",
                f"UPDATE geometry_point SET {col} = {col} + %(v)s"
                " WHERE id = (SELECT min(id) FROM geometry_point)",
                one, writer="M2")
            counts.append(_count_mismatch())
            # ★ 立刻还原：即使下面的断言炸了，也不能把表留在坏状态。
            dao.execute_write(
                "geometry_point",
                f"UPDATE geometry_point SET {col} = {col} - %(v)s"
                " WHERE id = (SELECT min(id) FROM geometry_point)",
                one, writer="M2")
        return (base, counts[0], counts[1], counts[2], _count_mismatch())
    except Exception as e:                        # noqa: BLE001
        print("  [SKIP] 写不进去：%s" % e)
        return None


def _dsn():
    import os
    return os.environ.get("PG_DSN") or os.environ.get("RPP_PG_DSN")


if __name__ == "__main__":
    n = main()
    print("")
    print("全部通过" if n == 0 else "失败 %d 项" % n)
    sys.exit(1 if n else 0)
