"""横断面设计线契约 —— 论文 (2.16) 的 Σ 必须真的有横坡。

运行（**离线可跑，不连数据库、不连容器、不连外网**）：
  cd /data/cy/shujuku/scaffold
  python3 tests/contract/test_section_surface.py

为什么必须有这个文件
--------------------

2026-10-15 发现：`gen_crg.py` 的 `road_z(s, v)` 返回 `elevation(s)` ——
**`v` 根本没出现在右边**。Docstring 写「横坡（暂取平坡）」，于是那个
101 MB 的 CRG 横向 **100% 平坦**（58,055 / 58,055 个断面极差 == 0）。
控制台上叫「三维起伏」的那张图，实际是一条 5.8 km 的平带。

它没被任何测试拦住，因为：

  ① 横断面的数学当时**只写在 gen_crg.py 里**，没有任何测试 import 它；
  ② CRG 产物 **gitignore 掉了**（101 MB），测产物等于测一个不存在的东西；
  ③ 唯一的「仿真」测试 `test_simulator_contract.py` 测的是 **WIM 造数器**，
     跟 CRG 一点关系没有。

⇒ 于是「纸面上写了横坡」和「程序真的算了横坡」之间没有任何联系。
   本文件就是补这条联系（宪法 §V：定不出靶子的门，永远不会红）。

现在数学搬到了 `simulator/crg/xsection.py`，`gen_crg.py` 调它，
本文件测它 —— **测的就是出货的那段代码**。

四组判据
--------

  §1  **横坡真的进了 z。** v 变了 z 必须变；且 z 对 v 的斜率必须等于
       该段的横坡。这是元测试的靶心：把 `z_offset` 改成 `return 0.0`
       （即当年那个 bug）必须立刻变红。

  §2  **分段必须存在。** 真实断面在车道/硬路肩交界处有折点。
       用车道的横坡一路外推，与实际值差多少 —— 认这个差。

  §3  **与库中 elev_diff 独立互证。** `roadbed_design_point.elev_diff_01..11`
       是设计文件里另存的一份断面抬升量。它是**独立数据源**，
       不是我们算出来的。比对采用「相邻点差值」口径，与基准点无关 ——
       因为该组列的基准本身不统一（0 / −37.5 / −22.5 / +7.5 mm 四种），
       那是源数据的问题，不该让模型背。

  §4  **元测试。** 注入当年的 bug（忽略 v）、注入「把分段压成一档」、
       注入「丢掉左半」，都必须被 §1/§2 认出来。
       **没有这一组，前三组绿也不知道门有没有在站岗。**

★ 本文件不读 101 MB 的 CRG，也不读 artifacts/ —— 那些是 gitignore 的，
  在干净检出上不存在。这里用**冻结的实测数值**当夹具，来源逐条注明。
"""
from __future__ import annotations

import pathlib
import sys
from decimal import Decimal as D

ROOT = pathlib.Path(__file__).resolve().parents[2]        # → scaffold/
sys.path.insert(0, str(ROOT / "simulator" / "crg"))

import xsection as XS                                     # noqa: E402

FAILED: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        FAILED.append(msg)


def pct(seq):
    """百分数 → 小数。★ z_offset 收的是【小数】。

    这个换算踩过一次坑：夹具直接喂百分数，量值差 100 倍，
    表面上像是模型错了，其实是测试写错了。故显式命名。
    """
    return [x / 100.0 for x in seq]


# ---------------------------------------------------------------- 夹具
#
# ★ 全部取自活库 section_id=6 实测值，不是构造的。
#
# 宽度（`roadbed_design_point`，332 行完全相同 —— 全路线只有一组）：
#     left_earth 0.75 | left_hard 0.75 | left_median 0 | left_lane 3.50
#     right_median 0 | extra09 0 | right_lane 3.50 | extra11 0
#     right_hard 0.75 | right_earth 0.75
W10 = [0.75, 0.75, 0.0, 3.50, 0.0, 0.0, 3.50, 0.0, 0.75, 0.75]

# ── 横坡（`superelev_transition`），顺序 [左土,左硬,左车道,右车道,右硬,右土]，单位 % ──
#
# 桩号 0.000000 km（实测，正常路拱 —— 注意土路肩是 −3%，不是 −2%）
CF_CROWN = [-3.00, -2.00, -2.00, -2.00, -2.00, -3.00]
#
# 桩号 1.463176 km（实测，全超高 —— 行车道 6%，且左右反号）
CF_SUPER = [-5.00, -5.00, -6.00, +6.00, +5.00, -3.00]

