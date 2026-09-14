#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提取 SVG + inkscape 转 PNG（图6-7核心域 与 图8）"""
import re, subprocess, os
os.environ["PYTHONPATH"] = ""
OUT = "/data/cy/shujuku/output"
for fn in ["图6-路面性能库ER-7核心域", "图8-平台模块搭建结构图", "图9-语义中枢引擎集成与涵盖范围", "图10-开发模块拆分与依赖"]:
    html = open(f"{OUT}/{fn}.html", encoding="utf-8").read()
    m = re.search(r"<svg\b[^>]*>.*?</svg>", html, re.DOTALL)
    svg = m.group(0)
    if not svg.startswith("<?xml"):
        svg = '<?xml version="1.0" encoding="UTF-8"?>\n' + svg
    open(f"{OUT}/{fn}.svg", "w", encoding="utf-8").write(svg)
    r = subprocess.run(
        f'inkscape "{OUT}/{fn}.svg" --export-type=png --export-filename="{OUT}/{fn}.png" --export-width=2200',
        shell=True, capture_output=True, text=True, timeout=180)
    print(fn, "svg+png:", "OK" if r.returncode == 0 else r.stderr[-300:],
          os.path.getsize(f"{OUT}/{fn}.png") if os.path.exists(f"{OUT}/{fn}.png") else 0)