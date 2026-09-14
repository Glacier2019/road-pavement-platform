#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图8 修正：主体 7 域 = GE/SU/RE/LO/WE/TE/DE（FA 交安设施移出至末章扩展）"""
import os
os.environ["PYTHONPATH"] = ""
P = "/data/cy/shujuku/output/图8-平台模块搭建结构图.html"
html = open(P, encoding="utf-8").read()

REPL = [
    # 域条
    ('<text x="300" y="756" fill="#cbd5e1" font-size="8">7 大核心对象域贯穿底座：GE 道路几何 · SU 路面表面 · RE 结构响应 · LO 交通荷载 · FA 交通设施 · WE 环境气象 · TE 试验检测（末章扩展至 12 域全景：+VI/SA/DE/QU/SE）</text>',
     '<text x="300" y="756" fill="#cbd5e1" font-size="8">7 大核心对象域贯穿底座（严格执行路面性能库本体）：GE 道路几何 · SU 路面表面 · RE 结构响应 · LO 交通荷载 · WE 环境气象 · TE 试验检测 · DE 决策输出（末章扩展：+FA 交安设施/VI/SA/QU/SE → 12 域）</text>'),
    # 感知源列：交安设施/RSU 方块标注扩展
    ('<rect x="50" y="452" width="172" height="58" rx="6" fill="#0f172a" stroke="#34d399" stroke-width="1"/><text x="58" y="468" fill="#6ee7b7" font-size="8" font-weight="600">交安设施/RSU</text>',
     '<rect x="50" y="452" width="172" height="58" rx="6" fill="#0f172a" stroke="#475569" stroke-width="0.8"/><text x="58" y="468" fill="#94a3b8" font-size="8" font-weight="600">交安设施/RSU（扩展域）</text>'),
]
cnt = 0
for old, new in REPL:
    if old in html:
        html = html.replace(old, new); cnt += 1
    else:
        print("!! 未匹配:", old[:60])
open(P, "w", encoding="utf-8").write(html)
print("图8 修正:", cnt, "/", len(REPL))