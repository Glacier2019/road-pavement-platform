#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轮荷时间序列可视化

读 05_vehicle_on_crg --csv 导出的轮荷 CSV，出六联图。

用法:
    PYTHONPATH=/data/cy/shujuku/.pylibs python3 plot_wheel_loads.py [csv] [-o 输出.png]

★ 关于「两种零」：CSV 里 Fz=0 有两种含义 —— 轮胎离地（pz 也是 0）与
  载荷恰为零（pz > 0）。本脚本按 (Fz==0 且 pz==0) 判定离地，与 C++ 侧一致。
  画图时离地帧**不能当 0 参与统计**，否则会低估平均轮荷 —— 这正是加 pz 列的原因。
"""
import sys
import csv
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager
from matplotlib.patches import Rectangle

# 中文字体。★ 本机只有 Noto Sans/Serif CJK JP，**没有 SC 变体** —— 写 SC 会
# 落到缺字体的默认族上，中文全变方框（而且 matplotlib 只给一句 warning）。
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for fam in ("Noto Sans CJK JP", "Noto Sans CJK SC", "Noto Serif CJK JP",
            "WenQuanYi Zen Hei", "DejaVu Sans"):
    if fam in _avail:
        plt.rcParams["font.family"] = fam
        break
plt.rcParams["axes.unicode_minus"] = False

# 色盲安全配色（Okabe-Ito）
C_FRONT_L, C_FRONT_R = "#0072B2", "#56B4E9"
C_REAR_L, C_REAR_R = "#D55E00", "#E69F00"
C_TOTAL, C_AIR = "#009E73", "#CC0000"
C_GRID = "#DDDDDD"

TARE_KN = 24.0   # HMMWV 整备重量，用来算动载系数


def load(path):
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    d = {k: [] for k in rows[0]}
    for r in rows:
        for k, v in r.items():
            d[k].append(float(v))
    return d


def main():
    src = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") \
        else "/data/cy/shujuku/.chronotest/veh-run/wheel_loads.csv"
    out = "wheel_loads_plan.png"
    if "-o" in sys.argv:
        out = sys.argv[sys.argv.index("-o") + 1]

    d = load(src)
    s, t = d["s_m"], d["t_s"]
    wheels = [("a0_L", "前轴 左", C_FRONT_L), ("a0_R", "前轴 右", C_FRONT_R),
              ("a1_L", "后轴 左", C_REAR_L), ("a1_R", "后轴 右", C_REAR_R)]
    fz = {k: d[f"{k}_Fz_kN"] for k, _, _ in wheels}
    pz = {k: d[f"{k}_pz_m"] for k, _, _ in wheels}
    n = len(s)

    total = [sum(fz[k][i] for k, _, _ in wheels) for i in range(n)]
    # 离地判定：任一 Fz=0 且同轮 pz=0
    air = [any(fz[k][i] == 0.0 and pz[k][i] == 0.0 for k, _, _ in wheels)
           for i in range(n)]
    air_idx = [i for i in range(n) if air[i]]
    # 每根轴是否离地（用于放大图）
    air_a0 = [fz["a0_L"][i] == 0.0 and pz["a0_L"][i] == 0.0 for i in range(n)]
    air_a1 = [fz["a1_L"][i] == 0.0 and pz["a1_L"][i] == 0.0 for i in range(n)]

    # 统计只用「有效帧」：整车四轮都在
    good = [i for i in range(n) if not air[i]]
    daf = [total[i] / TARE_KN for i in good]

    fig = plt.figure(figsize=(15.5, 13.2))
    gs = fig.add_gridspec(3, 2, hspace=0.34, wspace=0.20,
                          left=0.065, right=0.975, top=0.925, bottom=0.055)

    # ---------------------------------------------------------- (a) 四轮垂向力
    ax = fig.add_subplot(gs[0, :])
    for k, lab, c in wheels:
        ax.plot(s, fz[k], lw=0.55, color=c, label=lab, alpha=0.9)
    ax.axhline(TARE_KN / 4, color="#666666", ls=":", lw=1.2)
    ax.text(120, TARE_KN / 4 + 0.45, f"整备均分 {TARE_KN/4:.1f} kN/轮",
            fontsize=9.5, color="#444444")
    for i in air_idx:
        ax.axvspan(s[i] - 3, s[i] + 3, color=C_AIR, alpha=0.30, lw=0)
    ax.set_xlabel("里程 s (m)", fontsize=11)
    ax.set_ylabel("垂向力 (kN)", fontsize=11)
    ax.set_title("(a) 四轮垂向力随里程 —— 红带 = 有轮子离地", fontsize=12.5,
                 fontweight="bold", loc="left")
    ax.legend(ncol=4, fontsize=9.5, loc="upper right", framealpha=0.94)
    ax.grid(color=C_GRID, lw=0.6)
    ax.set_xlim(0, s[-1])
    ax.set_ylim(-1, 21)

    # ---------------------------------------------------------- (b) 合计 + 离地
    ax = fig.add_subplot(gs[1, 0])
    ax.plot(s, total, lw=0.7, color=C_TOTAL)
    ax.axhline(TARE_KN, color="#666666", ls=":", lw=1.2)
    ax.text(120, TARE_KN + 1.0, f"整备 {TARE_KN:.0f} kN", fontsize=9.5,
            color="#444444")
    ax.scatter([s[i] for i in air_idx], [total[i] for i in air_idx],
               s=26, color=C_AIR, zorder=5, label=f"离地帧 {len(air_idx)} 个")
    # 标注三处离地
    for i in air_idx:
        pass
    ax.set_xlabel("里程 s (m)", fontsize=11)
    ax.set_ylabel("四轮合计垂向 (kN)", fontsize=11)
    ax.set_title(f"(b) 合计垂向力 —— 离地帧 {len(air_idx)}/{n}（{100*len(air_idx)/n:.2f}%）",
                 fontsize=12.5, fontweight="bold", loc="left")
    ax.legend(fontsize=9.5, loc="upper right", framealpha=0.94)
    ax.grid(color=C_GRID, lw=0.6)
    ax.set_xlim(0, s[-1])
    ax.set_ylim(0, 48)

    # ---------------------------------------------------------- (c) 动载系数分布
    ax = fig.add_subplot(gs[1, 1])
    ax.hist(daf, bins=90, color="#0072B2", alpha=0.85, edgecolor="white", lw=0.3)
    ax.axvline(1.0, color="#666666", ls=":", lw=1.4)
    ax.axvline(sum(daf) / len(daf), color=C_AIR, lw=1.8)
    ax.text(sum(daf) / len(daf) + 0.012, ax.get_ylim()[1] * 0.86,
            f"均值 {sum(daf)/len(daf):.3f}", fontsize=10, color=C_AIR,
            fontweight="bold")
    ax.text(1.005, ax.get_ylim()[1] * 0.94, "整备 1.000", fontsize=9.5,
            color="#444444")
    ax.set_xlabel("动载系数 DAF = 合计垂向 / 整备", fontsize=11)
    ax.set_ylabel("样本数", fontsize=11)
    ax.set_title(f"(c) 动载系数分布（{len(good)} 个有效帧，最大 {max(daf):.3f}）",
                 fontsize=12.5, fontweight="bold", loc="left")
    ax.grid(color=C_GRID, lw=0.6, axis="y")
    ax.set_xlim(0.2, max(daf) * 1.06)

    # ---------------------------------------------------------- (d) 离地事件放大
    ax = fig.add_subplot(gs[2, 0])
    lo, hi = 213.9, 215.1
    sel = [i for i in range(n) if lo <= t[i] <= hi]
    ax.plot([t[i] for i in sel], [fz["a0_L"][i] + fz["a0_R"][i] for i in sel],
            lw=2.0, color=C_FRONT_L, marker="o", ms=3.2, label="前轴合计")
    ax.plot([t[i] for i in sel], [fz["a1_L"][i] + fz["a1_R"][i] for i in sel],
            lw=2.0, color=C_REAR_L, marker="s", ms=3.2, label="后轴合计")
    ax.plot([t[i] for i in sel], [total[i] for i in sel],
            lw=1.4, color=C_TOTAL, ls="--", label="整车合计")
    ax.axhline(TARE_KN / 2, color="#666666", ls=":", lw=1.1)
    ax.text(lo + 0.03, TARE_KN / 2 + 0.6, "整备均分 12 kN/轴", fontsize=9,
            color="#444444")
    ax.set_xlabel("时间 t (s)", fontsize=11)
    ax.set_ylabel("轴垂向力 (kN)", fontsize=11)
    ax.set_title("(d) 离地事件放大 t≈214.4s —— 前轴先离地，0.25s 后后轴离地",
                 fontsize=12.5, fontweight="bold", loc="left")
    ax.legend(fontsize=9.5, loc="upper right", framealpha=0.94)
    ax.grid(color=C_GRID, lw=0.6)

    # ---------------------------------------------------------- (e) 路面高程
    ax = fig.add_subplot(gs[2, 1])
    for k, lab, c in wheels:
        v = [pz[k][i] if pz[k][i] > 0 else None for i in range(n)]
        ax.plot(s, v, lw=0.6, color=c, label=lab, alpha=0.9)
    ax.set_xlabel("里程 s (m)", fontsize=11)
    ax.set_ylabel("着地点高程 (m)", fontsize=11)
    ax.set_title("(e) 轮下路面高程 —— 离地处断开（不是 0，是没有接触）",
                 fontsize=12.5, fontweight="bold", loc="left")
    ax.legend(ncol=4, fontsize=9, loc="upper right", framealpha=0.94)
    ax.grid(color=C_GRID, lw=0.6)
    ax.set_xlim(0, s[-1])

    # ---------------------------------------------------------- 总标题
    fig.text(0.065, 0.975, "WIM 断面轮荷时间序列", fontsize=17,
             fontweight="bold")
    fig.text(0.065, 0.949,
             f"源：{src.split('/')[-1]}    {n} 行 × 0.05 s    "
             f"里程 {s[0]:.1f}~{s[-1]:.1f} m    整车整备 {TARE_KN:.0f} kN    "
             f"均值 DAF {sum(daf)/len(daf):.3f}    峰值 DAF {max(daf):.3f}",
             fontsize=10.5, color="#444444")

    fig.savefig(out, dpi=110, facecolor="white")
    print(f"已写出 {out}")

    # 控制台摘要（我自己看不了图，只能靠这些数字自检）
    print(f"  行数 {n}  离地帧 {len(air_idx)}")
    print(f"  合计垂向  最小 {min(total):.2f}  最大 {max(total):.2f}  均值 {sum(total)/n:.2f} kN")
    print(f"  有效帧 DAF  均值 {sum(daf)/len(daf):.3f}  最大 {max(daf):.3f}")
    print(f"  高程范围  {min(v for k,_,_ in wheels for v in pz[k] if v>0):.2f} ~ "
          f"{max(v for k,_,_ in wheels for v in pz[k]):.2f} m")


if __name__ == "__main__":
    main()
