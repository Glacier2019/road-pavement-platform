#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WIM 全链冒烟测试可视化

把 run_e2e_smoke.sh 的 13 跳、三个检查脚本的分工、两次负向验证画成一张图。

用法:
    PYTHONPATH=/data/cy/shujuku/.pylibs python3 plot_wim_chain.py [-o 输出.png]

★ 这张图的内容全部来自 scaffold/run_e2e_smoke.sh 的实际输出，不是设计稿。
"""
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Rectangle

_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for fam in ("Noto Sans CJK JP", "Noto Sans CJK SC", "Noto Serif CJK JP",
            "WenQuanYi Zen Hei", "DejaVu Sans"):
    if fam in _avail:
        plt.rcParams["font.family"] = fam
        break
plt.rcParams["axes.unicode_minus"] = False

OK, BAD, NEU = "#2E7D32", "#C62828", "#455A64"
BOX, BOX_E = "#E3F2FD", "#1565C0"
BG = "#FAFAFA"

# 6 个链容器（scaffold/README.md §1 定义）+ minio 为辅助第 7 个
CONTAINERS = [
    ("rp-mqtt", "MQTT  Broker\nEMQX 5.8", "18883"),
    ("rp-ingest", "ingest\n订阅+校验+写库", "8010"),
    ("rp-pg", "PostgreSQL\n时序+业务表", "55432"),
    ("rp-api", "api\n只读查询", "8001"),
    ("rp-console", "console\n网页端", "8024"),
    ("rp-grafana", "grafana\n看板", "3001"),
]

# 13 跳：(编号, 一句话, 所在环节)
HOPS = [
    (1,  "6 个容器在跑 + paho 可导入", "前置"),
    (2,  "记下基线行数，确认标记不存在", "pg"),
    (3,  "qos 1 发出报文", "mqtt"),
    (4,  "轮询等它落库（超时打印 ingest 日志）", "mqtt→ingest→pg"),
    (5,  "overload_flag / rate / esal 已算出", "pg"),
    (6,  "明细 4 行 = axle_num，轴重和 = 32800", "pg"),
    (7,  "重发同一报文：仍 1 行 + 日志见「重复报文」", "ingest 去重"),
    (8,  "换未注册设备号：行数不变 + 日志见「设备未注册」", "ingest 设备门"),
    (9,  "GET /v1/objects/wim_axle/{id} 按字段核对", "api"),
    (10, "GET /v1/objects/wim_axle?limit=1", "api"),
    (11, "GET /v1/metrics/wim_hourly 含当前小时", "api"),
    (12, "console 200 + grafana /api/health 解析后 database=ok", "console+grafana"),
    (13, "删明细→删主记录，两个计数回到基线", "清理"),
]


def box(ax, x, y, w, h, title, sub, port, ok=True):
    ax.add_patch(FancyBboxPatch((x, y), w, h,
                 boxstyle="round,pad=0.012,rounding_size=0.02",
                 fc=BOX, ec=BOX_E, lw=1.8))
    ax.text(x + w / 2, y + h * 0.70, title, ha="center", va="center",
            fontsize=11.5, fontweight="bold", color="#0D47A1")
    ax.text(x + w / 2, y + h * 0.38, sub, ha="center", va="center",
            fontsize=8.6, color="#37474F", linespacing=1.35)
    ax.text(x + w / 2, y + h * 0.10, f":{port}", ha="center", va="center",
            fontsize=8.2, color="#78909C")


def arrow(ax, x1, y1, x2, y2, color=BOX_E, lw=2.0, style="-|>", ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                 mutation_scale=15, color=color, lw=lw, ls=ls,
                 shrinkA=0, shrinkB=0))


def main():
    out = "wim_chain_plan.png"
    if "-o" in sys.argv:
        out = sys.argv[sys.argv.index("-o") + 1]

    fig = plt.figure(figsize=(15.5, 12.6), facecolor="white")
    gs = fig.add_gridspec(3, 1, height_ratios=[1.06, 1.20, 0.86],
                          hspace=0.20, left=0.035, right=0.975,
                          top=0.925, bottom=0.035)

    # ================================================== (a) 链路与 13 跳
    ax = fig.add_subplot(gs[0])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.set_title("(a) 一条报文穿过的 6 个容器，13 跳逐跳问「到了吗」",
                 fontsize=13.5, fontweight="bold", loc="left", pad=8)

    # 报文路径（上排）
    n = len(CONTAINERS)
    w, h, y = 0.135, 0.40, 0.44
    gap = (1.0 - n * w) / (n + 1)
    xs = [gap + i * (w + gap) for i in range(n)]
    for i, (name, sub, port) in enumerate(CONTAINERS):
        box(ax, xs[i], y, w, h, name, sub, port)
        if i < n - 1:
            arrow(ax, xs[i] + w, y + h / 2, xs[i + 1], y + h / 2)

    # 起点：模拟器
    ax.text(xs[0] - 0.012, y + h + 0.075, "wim_simulator.py\n发 wim_axle.v1",
            ha="right", va="center", fontsize=9, color="#6A1B9A",
            fontweight="bold", linespacing=1.3)
    arrow(ax, xs[0] - 0.008, y + h * 0.72, xs[0], y + h * 0.72,
          color="#6A1B9A", lw=2.0)

    # 13 跳刻度（下排）
    hy = 0.30
    for i, (num, desc, where) in enumerate(HOPS):
        x = 0.022 + i * 0.0742
        ax.add_patch(Rectangle((x, 0.045), 0.0665, 0.215,
                     fc="#FFFFFF", ec="#CFD8DC", lw=1.0))
        ax.text(x + 0.0332, 0.225, f"{num}", ha="center", va="center",
                fontsize=11, fontweight="bold", color=OK)
        ax.text(x + 0.0332, 0.175, where, ha="center", va="center",
                fontsize=6.6, color="#78909C")
        # 折行
        words, lines, cur = desc, [], ""
        for ch in words:
            cur += ch
            if len(cur) >= 7:
                lines.append(cur); cur = ""
        if cur:
            lines.append(cur)
        ax.text(x + 0.0332, 0.130, "\n".join(lines[:3]), ha="center",
                va="top", fontsize=6.5, color="#263238", linespacing=1.5)

    # ================================================== (b) 13 跳明细表
    ax = fig.add_subplot(gs[1])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.set_title("(b) 每一跳实际断言了什么 —— 负向验证证明这些断言真的会失败",
                 fontsize=13.5, fontweight="bold", loc="left", pad=8)

    col_x = [0.028, 0.078, 0.60]
    ax.text(col_x[0], 0.945, "#", fontsize=10, fontweight="bold", color="#37474F")
    ax.text(col_x[1], 0.945, "断言内容", fontsize=10, fontweight="bold", color="#37474F")
    ax.text(col_x[2], 0.945, "环节", fontsize=10, fontweight="bold", color="#37474F")
    ax.plot([0.022, 0.978], [0.925, 0.925], color="#B0BEC5", lw=1.2)

    for i, (num, desc, where) in enumerate(HOPS):
        yy = 0.878 - i * 0.0685
        if i % 2 == 0:
            ax.add_patch(Rectangle((0.020, yy - 0.028), 0.960, 0.062,
                         fc="#FFFFFF", ec="none"))
        ax.text(col_x[0], yy, f"{num}", fontsize=10.5, fontweight="bold",
                color=OK, va="center")
        ax.text(col_x[1], yy, desc, fontsize=10.2, color="#1A1A1A", va="center")
        ax.text(col_x[2], yy, where, fontsize=9.4, color="#546E7A", va="center")

    # ================================================== (c) 三个脚本分工 + 负向验证
    ax = fig.add_subplot(gs[2])
    ax.set_facecolor(BG)
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    ax.set_title("(c) 三个检查脚本各管一段，以及为什么前两个都绿链路仍可能是断的",
                 fontsize=13.5, fontweight="bold", loc="left", pad=8)

    cards = [
        ("run_contract_tests.sh", "离线", "只读源文件里的字符串\n不需要 Docker / 库 / 网络",
         "20 套件 · 18~20 通过", NEU),
        ("verify.sh", "只读", "看运行中的容器 / 库 / 网页\n不写库、不改文件、不重启",
         "all | containers | db | web", NEU),
        ("run_e2e_smoke.sh", "在线", "真发 MQTT、真写库、真读接口\n跑完清理回基线",
         "13 跳 · 0 全绿", OK),
    ]
    cw, ch, cy = 0.305, 0.50, 0.40
    for i, (name, tag, desc, stat, col) in enumerate(cards):
        cx = 0.022 + i * 0.326
        ax.add_patch(FancyBboxPatch((cx, cy), cw, ch,
                     boxstyle="round,pad=0.010,rounding_size=0.02",
                     fc="#FFFFFF", ec=col, lw=2.0))
        ax.text(cx + 0.014, cy + ch - 0.085, name, fontsize=10.6,
                fontweight="bold", color="#0D47A1", va="center")
        ax.add_patch(FancyBboxPatch((cx + cw - 0.078, cy + ch - 0.115), 0.064, 0.062,
                     boxstyle="round,pad=0.004,rounding_size=0.012",
                     fc=col, ec="none"))
        ax.text(cx + cw - 0.046, cy + ch - 0.084, tag, fontsize=8.8,
                color="white", fontweight="bold", ha="center", va="center")
        ax.text(cx + 0.014, cy + ch - 0.235, desc, fontsize=8.9,
                color="#37474F", va="top", linespacing=1.6)
        ax.text(cx + 0.014, cy + 0.055, stat, fontsize=9.4, fontweight="bold",
                color=col, va="center")

    # 负向验证
    ax.add_patch(FancyBboxPatch((0.022, 0.045), 0.956, 0.30,
                 boxstyle="round,pad=0.008,rounding_size=0.02",
                 fc="#FFEBEE", ec=BAD, lw=1.8))
    ax.text(0.040, 0.290, "负向验证（只做一次不够）", fontsize=11,
            fontweight="bold", color=BAD, va="center")
    ax.text(0.040, 0.195,
            "① 停掉 rp-ingest  →  第 1 跳报「未运行」  →  退出码 2（前置不满足 = 没跑成，不是通过）",
            fontsize=9.8, color="#B71C1C", va="center")
    ax.text(0.040, 0.105,
            "② 容器全在、报文发到没人订阅的主题  →  第 4 跳超时  →  退出码 1（这一跳真断了）",
            fontsize=9.8, color="#B71C1C", va="center")

    fig.text(0.035, 0.972, "WIM 全链端到端冒烟测试", fontsize=17,
             fontweight="bold")
    fig.text(0.035, 0.947,
             "scaffold/run_e2e_smoke.sh    commit 12b0788    13 跳全绿，两次负向验证均按预期失败",
             fontsize=10.5, color="#444444")

    fig.savefig(out, dpi=110, facecolor="white")
    print(f"已写出 {out}")


if __name__ == "__main__":
    main()
