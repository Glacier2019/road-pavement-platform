#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图8 域条与标题：7 大核心对象域主体 → 末章 12 域全景"""
import os
os.environ["PYTHONPATH"] = ""
P = "/data/cy/shujuku/output/图8-平台模块搭建结构图.html"
html = open(P, encoding="utf-8").read()

REPL = [
    ("<title>路面性能数智化平台 · 模块搭建结构图（12 对象域 · 含数据流动）</title>",
     "<title>路面性能数智化平台 · 模块搭建结构图（7 大核心对象域 · 含数据流动）</title>"),
    ("<h1>路面性能数智化平台 · 模块搭建结构图（12 对象域为纲 · 含数据流动）</h1>",
     "<h1>路面性能数智化平台 · 模块搭建结构图（7 大核心对象域为纲 · 含数据流动）</h1>"),
    ("数据组织主线＝12 对象域（统一标准）",
     "数据组织主线＝7 大核心对象域（末章扩展 12 域全景）"),
    ("路面性能数智化平台 ＝ 数据底座 ＋ 底座之上的应用 ＋ 感知源（数据组织主线＝12 对象域）",
     "路面性能数智化平台 ＝ 数据底座 ＋ 底座之上的应用 ＋ 感知源（数据组织主线＝7 大核心对象域）"),
    ('<rect x="290" y="742" width="946" height="20" rx="4" fill="rgba(148,163,184,.10)" stroke="#64748b" stroke-width=".7"/>',
     '<rect x="290" y="742" width="946" height="20" rx="4" fill="rgba(148,163,184,.10)" stroke="#64748b" stroke-width=".7"/>'),
    ('<text x="300" y="756" fill="#cbd5e1" font-size="8">12 对象域贯穿底座：GE 道路几何 · SU 路面表面 · RE 结构响应⭐ · LO 交通荷载⭐ · FA 交通设施 · VI 视野感知 · SA 交通安全⭐ · WE 环境气象 · TE 试验检测⭐ · DE 决策输出 · QU 数据质量 · SE 众包服务⭐</text>',
     '<text x="300" y="756" fill="#cbd5e1" font-size="8">7 大核心对象域贯穿底座：GE 道路几何 · SU 路面表面 · RE 结构响应 · LO 交通荷载 · FA 交通设施 · WE 环境气象 · TE 试验检测（末章扩展至 12 域全景：+VI/SA/DE/QU/SE）</text>'),
    ("平台化总体设计 v2 ｜ 12 对象域统一标准 ｜ 底座=接入/存储/治理/融合/API服务",
     "平台化总体设计 v3 ｜ 7 大核心对象域 → 末章 12 域全景 ｜ 底座=接入/存储/治理/融合/API服务"),
]
cnt = 0
for old, new in REPL:
    if old in html:
        html = html.replace(old, new); cnt += 1
    else:
        print("!! 未匹配:", old[:50])
open(P, "w", encoding="utf-8").write(html)
print("图8 更新完成:", cnt, "/", len(REPL))