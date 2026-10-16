"""平面线形积分闭合契约 —— 积分出来的线必须对得上数据库里那套坐标。

运行（**离线可跑，不连数据库、不连容器、不连外网**）：

    cd /data/cy/shujuku/scaffold
    python3 tests/contract/test_alignment_closure.py

为什么必须有这个文件
--------------------

2026-10-16 有人问：「那张直纹面是用数据库数据积分算出来的吗？」
查下去发现两件事：

  ① 平面坐标**真的是积出来的** —— 中点法，0.1 m 一步，58 054 步；
  ② `alignment_element` 里**存着**六个坐标列
     （`start_x/start_y/end_x/end_y/center_x/center_y`），
     而 `gen_crg.py` **一列都没读**（搜这些名字零命中）。

⇒ 于是同一条路有**两个独立来源**。手工对账：刚体配准后残差最大 0.471 mm、
  均方根 0.303 mm（66 个点，5.8 km）—— 对得上。

★★ 但那次对账是**手工的**，下一次没人会对。
   这正是 D-032 的教训换个地方长回来：一份没人跑的检查，和没有检查，
   对后来的人是一样的。

★ 见证是**数据库给的**，不是我们算的 —— 这正是它能当证人的原因。
  所以夹具里那 66 个坐标值必须原样保留，判据 1 就是守这件事。
  夹具再生（真库导出，NULL 保持空，与 `gen_crg.py` 的读法一致）::

    docker exec rp-pg psql -U rp -d road_pavement -t -A -F, -c \
      "SELECT element_seq,element_type,start_station_km,end_station_km,start_x,start_y,
              end_x,end_y,azimuth_deg,end_azimuth_deg,radius_start_m,radius_end_m
       FROM alignment_element ORDER BY element_seq;" > <表头见夹具第一行>

六组判据
--------

  1. 夹具形态 —— 33 段、66 个坐标齐全、里程首尾相接、全长 5805.421、坐标不退化
  2. 积分收敛 —— 0.1 m 与 0.01 m 的终点差 < 0.01 mm
  3. ★ 闭合   —— 配准后残差最大 < 1.0 mm（实测 0.471）
  4. 弦长     —— 积分总弦长 vs 夹具首末点距离，差 < 1 mm
  5. ★ 有靶子 —— 四个**真 bug** 必须让判据 3 变红；一个**等价实现**必须不变红
     （5a 真 bug：半径哨兵 / sin-cos 互换 / 曲率符号取反 / 步长 50 m）
     （5b 等价：段边界归属 —— 边界两侧方位角相同，本数据测不出差）
  6. 接线     —— `gen_crg.py` 必须**调** `alignment.py`，不得自己再写一份
"""
from __future__ import annotations

import bisect
import csv
import math
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SCAFFOLD = HERE.parent.parent
CRG_DIR = SCAFFOLD / "simulator" / "crg"
FIXTURE = HERE / "fixtures" / "alignment_elements.csv"

sys.path.insert(0, str(CRG_DIR))
import alignment as AL  # noqa: E402

#: 残差上限（米）。实测 0.471 mm，留一倍余量。
TOL_M = 1.0e-3
#: 收敛上限（米）。实测 0.000046 mm。
TOL_CONV_M = 1.0e-5
#: 全长（米）。夹具末段的 end_station_km 必须等于它。
TOTAL_M = 5805.421

FAILS: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print("  %s %s%s" % ("✓" if ok else "✗", label, ("　" + detail) if detail else ""))
    if not ok:
        FAILS.append(label)
    return ok


def num(s):
    """夹具里的**空串** = 数据库的 NULL。★ 不要把它读成 0 或 -1。"""
    s = (s or "").strip()
    return None if s in ("", "NULL") else float(s)


def load_fixture():
    with FIXTURE.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def to_elements(rows):
    """夹具 → `gen_crg.py` 用的那种 E（**键名与它逐字一致**）。"""
    out = []
    for r in rows:
        out.append(dict(
            seq=int(r["element_seq"]), t=r["element_type"],
            s0=num(r["start_station_km"]) * 1000, s1=num(r["end_station_km"]) * 1000,
            az0=num(r["azimuth_deg"]), az1=num(r["end_azimuth_deg"]),
            r0=num(r["radius_start_m"]), r1=num(r["radius_end_m"]),
            x0=num(r["start_x"]), y0=num(r["start_y"]),
            x1=num(r["end_x"]), y1=num(r["end_y"]),
        ))
    return out


