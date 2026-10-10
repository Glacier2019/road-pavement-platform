#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把"靠右行驶"这件事画成可核对的图。

三块内容：
  (a) 二级公路标准横断面 + 车道标线 + 车在右侧车道中心（位置全部来自设计表）
  (b) 三个 --offset 下，黄色中心线像素质心 x 的实测值 —— 右侧车道行驶时，
      中心线必须落在画面中线**左边**
  (c) 加标线 / 不加标线，画面里的黄色像素数

数据来源（全部是实测，不是示意）：
  - 横断面宽度：数据库 roadbed_design_point, section_id = 6
  - 黄线像素质心：--duration 10 --video-dt 0.5 --video-size 1280x720 的渲染帧
  - 黄像素计数：同上，--no-markings 作对照

用法：
  python3 plot_lane_position.py -o lane_position_plan.png
"""

import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import FancyArrowPatch, Polygon, Rectangle

# ★ 只装了 Noto CJK；不设这个会出「Glyph 8722 missing」方框
for _f in ("Noto Sans CJK JP", "Noto Sans CJK SC", "Noto Serif CJK JP"):
    try:
        matplotlib.font_manager.findfont(_f, fallback_to_default=False)
        plt.rcParams["font.family"] = _f
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

# ----------------------------------------------------------------- 设计数据
# roadbed_design_point, section_id = 6（332 个断面全是这一组值）
SHOULDER = 0.75          # 硬路肩
LANE = 3.50              # 行车道
TOTAL_HALF = SHOULDER + LANE   # 4.25
MARK_W = 0.15            # 标线宽
VEH_OFFSET = -1.75       # 默认行驶位置 = 右侧车道中心

# ------------------------------------------- 实测：近场黄线落在左半边的比例
# ★ 为什么不用质心：黄像素只有一百多个的帧（虚线空隙），质心恒在画面中央
#   附近，那个数字没有含义。改成"近场黄像素落在 x<640 的比例"，
#   并以"近场黄像素 >= 500"作为可判门槛 —— 统计量就不再随稀疏噪声漂移。
# 数据：近场 = 画面下 1/3；1280x720。
LEFTFRAC = [
    (-1.75, "靠右\n（默认）", 0.9753, 19),
    (0.00, "压中线", 0.4560, 19),
    (+1.75, "靠左", 0.0033, 19),
]
FRAME_CX = 0.5           # 比例判据的中线

# --------------------------------------------------- 实测：黄像素计数
YELLOW_PX = {
    "加标线": [5270, 4449, 4496],
    "不加标线\n(--no-markings)": [1324, 844, 866],
}


def panel_cross_section(ax):
    """(a) 标准横断面 + 标线 + 车。"""
    ax.set_title("(a) 二级公路标准横断面（设计表 roadbed_design_point，section_id 6）",
                 fontsize=11, loc="left", pad=10)

    # 路面：硬路肩 + 行车道（两侧对称）
    ax.add_patch(Rectangle((-TOTAL_HALF, 0), SHOULDER, 1, fc="#b9b3a8", ec="#7a756c", lw=0.8))
    ax.add_patch(Rectangle((-(SHOULDER + LANE), 0), LANE, 1, fc="#d9d5cc", ec="#7a756c", lw=0.8))
    ax.add_patch(Rectangle((0, 0), LANE, 1, fc="#d9d5cc", ec="#7a756c", lw=0.8))
    ax.add_patch(Rectangle((LANE, 0), SHOULDER, 1, fc="#b9b3a8", ec="#7a756c", lw=0.8))

    # 标线：黄虚线中线 v=0；白实线车道边缘 v=±3.50
    for x0 in np.arange(-0.36, 0.40, 0.24):        # 虚线：画 0.15 / 空 0.09（示意节距）
        ax.add_patch(Rectangle((x0 - MARK_W / 2, 0.02), MARK_W, 0.96,
                               fc="#f2cc19", ec="none", zorder=3))
    for v in (-LANE, LANE):
        ax.add_patch(Rectangle((v - MARK_W / 2, 0.02), MARK_W, 0.96,
                               fc="#f2f2f2", ec="#9a9a9a", lw=0.4, zorder=3))

    # 车：右车道中心
    ax.add_patch(Rectangle((VEH_OFFSET - 0.92, 0.18), 1.84, 0.64,
                           fc="#2f6fbf", ec="#1b4a86", lw=1.2, zorder=4))
    ax.text(VEH_OFFSET, 0.50, "车", ha="center", va="center",
            color="white", fontsize=9, zorder=5)

    # 尺寸标注
    def dim(x0, x1, y, txt):
        ax.annotate("", xy=(x0, y), xytext=(x1, y),
                    arrowprops=dict(arrowstyle="<->", lw=0.8, color="#444"))
        ax.text((x0 + x1) / 2, y + 0.06, txt, ha="center", va="bottom", fontsize=8)

    dim(-TOTAL_HALF, -LANE, 1.30, "硬路肩 0.75")
    dim(-LANE, 0, 1.30, "左车道 3.50")
    dim(0, LANE, 1.30, "右车道 3.50")
    dim(LANE, TOTAL_HALF, 1.30, "硬路肩 0.75")
    dim(VEH_OFFSET, 0, -0.42, "1.75 m")

    ax.plot([0, 0], [-0.30, 0.0], ls=":", lw=0.9, color="#666")
    ax.text(0, -0.62, "路中线 v = 0\n（黄虚线）", ha="center", va="top", fontsize=8, color="#8a6d00")
    for v, lab in ((-LANE, "白实线 v = -3.50"), (LANE, "白实线 v = +3.50")):
        ax.plot([v, v], [1.15, 1.34], ls=":", lw=0.8, color="#666")
    ax.text(TOTAL_HALF + 0.12, 0.5, "白实线\nv = ±3.50", ha="left", va="center", fontsize=8, color="#555")

    ax.annotate("", xy=(-4.9, 0.5), xytext=(-5.6, 0.5),
                arrowprops=dict(arrowstyle="->", lw=1.1, color="#c0392b"))
    ax.text(-5.0, 0.62, "--offset -1.75\n= 靠右", fontsize=8, color="#c0392b", va="bottom")

    ax.set_xlim(-6.2, 6.0)
    ax.set_ylim(-1.05, 1.75)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.text(0, -1.00, "总宽 3.50+3.50+0.75+0.75 = 8.5 m，与 CRG 的 v 覆盖宽度一致；中央分隔带为 0",
            ha="center", fontsize=8, color="#666")


def panel_centroid(ax):
    """(b) 近场黄线落在画面左半边的比例 vs offset。"""
    ax.set_title("(b) 实测：黄色中心线落在画面左半边的比例（近场，1280 px 宽）",
                 fontsize=11, loc="left", pad=10)

    labels = [c[1] for c in LEFTFRAC]
    vals = [c[2] for c in LEFTFRAC]
    ns = [c[3] for c in LEFTFRAC]
    colors = ["#2e8b57" if c[0] < 0 else ("#c0392b" if c[0] > 0 else "#b8860b")
              for c in LEFTFRAC]

    bars = ax.barh(range(len(vals)), vals, color=colors, height=0.5)
    ax.axvline(FRAME_CX, color="#333", ls="--", lw=1.2)
    ax.text(FRAME_CX + 0.015, len(vals) - 0.42, "0.5", fontsize=8.5, color="#333")

    for i, (v, c) in enumerate(zip(vals, LEFTFRAC)):
        if c[0] < 0:
            side = "黄线在车左 ⇒ 车在右（对）"
        elif c[0] > 0:
            side = "黄线在车右 ⇒ 车在左（错）"
        else:
            side = "黄线几乎均分 ⇒ 压线"
        ax.text(v + (0.02 if v < 0.6 else -0.02), i,
                f"{v:.4f}  {side}",
                va="center", ha="left" if v < 0.6 else "right", fontsize=8.5)

    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlabel("近场黄像素落在 x < 640 的比例", fontsize=9)
    ax.set_xlim(0, 1.24)
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.25, lw=0.6)
    ax.text(0.01, -0.34,
            "每个配置 19 帧全部可判（近场黄像素 ≥ 500）。\n"
            "三种 offset 的比例 0.98 / 0.46 / 0.00 单调且方向明确\n"
            "⇒ 同时证实了「正 = 左」的约定和默认车道位置。",
            transform=ax.transAxes, fontsize=8, color="#555", va="top")


def panel_pixels(ax):
    """(c) 加/不加标线的黄像素数。"""
    ax.set_title("(c) 实测：画面里的黄色像素数（验证标线确实渲染出来了）",
                 fontsize=11, loc="left", pad=10)

    names = list(YELLOW_PX.keys())
    vals = [np.mean(YELLOW_PX[k]) for k in names]
    raw = [YELLOW_PX[k] for k in names]
    colors = ["#f2cc19", "#c9c4ba"]
    bars = ax.bar(names, vals, color=colors, ec="#666", lw=0.8, width=0.55)

    for b, v, r in zip(bars, vals, raw):
        ax.text(b.get_x() + b.get_width() / 2, v + 90,
                f"{v:.0f}\n({', '.join(str(x) for x in r)})",
                ha="center", fontsize=8.5)

    ratio = vals[0] / vals[1]
    ax.set_ylabel("黄色像素数（三帧均值）", fontsize=9)
    ax.set_ylim(0, max(vals) * 1.42)
    ax.grid(axis="y", alpha=0.25, lw=0.6)

    ax.text(0.5, 0.80, f"×{ratio:.1f}", transform=ax.transAxes,
            ha="center", fontsize=22, color="#8a6d00", fontweight="bold")
    ax.text(0.5, 0.70, "加标线 / 不加标线", transform=ax.transAxes,
            ha="center", fontsize=9, color="#555")
    ax.text(0.02, -0.20,
            "「不加标线」并非 0 —— 混凝土贴图自带暖色调，有约 1000 个像素\n"
            "落进黄色判据内。所以这里的读法是**比值**，不是绝对值。",
            transform=ax.transAxes, fontsize=8, color="#555", va="top")


def main():
    ap = argparse.ArgumentParser(description="车道位置与标线的实测证据图")
    ap.add_argument("-o", "--out", default="lane_position_plan.png")
    args = ap.parse_args()

    fig = plt.figure(figsize=(13.5, 9.2))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.0, 1.05],
                          hspace=0.42, wspace=0.24,
                          left=0.06, right=0.97, top=0.90, bottom=0.09)

    panel_cross_section(fig.add_subplot(gs[0, :]))
    panel_centroid(fig.add_subplot(gs[1, 0]))
    panel_pixels(fig.add_subplot(gs[1, 1]))

    fig.suptitle("靠右行驶：从设计横断面到渲染像素的完整核对链",
                 fontsize=14, x=0.06, ha="left", y=0.965)
    fig.text(0.06, 0.925,
             "路面近乎无横坡（实测路拱高差 0.0002 mm）；标线 z 取地形实测值 + 抬 2 cm。",
             fontsize=9, color="#555", ha="left")

    fig.savefig(args.out, dpi=130)
    print(f"已写出 {args.out}  ({os.path.getsize(args.out)} B)")


if __name__ == "__main__":
    main()