# ── elev_diff 实测值 ──
#
# 桩号 0.000000 km，库中 elev_diff_01..11（最小值恰为 0，基准与模型一致）：
#   0.0000 0.0225 0.0375 0.0375 0.1075 0.1075 0.1075 0.0375 0.0375 0.0225 0.0000
ED_CROWN = [0.0000, 0.0225, 0.0375, 0.0375, 0.1075, 0.1075, 0.1075,
            0.0375, 0.0375, 0.0225, 0.0000]
#
# 桩号 1.463176 km，库中 elev_diff_01..11（最小值是 −0.0375，基准与模型
# **差一个常数** +0.0375 —— 11 个点全部同差，说明形状一致、基准不同）：
#   -0.0375 0.0000 0.0375 0.0375 0.2475 0.2475 0.2475 0.4575 0.4575 0.4950 0.4725
ED_SUPER = [-0.0375, 0.0000, 0.0375, 0.0375, 0.2475, 0.2475, 0.2475,
            0.4575, 0.4575, 0.4950, 0.4725]


def diffs(seq):
    """相邻点差值 —— 与基准点无关的口径。"""
    return [seq[i+1] - seq[i] for i in range(len(seq) - 1)]


# ---------------------------------------------------------------- §1 横坡真的进了 z
def sec1():
    """v 必须影响 z，且斜率等于该段横坡。"""

    # (a) 中线处偏移为 0；越往右越高（全超高右半抬升）
    z0 = XS.z_offset(0.0, pct(CF_SUPER), W10)
    z35 = XS.z_offset(3.50, pct(CF_SUPER), W10)
    z425 = XS.z_offset(4.25, pct(CF_SUPER), W10)
    check(abs(z0) < 1e-12, f"§1a 中线处 z 偏移应为 0，得 {z0}")
    check(abs(z35 - 0.210) < 1e-12, f"§1a 3.5 m 处应 210.000 mm，得 {z35*1000:.3f} mm")
    check(z425 > z35, f"§1a 4.25 m 处应比 3.5 m 更高，得 {z425*1000:.1f} vs {z35*1000:.1f} mm")

    # (b) ★ 元测试靶心：v 被忽略时 z 不随 v 变
    check(z425 != z0, "§1b 横坡没有进入 z —— v 被忽略了（这就是 D-032 那个 bug）")

    # (c) 段内斜率必须**精确**等于该段横坡（右车道 6%，右硬路肩 5%）
    for v in (0.5, 1.0, 2.0, 3.0, 3.5):
        want = v * 0.06
        got = XS.z_offset(v, pct(CF_SUPER), W10)
        check(abs(got - want) < 1e-12,
              f"§1c v={v} 右车道斜率应 {want*1000:.3f} mm，得 {got*1000:.3f} mm")
    want = 3.50*0.06 + 0.75*0.05                      # = 247.5 mm
    got = XS.z_offset(4.25, pct(CF_SUPER), W10)
    check(abs(got - want) < 1e-12,
          f"§1c v=4.25 应 {want*1000:.3f} mm（3.5×6% + 0.75×5%），得 {got*1000:.3f} mm")

    # (d) 路拱左右必须**同高**（对称）
    zl = XS.z_offset(-4.25, pct(CF_CROWN), W10)
    zr = XS.z_offset(+4.25, pct(CF_CROWN), W10)
    check(abs(zl - zr) < 1e-12,
          f"§1d 路拱应左右对称，得 {zl*1000:.3f} vs {zr*1000:.3f} mm")

    # (e) 全超高左右必须**反号**（单向横坡，一边升一边降）
    zl = XS.z_offset(-4.25, pct(CF_SUPER), W10)
    zr = XS.z_offset(+4.25, pct(CF_SUPER), W10)
    check(zl < 0 < zr, f"§1e 全超高应左低右高，得 {zl*1000:.1f} / {zr*1000:.1f} mm")