def sample(pts, U, u):
    """在积分链上按里程线性插值取 (x, y)。"""
    i = bisect.bisect_left(U, u)
    if i <= 0:
        return pts[0][1], pts[0][2]
    if i >= len(U):
        return pts[-1][1], pts[-1][2]
    u0, u1 = U[i - 1], U[i]
    t = 0.0 if u1 == u0 else (u - u0) / (u1 - u0)
    return (pts[i - 1][1] + t * (pts[i][1] - pts[i - 1][1]),
            pts[i - 1][2] + t * (pts[i][2] - pts[i - 1][2]))


def rigid_residual(A, B):
    """把 A **刚体配准**到 B（先对齐位置与朝向，再看形状），返回逐点残差（米）。

    2D 的闭式 Procrustes：最优转角 θ = atan2(Σ a×b, Σ a·b)。
    不用 numpy —— 这条门不为此加依赖。
    """
    n = len(A)
    ax = sum(p[0] for p in A) / n
    ay = sum(p[1] for p in A) / n
    bx = sum(p[0] for p in B) / n
    by = sum(p[1] for p in B) / n
    dot = cross = 0.0
    for (x, y), (X, Y) in zip(A, B):
        x -= ax; y -= ay; X -= bx; Y -= by
        dot += x * X + y * Y
        cross += x * Y - y * X
    th = math.atan2(cross, dot)
    c, s = math.cos(th), math.sin(th)
    res = []
    for (x, y), (X, Y) in zip(A, B):
        x -= ax; y -= ay
        res.append(math.hypot(c * x - s * y + bx - X, s * x + c * y + by - Y))
    return res, th


def closure(E, step=AL.DEFAULT_STEP):
    """对账：积分 → 按段边界取样 → 刚体配准到夹具坐标。返回 (max, rms, 弦长差)。"""
    pts = AL.integrate(E, step)
    U = [p[0] for p in pts]
    A, B = [], []
    for el in E:
        A.append(sample(pts, U, el["s0"])); B.append((el["x0"], el["y0"]))
        A.append(sample(pts, U, el["s1"])); B.append((el["x1"], el["y1"]))
    res, _ = rigid_residual(A, B)
    mx = max(res)
    rms = math.sqrt(sum(r * r for r in res) / len(res))
    chord_i = math.hypot(pts[-1][1] - pts[0][1], pts[-1][2] - pts[0][2])
    chord_f = math.hypot(B[-1][0] - B[0][0], B[-1][1] - B[0][1])
    return mx, rms, abs(chord_i - chord_f)


