#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""最终尺寸视觉 QA 渲染（QA-only，不是交付件）。

契约要求：导出后必须在**最终物理尺寸**下逐面板目视检查，因为源码校验
无法证明色彩层级、标签避让、图例间距与灰度可读性。

================================ 方法 ================================
裁切框**必须由 PDF 文本层的真实包围盒驱动**，不能靠肉眼估坐标。踩过的三个坑：

  1. 坐标系搞反——PIL 原点在左上，图件坐标原点在左下。按图件坐标传给 PIL，
     会把图件的另一端裁出来。
  2. 把文字横切了一半——手工估的 y 落在文字中间，模型只拿到半个字高，
     于是**自信地编造**（实测编成过医学文本、MBTI 对照表）。它编造时的
     语气和读对时一样确定，不会报错。
  3. 关键词子串匹配抓错段落——例如 "红虚线" 在图的副标题里也有，
     并集包围盒横跨整幅图，压到 419×226 px 后必然编造。

所以这里：文本层取包围盒 → 关键字用**完整段落文字**匹配 → 按框裁切。
另外**不靠降采样凑像素预算**，而是把裁切框收小：原生 300 dpi 下 5 pt 字
有约 42 px 高，足够判读；降到 150 dpi 只剩一半，就接近模型可辨下限了。

视觉通道实测边界：原生分辨率小裁切 ≤约 9–12 万像素时可逐字准确读出；
≥7 万像素的密集多行块容易超时；缩略图只能判断版面结构，
**绝不能信它读出的字**。
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

WPT = 170.0 / 25.4 * 72          # 481.9 pt —— 与出图脚本一致的最终宽度
BUDGET = 95_000                  # 视觉通道稳定读取上限（12.8 万会超时）

FIG = {"A": "架构图A-平台分层架构与模块接口",
       "B": "架构图B-七域数据流动",
       "C": "架构图C-落地范围与模块分工"}

spec = importlib.util.spec_from_file_location("gaf", ROOT / "gen_arch_figures.py")
gaf = importlib.util.module_from_spec(spec)
sys.modules["gaf"] = gaf
spec.loader.exec_module(gaf)


def render():
    """按 170 mm @300 dpi 重渲染——即印刷时的真实像素密度。"""
    for build in (gaf.build_fig1, gaf.build_fig2, gaf.build_fig3):
        fig, stem = build()
        p = QA / f"{stem}.print300.png"
        fig.savefig(str(p), dpi=300)
        plt.close(fig)
        print(f"  印刷尺寸渲染 {p.name}  {Image.open(p).size}")


def spans(key: str):
    """读 PDF 文本层，返回 [(文本, bbox)]。bbox 为左上原点，与 PIL 一致。"""
    import pymupdf
    doc = pymupdf.open(ROOT / "output" / f"{FIG[key]}.pdf")
    out = []
    for b in doc[0].get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for s in line["spans"]:
                if s["text"].strip():
                    out.append((s["text"].strip(), s["bbox"]))
    return out


def _save(key, name, box, note=""):
    im = Image.open(QA / f"{FIG[key]}.print300.png")
    ar = im.width / WPT
    x0, y0, x1, y1 = box
    c = im.crop((int(x0 * ar), int(y0 * ar), int(x1 * ar), int(y1 * ar)))
    if c.width * c.height > BUDGET:              # 兜底：只在不必要时才降采样
        s = (BUDGET / (c.width * c.height)) ** 0.5
        c = c.resize((int(c.width * s), int(c.height * s)), Image.LANCZOS)
    c.save(QA / f"{name}.png")
    print(f"  {name:26s} {c.size[0]:>4}×{c.size[1]:<4}={c.width*c.height:>6,}px  {note}")


