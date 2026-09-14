#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最终尺寸视觉 QA 渲染（QA-only，不是交付件）。

契约要求：导出后必须在**最终物理尺寸**下逐面板目视检查，因为源码校验
无法证明色彩层级、标签避让、图例间距与灰度可读性。

本脚本按 170 mm @300 dpi（＝印刷时的真实像素密度）重渲染三张图，
并切出 ≤13 万像素的抽查块——视觉通道的稳定读取上限约 12.8 万像素，
超过会超时，降采样过度则会自信地编造内容。
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

os.environ.setdefault("MPLCONFIGDIR", "/data/cy/shujuku/.mplcache")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

ROOT = pathlib.Path("/data/cy/shujuku")
QA = ROOT / ".qarenders"
QA.mkdir(exist_ok=True)

spec = importlib.util.spec_from_file_location("gaf", ROOT / "gen_arch_figures.py")
gaf = importlib.util.module_from_spec(spec)
sys.modules["gaf"] = gaf
spec.loader.exec_module(gaf)

MAX_PX = 95_000          # 视觉通道稳定上限（12.8 万会超时，内容越密越早超）


def render():
    out = []
    for build in (gaf.build_fig1, gaf.build_fig2, gaf.build_fig3):
        fig, stem = build()
        p = QA / f"{stem}.print300.png"        # 170mm @300dpi ＝ 印刷真实密度
        fig.savefig(str(p), dpi=300)
        plt.close(fig)
        out.append(p)
        print(f"  印刷尺寸渲染 {p.name}  {Image.open(p).size}")
    return out


def crop(src: pathlib.Path, name: str, box, dpi=150):
    """按图件坐标裁切；dpi 为等效栅格密度（物理尺寸不变，只改像素密度）。"""
    im = Image.open(src)
    W, H = im.size
    WPT = gaf.W_MM / 25.4 * 72
    ar = W / WPT                                # 每 pt 对应像素
    x0, y0, x1, y1 = box
    # 入参用图件坐标（原点左下，与 matplotlib 一致）；
    # PIL 原点在左上，必须翻转 y，否则会取到图的另一端。
    top, bot = H - y1 * ar, H - y0 * ar
    c = im.crop((int(x0 * ar), int(top), int(x1 * ar), int(bot)))
    k = dpi / 300.0
    if k != 1.0:
        c = c.resize((max(1, int(c.width * k)), max(1, int(c.height * k))),
                     Image.LANCZOS)
    if c.width * c.height > MAX_PX:            # 保证不超读取上限
        k = (MAX_PX / (c.width * c.height)) ** 0.5
        c = c.resize((int(c.width * k), int(c.height * k)), Image.LANCZOS)
    dst = QA / f"{name}.png"
    c.save(dst)
    print(f"  {dst.name:34s} {c.size[0]}×{c.size[1]} = {c.width*c.height:,} px")
    return dst


if __name__ == "__main__":
    print("=== 印刷尺寸重渲染 ===")
    imgs = render()
    print("=== 抽查块（≤12.8 万像素）===")
    f1, f2, f3 = imgs
    W = gaf.W_MM / 25.4 * 72
    # 图1：整幅结构总览（缩到能一次看完）+ 图例与首两层原生裁切
    crop(f1, "q1_总览", (0, 0, W, 4.86 * 72), dpi=100)
    crop(f1, "q1_图例与前两层", (0, 4.86 * 72 - 120, W * 0.62, 4.86 * 72 - 30))
    crop(f1, "q1_契约栏", (W * 0.60, 20, W, 4.86 * 72 - 36))
    # 图2：总览 + 表头与首两条泳道
    crop(f2, "q2_总览", (0, 0, W, 4.20 * 72), dpi=100)
    crop(f2, "q2_表头与GE_LO", (0, 4.20 * 72 - 105, W, 4.20 * 72 - 22))
    crop(f2, "q2_底部双闭环", (0, 20, W, 90))
    # 图3：总览 + 服务清单 + 分工表尾
    crop(f3, "q3_总览", (0, 0, W, 4.50 * 72), dpi=100)
    crop(f3, "q3_服务清单", (0, 4.50 * 72 - 118, W, 4.50 * 72 - 30))
    crop(f3, "q3_契约块", (0, 0, W, 92))