def main() -> int:
    print("平面线形积分闭合契约 —— 论文 (2.15) 前两个积分 vs 数据库坐标")
    print("  积分实现 %s" % (CRG_DIR / "alignment.py"))
    print("  见证     %s" % FIXTURE)

    rows = load_fixture()
    E = to_elements(rows)

    # ---------- 1. 夹具形态 ----------
    print("\n【1】夹具形态 —— 见证必须完整，否则门是空的")
    check("33 段线元", len(rows) == 33, "实得 %d" % len(rows))
    blanks = [r["element_seq"] for r in rows
              if any((r[k] or "").strip() == "" for k in
                     ("start_x", "start_y", "end_x", "end_y"))]
    check("66 个坐标值齐全（33 段 × 首末）", not blanks,
          "缺坐标的段：%s" % (blanks or "无"))
    gaps = [(E[i]["seq"], E[i + 1]["seq"]) for i in range(len(E) - 1)
            if abs(E[i]["s1"] - E[i + 1]["s0"]) > 1e-9]
    check("里程首尾相接（段间无缺口）", not gaps, "缺口：%s" % (gaps or "无"))
    check("全长 %.3f m" % TOTAL_M,
          abs(E[0]["s0"]) < 1e-9 and abs(E[-1]["s1"] - TOTAL_M) < 1e-6,
          "实得 %.6f .. %.6f" % (E[0]["s0"], E[-1]["s1"]))
    span = math.hypot(E[-1]["x1"] - E[0]["x0"], E[-1]["y1"] - E[0]["y0"])
    check("坐标不退化（首末点相距 > 1000 m）", span > 1000.0, "实得 %.1f m" % span)
    disc = [E[i]["seq"] for i in range(len(E) - 1)
            if abs(E[i]["az1"] - E[i + 1]["az0"]) > 1e-9]
    check("相邻段在边界的方位角连续", not disc, "不连续的段：%s" % (disc or "无"))
    print("    ★ 正因方位角连续，【5b】的段边界归属在本数据上不可观测。")

    # ---------- 2. 积分收敛 ----------
    print("\n【2】积分收敛 —— 0.1 m 是不是够细")
    p1 = AL.integrate(E, 0.1)
    p2 = AL.integrate(E, 0.01)
    d = math.hypot(p1[-1][1] - p2[-1][1], p1[-1][2] - p2[-1][2])
    check("0.1 m 与 0.01 m 的终点差 < 0.01 mm", d < TOL_CONV_M,
          "实得 %.6f mm" % (d * 1000))

    # ---------- 3. 闭合（主判据） ----------
    print("\n【3】★ 闭合 —— 积分线配准到数据库坐标之后")
    mx, rms, dchord = closure(E)
    check("残差最大 < 1.0 mm", mx < TOL_M, "实得 %.3f mm" % (mx * 1000))
    print("    均方根 %.3f mm（%d 个对账点）" % (rms * 1000, 2 * len(E)))

    # ---------- 4. 弦长 ----------
    print("\n【4】弦长 —— 形状对不对")
    check("积分总弦长与夹具首末点距离差 < 1 mm", dchord < TOL_M,
          "实得 %.3f mm" % (dchord * 1000))

    # ---------- 5. 有靶子（这条门真的会红吗） ----------
    print("\n【5】★ 有靶子 —— 这条门真的会红吗")
    orig_az = AL.azimuth_at

    def sentinel(E):
        """把 None 半径换成 -1 哨兵（本仓 alignment_element.csv 用的正是它）。"""
        return [dict(e, r0=(-1.0 if e["r0"] is None else e["r0"]),
                     r1=(-1.0 if e["r1"] is None else e["r1"])) for e in E]

    def last_seg(E, u):
        """段边界归**后**段（原实现归前段）—— 边界归属错了。"""
        hit = None
        for el in E:
            if el["s0"] <= u <= el["s1"]:
                hit = el
        if hit is None:
            return math.radians(E[-1]["az1"])
        k0, k1, LL = AL.curvature(hit)
        s = u - hit["s0"]
        return math.radians(hit["az0"]) + k0 * s + (k1 - k0) * s * s / (2 * LL)


    orig_curv = AL.curvature

    def run(Em, st, az=None, curv=None):
        AL.azimuth_at = az or orig_az
        AL.curvature = curv or orig_curv
        try:
            return closure(Em, st)[0]
        finally:
            AL.azimuth_at, AL.curvature = orig_az, orig_curv

    print("  ── 5a 真 bug：必须被判红")
    bugs = [
        ("半径 None 写成 -1 哨兵", lambda: run(sentinel(E), AL.DEFAULT_STEP)),
        ("sin/cos 互换", lambda: run(
            E, AL.DEFAULT_STEP, az=lambda E_, u: math.pi / 2 - orig_az(E_, u))),
        ("曲率符号取反", lambda: run(
            E, AL.DEFAULT_STEP,
            curv=lambda el: (lambda k0, k1, L: (-k0, -k1, L))(*orig_curv(el)))),
        ("步长放大到 50 m", lambda: run(E, 50.0)),
    ]
    for label, fn in bugs:
        mmx = fn()
        check("「%s」被判红" % label, mmx > TOL_M, "残差 %.3f mm" % (mmx * 1000))

    # ★ 这一条**不是**漏网，是等价实现 —— 2026-10-16 立门时以为它是 bug，
    #   实测残差与基线**一模一样**（0.471 mm）。原因在【1】：相邻段在边界的
    #   方位角相同（设计本身连续），所以两侧取值相等，两种归属给出同一个数。
    #   ★ 教训：**改预期可以，改容差不行。** 查清之后，这条从「靶子」改成「事实」。
    print("  ── 5b 等价实现：在本数据上**测不出差**（不是漏网）")
    base, _, _ = closure(E)
    alt = run(E, AL.DEFAULT_STEP, az=last_seg)
    check("段边界归后段与归前段给出同一个残差", abs(alt - base) < 1e-12,
          "%.6f mm vs %.6f mm" % (alt * 1000, base * 1000))

    # ---------- 6. 接线 ----------
    print("\n【6】接线 —— 测的就是出货的那段代码")
    src = (CRG_DIR / "gen_crg.py").read_text(encoding="utf-8")
    check("gen_crg.py 调 alignment.integrate", "AL.integrate(" in src)
    check("gen_crg.py 不再自己定义积分", "def azimuth_at" not in src
          and "def curvature" not in src and "math.cos(am)" not in src)
    check("测的 alignment.py 就在出货目录里",
          (CRG_DIR / "alignment.py").resolve() ==
          pathlib.Path(AL.__file__).resolve())

    print()
    if FAILS:
        print("✗ 不通过 %d 项：%s" % (len(FAILS), "; ".join(FAILS)))
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