def crop_overview(key, name, dpi=100):
    """整幅缩略图：只用于判断版面与配色，**不要用它读字**。"""
    im = Image.open(QA / f"{FIG[key]}.print300.png")
    c = im.resize((int(im.width * dpi / 300), int(im.height * dpi / 300)),
                  Image.LANCZOS)
    c.save(QA / f"{name}.png")
    print(f"  {name:26s} {c.size[0]:>4}×{c.size[1]:<4}={c.width*c.height:>6,}px  (仅结构)")


def crop_row(key, name, y_center, margin=2.5):
    """按 y 分带取该行的**全部**文本段（行内多列一次取全），用于表格行。

    必须逐行取：一个 94k 像素的裁切块里塞 12 个文本段时模型会编造
    （实测把分工表读成 Minecraft mod 路线图）；约 8 段以内才可靠。
    """
    hit = [bb for _, bb in spans(key) if y_center - 2.0 <= bb[1] <= y_center + 8.0]
    if not hit:
        print(f"  {name:26s} ✗ y={y_center} 处未匹配到文本")
        return
    _save(key, name,
          (min(b[0] for b in hit) - margin, min(b[1] for b in hit) - margin,
           max(b[2] for b in hit) + margin, max(b[3] for b in hit) + margin),
          f"{len(hit)} 段")


def crop_by_pdf_text(key, name, keys, margin=3.0):
    """按完整段落文字匹配 → 取并集包围盒 → 裁切。**结构上不可能切断文字。**"""
    hit = [bb for t, bb in spans(key) if any(k == t or k in t for k in keys)]
    if not hit:
        print(f"  {name:26s} ✗ 未匹配到文本，检查关键字是否与该段落完全一致")
        return
    _save(key, name,
          (min(b[0] for b in hit) - margin, min(b[1] for b in hit) - margin,
           max(b[2] for b in hit) + margin, max(b[3] for b in hit) + margin),
          f"{len(hit)} 段")


# 待逐项目视复核的区域。要补验某处，把该段落的**完整文字**加进对应清单即可。
TARGETS = [
    # --- 架构图C：分工表逐行（密集块必须逐行，见 crop_row 说明）---
    ("ROW", "c_分工表M4行", 163.2),
    ("ROW", "c_分工表M6行", 182.4),
    ("ROW", "c_分工表M8行", 201.6),
    ("ROW", "c_分工表M10行", 220.8),
    # --- 架构图C：尚未逐个目视的部分 ---
    ("C", "c_服务ingest_api", ["python:3.11-slim（自建）"]),
    ("C", "c_端口列",         [":8010", ":8001"]),
    ("C", "c_契约34",         ["③ DAO 契约（7 域 repository）",
                               "④ M6 OpenAPI（服务契约）"]),
    ("C", "c_分工表M4_M6",    ["M4", "数据治理 · 真数据门", "M5", "融合辨析引擎",
                               "M6", "API 服务与指标语义层"]),
    ("C", "c_分工表M7_M10",   ["M7", "语义中枢 · 接入 Copilot", "M8", "M10",
                               "Agent 执行引擎集成（二期）"]),
    # --- 架构图B：底部注记 ---
    ("B", "c_底部注记",       ["末章扩展域"]),
    # --- 架构图A：契约栏末行 ---
    ("A", "c_契约栏末行",     ["脚本 scaffold/tests/contract/（零依赖可运行）"]),
]


if __name__ == "__main__":
    print("=== 1) 印刷尺寸重渲染 ===")
    render()
    print("=== 2) 整幅缩略图（仅判断版面结构，不要读字）===")
    for k, n in (("A", "overview_A"), ("B", "overview_B"), ("C", "overview_C")):
        crop_overview(k, n)
    print("=== 3) 待逐项目视复核的区域（文本层包围盒驱动）===")
    for row in TARGETS:
        if row[0] == "ROW":
            crop_row("C", row[1], row[2])
        else:
            crop_by_pdf_text(row[0], row[1], row[2])
    print("\n复核方式：用视觉通道逐块读出，确认文字完整、可读、无重叠。")