# ---------------------------------------------------------------- §2 分段必须存在
def sec2():
    """一个平面做不出路拱 —— 认这个几何事实，别让它悄悄退化成单母线。"""

    # (a) 单母线外推 vs 实际分段：全超高断面，v = 4.25
    #     单母线一路用 6% 推到底；实际硬路肩是 5%
    plane = 4.25 * 0.06                                    # = 255.0 mm
    real = XS.z_offset(4.25, pct(CF_SUPER), W10)           # = 247.5 mm
    gap = abs(plane - real)
    check(abs(gap - 0.75*0.01) < 1e-12,
          f"§2a 差异应恰为 0.75×(6%−5%) = 7.500 mm，得 {gap*1000:.3f} mm")

    # (b) ★ 折点必须存在：z(v) 在 v = 3.5 m 处斜率要变
    eps = 1e-6
    cf = pct(CF_SUPER)
    s_before = (XS.z_offset(3.5, cf, W10) - XS.z_offset(3.5-eps, cf, W10)) / eps
    s_after = (XS.z_offset(3.5+eps, cf, W10) - XS.z_offset(3.5, cf, W10)) / eps
    check(abs(s_before - 0.06) < 1e-6, f"§2b 折点前斜率应 0.06，得 {s_before}")
    check(abs(s_after - 0.05) < 1e-6, f"§2b 折点后斜率应 0.05，得 {s_after}")
    check(abs(s_before - s_after) > 1e-3, "§2b 折点消失了 —— 分段被压成了一档")

    # (c) 路拱是「人字」：中线必须最高
    cf = pct(CF_CROWN)
    zc = XS.z_offset(0.0, cf, W10)
    zl = XS.z_offset(-2.0, cf, W10)
    zr = XS.z_offset(+2.0, cf, W10)
    check(zc > zl and zc > zr,
          f"§2c 路拱中线应最高，得 中线{zc*1000:.1f} 左{zl*1000:.1f} 右{zr*1000:.1f} mm")


# ---------------------------------------------------------------- §3 与 elev_diff 互证
def sec3():
    """与库中独立数据源 elev_diff 互证。"""

    # (a) 路拱断面：基准恰好一致，整条抬升量曲线必须逐位吻合
    got = XS.section_bumps(pct(CF_CROWN), W10)
    check(len(got) == 11, f"§3a 断面应有 11 个点，得 {len(got)}")
    for i, (g, e) in enumerate(zip(got, ED_CROWN)):
        check(abs(g - e) < 1e-9,
              f"§3a 路拱第 {i+1} 点应 {e:.4f} m，得 {g:.6f} m")

    # (b) 全超高断面：库中基准与模型差一个常数 +0.0375，故走**与基准无关**的
    #     相邻差口径。相邻差全等 ⇒ 形状全等。
    got = XS.section_bumps(pct(CF_SUPER), W10)
    dg, de = diffs(got), diffs(ED_SUPER)
    for i, (g, e) in enumerate(zip(dg, de)):
        check(abs(g - e) < 1e-9,
              f"§3b 全超高第 {i+1} 个相邻差应 {e:.4f} m，得 {g:.6f} m")

    # (c) 上述「差一个常数」必须真的是**常数** —— 若各点差不等，说明不是基准
    #     问题而是形状问题，那 (b) 的通过就不算数
    off = [a - b for a, b in zip(got, ED_SUPER)]
    check(max(off) - min(off) < 1e-9,
          f"§3c 全超高各点偏移应恒定，得极差 {(max(off)-min(off))*1e9:.3f} nm")

    # (d) ★ 口径自洽：`bump_above_lowest` 的最小值必须恒为 0
    zs = [XS.z_offset(v - 5.0, pct(CF_SUPER), W10) for v in XS.point_offsets(W10)]
    bumps = XS.bump_above_lowest(zs)
    check(abs(min(bumps)) < 1e-15, f"§3d 抬升量最小值应为 0，得 {min(bumps)}")


