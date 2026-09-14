#!/usr/bin/env python3
import re, os

src = "/data/cy/shujuku/output"
files = [
    "图5-完整ER图-对象域版.html",
    "图6-路面性能库ER图.html",
    "图1-整体架构.html",
    "图2-数据流动.html",
    "图3-板块全景与数据依赖.html",
    "图4-对象域划分与依赖矩阵.html",
]

for fn in files:
    path = os.path.join(src, fn)
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    # 修正字体
    html = html.replace(
        "font-family: 'JetBrains Mono', 'Noto Sans SC', monospace",
        "font-family: 'DejaVu Sans Mono', 'Noto Sans SC', 'DejaVu Sans', monospace"
    )
    m = re.search(r"<svg\b[^>]*>.*?</svg>", html, re.DOTALL)
    if not m:
        print(f"⚠️ {fn}: 未找到 SVG")
        continue
    svg = m.group(0)
    if not svg.startswith("<?xml"):
        svg = '<?xml version="1.0" encoding="UTF-8"?>\n' + svg
    out_fn = fn.replace(".html", ".svg")
    out_path = os.path.join(src, out_fn)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"✅ {out_fn}")

print("\n完成。")