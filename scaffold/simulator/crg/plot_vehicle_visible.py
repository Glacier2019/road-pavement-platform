#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plot_vehicle_visible.py —— 证明"车确实画进画面了"的受控对比图

三块：
  (a) 开车辆可视化            这一帧里有车
  (b) 不开车辆可视化（其他全同） 同一时刻的同一视角，只有路
  (c) (a)-(b) 的逐像素差异     差异落在哪，车就在哪

以及一行对照：同一份二进制跑两次，(c) 这一栏几乎是空的 ——
用来排除"两次跑本来就不一样"这个解释。

用法:
    PYTHONPATH=/data/cy/shujuku/.pylibs python3 plot_vehicle_visible.py \
        --with-car A.png --with-car-2 B.png --without-car C.png \
        -o vehicle_visible_plan.png
"""
import argparse
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib import font_manager

for _f in ("Noto Sans CJK JP", "Noto Serif CJK JP"):
    try:
        font_manager.findfont(_f, fallback_to_default=False)
        plt.rcParams["font.sans-serif"] = [_f]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False


def load(p):
    im = mpimg.imread(p)
    return im[:, :, :3]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-car", required=True, help="开了车辆可视化的帧")
    ap.add_argument("--with-car-2", required=True, help="同一二进制再跑一次的同一帧（对照组）")
    ap.add_argument("--without-car", required=True, help="没开车辆可视化的帧")
    ap.add_argument("-o", "--out", default="vehicle_visible_plan.png")
    a = ap.parse_args()

    frame_with = load(a.with_car)
    frame_ctrl = load(a.with_car_2)
    frame_without = load(a.without_car)
    h, w = frame_with.shape[:2]

    d_car = np.abs(frame_with - frame_without).max(axis=2)
    d_ctl = np.abs(frame_with - frame_ctrl).max(axis=2)

    fig = plt.figure(figsize=(18, 7.2))
    gs = fig.add_gridspec(2, 3, height_ratios=[1, 1], hspace=0.16, wspace=0.05)

    for col, (img, title) in enumerate([
        (frame_with, "(a) 开车辆可视化 —— 车在这里"),
        (frame_without, "(b) 不开车辆可视化 —— 同一时刻、同一视角"),
    ]):
        ax = fig.add_subplot(gs[0, col])
        ax.imshow(img)
        ax.set_title(title, fontsize=13, pad=8)
        ax.set_xticks([]); ax.set_yticks([])

    for col, (d, title) in enumerate([
        (d_car, "(c) (a)−(b) 差异 —— 亮的地方就是车"),
        (d_ctl, "(d) 对照：同一二进制跑两次的差异"),
    ]):
        ax = fig.add_subplot(gs[1, col])
        im = ax.imshow(d, cmap="inferno", vmin=0, vmax=1)
        ax.set_title(title, fontsize=13, pad=8)
        ax.set_xticks([]); ax.set_yticks([])
        cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
        cb.ax.tick_params(labelsize=9)

    # 右下角：数字汇总
    ax = fig.add_subplot(gs[:, 2])
    ax.axis("off")
    rows = [
        ("画面", f"{w} × {h}"),
        ("", ""),
        ("同一二进制跑两次（对照）", ""),
        ("　差异均值", f"{d_ctl.mean():.5f}"),
        ("　显著差异像素 (>0.1)", f"{100 * (d_ctl > 0.1).mean():.2f} %"),
        ("", ""),
        ("开车辆可视化 vs 不开", ""),
        ("　差异均值", f"{d_car.mean():.5f}"),
        ("　显著差异像素 (>0.1)", f"{100 * (d_car > 0.1).mean():.2f} %"),
        ("", ""),
        ("倍数", f"{d_car.mean() / max(d_ctl.mean(), 1e-9):.0f} ×"),
    ]
    y = 0.94
    for k, v in rows:
        if not k:
            y -= 0.035
            continue
        bold = not k.startswith("　")
        ax.text(0.02, y, k, fontsize=12, transform=ax.transAxes,
                fontweight="bold" if bold else "normal", va="top")
        ax.text(0.98, y, v, fontsize=12, transform=ax.transAxes,
                ha="right", va="top",
                family="monospace", fontweight="bold" if bold else "normal")
        y -= 0.058
    ax.text(0.02, y - 0.04,
            "对照组几乎为零 ⇒ (c) 里那一片亮斑\n只能来自「车被画出来了」，\n"
            "不是两次运行本身的抖动。\n\n"
            "顺带得到一个有用的结论：\n这个仿真是逐帧可复现的。",
            fontsize=11, transform=ax.transAxes, va="top",
            linespacing=1.6,
            bbox=dict(boxstyle="round,pad=0.6", fc="#FFF8E1", ec="#FFC107"))

    fig.suptitle("车一直没画出来 —— 根因是 Chrono 9 把 VisualizationType 默认改成了 NONE",
                 fontsize=16, y=0.985)
    fig.savefig(a.out, dpi=110, bbox_inches="tight", facecolor="white")
    print(f"已输出 {a.out}")
    print(f"  对照（两次跑）  差异均值 {d_ctl.mean():.5f}  显著 {100 * (d_ctl > 0.1).mean():.2f}%")
    print(f"  有车 vs 无车    差异均值 {d_car.mean():.5f}  显著 {100 * (d_car > 0.1).mean():.2f}%")


if __name__ == "__main__":
    main()