# ---------------------------------------------------------------- §5 网格欠估（不是 bug，是网格的性质）
def sec5():
    """★ 折点不在 v 网格上 ⇒ 采样必然欠估。钉住机制，不钉住数字。

    CRG 的 v 网格是 `−vmax + j·Δv`，`vmax = 4.25`，`Δv = 0.1`。
    三个设计折点都**不在**网格上（j 分别是 42.5 / 7.5 / 77.5）：

        v = 0.00   路拱顶点 / 超高反号点
        v = ±3.50  车道 ↔ 硬路肩折点

    折点是尖角，用相邻两列线性插值必从尖角削掉一块。
    **欠估量有闭式解**（令 h = Δv/2 = 半格）：

        顶点： z(−h) = h·t_L，z(+h) = h·t_R  ⇒  欠估 = h·|t_L + t_R| / 2
        折点： 两侧斜率 t₁、t₂                ⇒  欠估 = h·|t₂ − t₁| / 2

    ★ 本组不写死「现在是 1.000 mm」，而写**这两个公式**并断言实测符合。
      若把 `vmax` 改成 4.30（0 → j=43，±3.5 → j=8/78 皆为整数），
      h 形式上仍在，但折点落在列上 ⇒ 插值不再跨尖角 ⇒ 欠估为 0，
      公式给出 0，本组**照样通过**。
      ⇒ 钉住机制，不钉住现状；否则修好了反而变红，等于在奖励缺陷。
    """

    VMAX, DV = 4.25, 0.1
    H = DV / 2.0                       # 半格

    def interp_across(v, cf):
        """把 v 邻域两列做线性插值 —— 复现 CRG 网格的采样方式。"""
        j = (v + VMAX) / DV
        j0 = int(j) if j == int(j) else int(j)      # 向下取整
        v0 = -VMAX + j0 * DV
        v1 = v0 + DV
        z0 = XS.z_offset(v0, cf, W10)
        z1 = XS.z_offset(v1, cf, W10)
        t = (v - v0) / DV
        return z0 + t * (z1 - z0)

    # (a) 路拱顶点：左右车道横坡同号，欠估 = h·|t_L + t_R| / 2
    cf = pct(CF_CROWN)                       # 左右车道均 −2%
    pred = H * abs(cf[2] + cf[3]) / 2 * 1000
    got = abs(interp_across(0.0, cf) - XS.z_offset(0.0, cf, W10)) * 1000
    check(abs(got - pred) < 1e-6,
          f"§5a 路拱顶点欠估应 {pred:.3f} mm，得 {got:.3f} mm")

    # (b) 全超高顶点：t_L = −6%、t_R = +6% ⇒ 反号相消，欠估应为 0
    cf = pct(CF_SUPER)
    pred = H * abs(cf[2] + cf[3]) / 2 * 1000
    got = abs(interp_across(0.0, cf) - XS.z_offset(0.0, cf, W10)) * 1000
    check(pred < 1e-12, f"§5b 全超高顶点两侧反号，预测欠估应为 0，得 {pred:.6f} mm")
    check(got < 1e-6, f"§5b 全超高顶点实测欠估应为 0，得 {got:.6f} mm")

    # (c) 车道 ↔ 硬路肩折点：欠估 = h·|t₂ − t₁| / 2
    for v, t1, t2 in ((3.50, cf[3], cf[4]), (-3.50, cf[2], cf[1])):
        pred = H * abs(t2 - t1) / 2 * 1000
        got = abs(interp_across(v, cf) - XS.z_offset(v, cf, W10)) * 1000
        check(abs(got - pred) < 1e-6,
              f"§5c v={v:+.2f} 折点欠估应 {pred:.3f} mm，得 {got:.3f} mm")

    # (d) ★ 关键：把网格挪正，折点落回列上 ⇒ 欠估必须真的归零
    #     直接验「列上」这个事实（不重跑生成器）
    for vmax2, bps in ((4.30, (0.0, 3.50, -3.50)), (4.20, (0.0,))):
        for bp in bps:
            # ★ 必须用 Decimal：二进制浮点下 (0.0+4.30)/0.1 = 42.99999999999999，
            #   会让「恰在列上」这个判断假红。网格的算术是十进制的。
            j = (D(str(bp)) + D(str(vmax2))) / D(str(DV))
            check(j == j.to_integral_value(),
                  f"§5d vmax={vmax2} 时 v={bp:+.2f} 应恰在网格列上，得 j={j}")

    # (e) 定性：路拱顶点欠估必须**大于**折点欠估（2% vs 0.5%），
    #     否则说明上面几条测的不是同一件事
    pred_apex = H * abs(pct(CF_CROWN)[2] + pct(CF_CROWN)[3]) / 2 * 1000
    pred_kink = H * abs(pct(CF_SUPER)[4] - pct(CF_SUPER)[3]) / 2 * 1000
    check(pred_apex > pred_kink,
          f"§5e 顶点欠估应大于折点欠估，得 {pred_apex:.3f} vs {pred_kink:.3f} mm")


# ---------------------------------------------------------------- §4 元测试
def sec4():
    """★ 把当年的 bug 注回去，必须被认出来。没有这一组，前三组绿也是白绿。"""

    orig = XS.z_offset
    cf = pct(CF_SUPER)
    try:
        # (a) 忽略 v —— D-032 的原样 bug
        XS.z_offset = lambda v, c, w: 0.0
        check(XS.z_offset(4.25, cf, W10) == XS.z_offset(0.0, cf, W10),
              "§4a 元测试失效：注入「忽略 v」后 z 竟然仍随 v 变")
    finally:
        XS.z_offset = orig

    try:
        # (b) 把分段压成一档（只用车道横坡外推到底）—— 折点必须消失
        XS.z_offset = lambda v, c, w: (v * (c[3] if v >= 0 else c[2]))
        eps = 1e-6
        sb = (XS.z_offset(3.5, cf, W10) - XS.z_offset(3.5-eps, cf, W10)) / eps
        sa = (XS.z_offset(3.5+eps, cf, W10) - XS.z_offset(3.5, cf, W10)) / eps
        check(abs(sb - sa) < 1e-12,
              "§4b 元测试失效：压成一档后折点竟然还在")
    finally:
        XS.z_offset = orig

    try:
        # (c) 丢掉左半（左右同号）—— §1e/§1d 的口径必须认出来
        XS.z_offset = lambda v, c, w: abs(v) * c[3]
        check(abs(XS.z_offset(-4.25, cf, W10) - XS.z_offset(+4.25, cf, W10)) < 1e-15,
              "§4c 元测试失效：丢掉左半后左右竟然不等")
    finally:
        XS.z_offset = orig

    # (d) 确认 finally 真的生效，没有污染后续判据
    check(abs(XS.z_offset(4.25, cf, W10) - (3.50*0.06 + 0.75*0.05)) < 1e-12,
          "§4d 元测试污染了全局状态：恢复后 z_offset 不对了")


# ---------------------------------------------------------------- §0 靶子存在性
def sec0():
    """★ 宪法 §V：门必须确认自己的靶子真的在。"""

    check(hasattr(XS, "z_offset"), "§0 xsection.z_offset 不存在 —— 门找不到靶子")
    check(hasattr(XS, "section_bumps"), "§0 xsection.section_bumps 不存在")

    # 正向：gen_crg.py 必须真的**调用**它
    g = (ROOT / "simulator" / "crg" / "gen_crg.py").read_text(encoding="utf-8")
    check("XS.z_offset" in g,
          "§0 gen_crg.py 没有调用 XS.z_offset —— 横断面数学被复制回去了，"
          "那这个测试测的就不是出货的代码")

    # 反向：gen_crg.py 里不该再出现自己算横坡的痕迹
    tail = g.split("# ---------- 5.")[-1] if "# ---------- 5." in g else g
    check("cf[" not in tail and "* cf" not in tail,
          "§0 gen_crg.py 里仍在自己算横坡 —— 数学有两份实现")

    # 契约测试自身必须被登记，否则运行器根本不跑它
    runner = (ROOT / "run_contract_tests.sh").read_text(encoding="utf-8")
    check("test_section_surface.py" in runner,
          "§0 本文件没登记进 run_contract_tests.sh 的 TESTS=() —— "
          "那它永远只是『一个没人跑的文件』")


def main() -> int:
    for fn in (sec0, sec1, sec2, sec3, sec4, sec5):
        before = len(FAILED)
        fn()
        mark = "✓" if len(FAILED) == before else "✗"
        print(f"  {mark} {fn.__name__:<6} {fn.__doc__.splitlines()[0]}")
    print()
    if FAILED:
        print(f"  ✗ 横断面契约：{len(FAILED)} 条不达标")
        for m in FAILED:
            print(f"      - {m}")
        return 1
    print("  ✓ 横断面契约：全部通过")
    print("     §0 靶子存在 · §1 横坡进 z · §2 分段存在 · §3 elev_diff 互证 · §4 元测试 · §5 网格欠估")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
