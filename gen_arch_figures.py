#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""路面性能数智化平台 · 架构图（matplotlib 版，出版级）

======================= 图件契约（figure contract）=======================
1. 核心结论（每张图必须捍卫的一句话）
   平台以 7 大核心对象域为逻辑主线，由 M1–M10 十个开发模块按
   「接入→双库存储→对象域访问→治理→融合→M6 服务出口→应用」分层承载；
   当前骨架栈只落地 6 个服务，可跑通 7 域中的 5 域（RE 时序与 WE 全域缺落点）。
2. 证据链（三张图＝三个互不重复的论断）
   图1 分层架构与模块接口 —— 层是什么、每层欠下一层什么契约
   图2 七域数据流动       —— 每个域的数据怎么走、在哪里合并
   图3 落地范围与分工     —— 现在有多少、谁做哪块、契约真源在哪
3. 图型（archetype）：schematic-led composite
4. 后端（backend）：python（matplotlib）—— 技能已保存偏好为 python，R 未安装
5. 期刊/导出契约
   · 最终物理尺寸：宽 6.77 in（172 mm，＝报告中图件的实际排版宽度）
   · 字号下限：5 pt（每个渲染字形，含 SVG/PDF 文本层）
   · 可编辑文本：svg.fonttype=none、pdf.fonttype=42
   · 导出：PDF + SVG + PNG@600dpi
   · 交付前必过：validate_figure.py → audit_pdf_text.py --min-pt 5
                 → audit_figure_collisions.py（零 FAIL）
   · 单 axes 绘制（整幅为一个 plot area），故面板对齐门为 NOT APPLICABLE

配色遵循 stance「一个中性族＋一个信号族＋一个强调族」：
  · 中性族（层级/基础设施）：slate 灰阶 —— 层级靠明度阶梯区分，不靠色相
  · 信号族（7 大对象域）：技能调色板中抽出 7 色，灰度可辨
  · 强调族（状态与闭环）：绿＝已落地、琥珀＝部分落地、灰＝规划；红/绿虚线＝双闭环

用法：python3 gen_arch_figures.py
"""
from __future__ import annotations

import os
import pathlib
import sys

os.environ.setdefault("MPLCONFIGDIR", "/data/cy/shujuku/.mplcache")
pathlib.Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

import warnings

# 导出件纳入 git 管理，必须字节可复现：matplotlib 的 PDF 后端默认写入
# /CreationDate 当前时间。SOURCE_DATE_EPOCH 是可复现构建的标准机制，
# 必须在导入 matplotlib 之前设好——内建在这里，避免依赖调用者的环境。
os.environ.setdefault("SOURCE_DATE_EPOCH", "1700000000")   # 2023-11-14T22:13:20Z

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle, FancyArrow
from matplotlib.lines import Line2D

# ------------------------------------------------------------------ 全局设置
plt.rcParams.update({
    "font.family": "sans-serif",
    # 本机只暴露 Noto Sans CJK 的 **JP** 面（无 SC），若写 "Noto Sans CJK SC" 会静默
    # 回退。对中文报告而言 SC 字形正确性优先于轮廓观感（JP 面会把 直/骨/画 等渲染成
    # 日文变体），故显式首选 WenQuanYi Zen Hei，并已核验全图零缺字形。
    "font.sans-serif": ["WenQuanYi Zen Hei", "Noto Sans CJK JP", "DejaVu Sans"],
    "axes.unicode_minus": False,
    "svg.fonttype": "none",      # 文本保持可编辑
    # 导出件纳入 git 管理，必须字节可复现：matplotlib 默认会给 SVG 的 clip-path
    # 生成随机 id、并写入 dc:date 时间戳，导致每次重跑都产生一堆假 diff。
    "svg.hashsalt": "arch-figs-v1",   # 固定 clip-path 等元素 id
    "pdf.fonttype": 42,          # TrueType 嵌入，文本可选中
    "font.size": 5.2,            # 正文基准；所有显式字号均 ≥5pt 下限
    "axes.labelsize": 5.2,
    "axes.titlesize": 7.0,
    "xtick.labelsize": 5.0,
    "ytick.labelsize": 5.0,
    "legend.fontsize": 5.0,
    "figure.facecolor": "white",
    "savefig.facecolor": "white",
})

OUT = pathlib.Path("/data/cy/shujuku/output")
W_MM = 170.0                      # 报告中图件**最窄**的实际排版宽度（非期刊 89/183 栏宽）
W_IN = W_MM / 25.4                # 170 mm = 6.69 in；
# 说明：5 pt 下限必须在**实际复现宽度**下成立。报告把图件排到 6.69291 in（＝170.0 mm）
# 与 6.77165 in（＝172.0 mm）两种宽度，取**较窄者 170.0 mm** 作为出图尺寸，
# 这样即使被排到较窄的那一档，字号也不低于 5.0 pt。若按 183 mm（期刊双栏）出图再缩放
# 回 170 mm，字号会等比缩小 7%，5.0 pt 实际只剩 4.65 pt，下限即告失守。
PT = 72.0
FLOOR = 5.0                       # 字形下限（pt）

# ------------------------------------------------------------------ 配色
NEUTRAL = {                       # 中性族：层级明度阶梯
    "bg1": "#F4F5F7", "bg2": "#E9EBEF", "bg3": "#DEE1E7", "bg4": "#D3D7DF",
    "bg5": "#C8CDD6", "bg6": "#BFC5CF", "bg7": "#B6BDC8",
    "rule": "#4D4D4D", "mid": "#767676", "light": "#CFCECE", "ink": "#272727",
}
DOMAIN = {                        # 信号族：7 大对象域（取自技能调色板）
    "GE": "#0F4D92", "SU": "#3775BA", "RE": "#B64342", "LO": "#9A4D8E",
    "WE": "#42949E", "TE": "#4F8A3D", "DE": "#7C6CCF",
}
ACCENT = {"done": "#2E9E44", "part": "#B8860B", "plan": "#8A8A8A",
          "fb": "#E53935", "ticket": "#2E9E44", "ink2": "#4D4D4D"}

S_FLOOR = 5.0
S_BODY = 5.2
S_SMALL = 5.0
S_TITLE = 7.0
S_H = 6.2
S_FIGTITLE = 8.6

_MEASURE: list = []               # 供渲染后真实包围盒自检


def new_canvas(h_in: float):
    """建立精确坐标系：1 数据单位 = 1 pt。"""
    # 字面量便于静态审计识别；断言保证与 W_MM 恒等，避免两处漂移。
    assert abs(6.6929 * 25.4 - W_MM) < 0.05, "figsize 字面量与 W_MM 不一致"
    fig = plt.figure(figsize=(6.6929, h_in))   # 6.6929 in = 170.0 mm
    ax = fig.add_axes([0, 0, 1, 1])
    w, h = W_IN * PT, h_in * PT
    ax.set_xlim(0, w)
    ax.set_ylim(0, h)
    ax.axis("off")
    return fig, ax, w, h


def box(ax, x, y, w, h, face, edge=None, lw=0.5, r=1.6, ls="solid", z=1):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle=f"round,pad=0,rounding_size={r}",
                       linewidth=lw, edgecolor=edge or face, facecolor=face,
                       linestyle=ls, zorder=z)
    ax.add_patch(p)
    return p


def txt(ax, x, y, s, size=S_BODY, color=None, weight="normal",
        ha="left", va="top", track=True, rot=0):
    t = ax.text(x, y, s, fontsize=size, color=color or NEUTRAL["ink"],
                fontweight=weight, ha=ha, va=va, rotation=rot,
                zorder=5, rotation_mode="anchor")
    if track:
        _MEASURE.append({"t": t, "size": size, "s": s, "kind": "text"})
    return t


def fit_lines(ax, x, y, lines, width, size=S_BODY, lh=None, color=None,
              weight="normal", indent_pt=0):
    """逐行绘制并登记真实包围盒，用于渲染后核对是否越出分配宽度。"""
    lh = lh or size * 1.32
    for i, ln in enumerate(lines):
        t = txt(ax, x, y - i * lh, ln, size, color, weight=weight,
                va="top", track=False)
        _MEASURE.append({"t": t, "size": size, "s": ln, "kind": "fit",
                         "x0": x + indent_pt, "w_avail": width - indent_pt})
    return y - len(lines) * lh


def vmark(ax, cx, cy, kind, r=2.0):
    """状态标记一律矢量绘制（不用 emoji 字体）。"""
    if kind == "done":
        ax.add_patch(Rectangle((cx - r, cy - r), 2 * r, 2 * r, facecolor=ACCENT["done"],
                               edgecolor="none", zorder=6))
        ax.add_line(Line2D([cx - r * .45, cx - r * .08, cx + r * .5],
                           [cy, cy - r * .45, cy + r * .5],
                           color="white", lw=0.9, solid_capstyle="round", zorder=7))
    elif kind == "part":
        ax.add_patch(Rectangle((cx - r, cy - r), 2 * r, 2 * r, facecolor="white",
                               edgecolor=ACCENT["part"], lw=0.7, zorder=6))
        ax.add_patch(Rectangle((cx - r, cy - r), r, 2 * r, facecolor=ACCENT["part"],
                               edgecolor="none", zorder=7))
    else:
        ax.add_patch(Rectangle((cx - r, cy - r), 2 * r, 2 * r, facecolor="white",
                               edgecolor=ACCENT["plan"], lw=0.7, zorder=6))


def warn_mark(ax, cx, cy, r=2.0, color="#8A3A1E"):
    """矢量警告三角（不用 emoji / 不用 ⚠ 字形：本字体栈缺该字形）。"""
    tri = plt.Polygon([[cx, cy + r], [cx - r, cy - r * .75], [cx + r, cy - r * .75]],
                      closed=True, facecolor="none", edgecolor=color,
                      lw=0.6, zorder=6)
    ax.add_patch(tri)
    ax.add_line(Line2D([cx, cx], [cy - r * .45, cy + r * .12], color=color, lw=0.55, zorder=7))
    ax.add_line(Line2D([cx, cx], [cy - r * .68, cy - r * .68 + 0.35], color=color, lw=0.55, zorder=7))


def fig_title(ax, w, h, main, sub):
    txt(ax, 3, h - 3, main, S_FIGTITLE, NEUTRAL["ink"], weight="bold", va="top")
    txt(ax, 3, h - 3 - S_FIGTITLE * 1.35, sub, S_SMALL, ACCENT["ink2"], va="top")
    ax.add_line(Line2D([3, w - 3], [h - 3 - S_FIGTITLE * 1.35 - 6.4] * 2,
                       color=NEUTRAL["light"], lw=0.7, zorder=2))


def legend_marks(ax, x, y):
    items = [("done", "已落地"), ("part", "部分落地"), ("plan", "规划中")]
    for kind, lab in items:
        vmark(ax, x + 2.4, y - 2.2, kind)
        t = txt(ax, x + 6.4, y, lab, S_SMALL, ACCENT["ink2"], va="top", track=False)
        _MEASURE.append({"t": t, "size": S_SMALL, "s": lab, "kind": "text"})
        x += 6.4 + len(lab) * S_SMALL * 1.06 + 7.0
    return x


# ================================================================== 图1
def build_fig1():
    """分层架构与模块接口。"""
    fig, ax, w, h = new_canvas(4.86)
    fig_title(ax, w, h,
              "图 A ｜ 平台分层架构与模块接口",
              "路面性能数智化平台 · 7 大核心对象域 · M1–M10 开发模块 · G228 福州滨海大道试验段")

    y = h - 30
    x = legend_marks(ax, 3, y)
    txt(ax, x + 4, y, "｜  ← 左色标＝层级作用；层间▲＝接口契约（模块间只认契约，不认实现）",
        S_SMALL, ACCENT["ink2"], va="top")

    bands = [
        ("接入层 · M2 数据接入与设备自管", "done", NEUTRAL["bg1"],
         ["EMQX 5.8：MQTT Broker＋规则引擎（主题路由／边缘缓存／断点续传）；备选 Kafka 削峰",
          "设备自管：device_status 心跳＋EMQX 认证/ACL（凭证存 PG，可轮换），不引入重型设备平台",
          "骨架栈实现说明：接入逻辑落在 ingest 服务（订阅→契约校验→落库），而非 §3.2 的 EMQX 规则直写"]),
        ("双库存储层 · M1 平台基础设施与编排（三库分工）", "done", NEUTRAL["bg2"],
         ["PostgreSQL 16＋PostGIS：GE/SU(元数据)/LO(分区)/TE/DE/业务档案（业务主库）",
          "Apache IoTDB：RE 高频时序／WE 全域／LO 流量明细（1k~128kHz）——【骨架栈尚未接入】",
          "MinIO 对象存储：SU 影像与三维模型／TE 原始文件（库内只存路径索引）"]),
        ("M3 对象域数据访问层（DAO 契约 · 库内模块，不单独起服务）", "plan", NEUTRAL["bg3"],
         ["7 域 repository 封装＋PG/IoTDB/MinIO 统一访问＋分区与聚合接口——数据出口契约",
          "现状：api 服务直接持有 psycopg 连接池查 PG，DAO 尚未抽出（见待办①）",
          "本层是「应用不直连存储」这条铁律的落点"]),
        ("治理层 · M4 真数据门 ＋ M7 语义中枢（接入 Copilot）", "part", NEUTRAL["bg4"],
         ["M4：GE 校验套件＋Airflow DAG 调度＋质量日志/truth_flag/校准补偿/待检区",
          "骨架栈已实现其接入侧切片：JSON Schema＋Pydantic 双实现校验、质量日志、批次台账",
          "M7：结构探查→规则→LLM 候选→人工确认→mapping_set 持久化（上传表头/单位/桩号）"]),
        ("融合辨析层 · M5 融合辨析引擎", "plan", NEUTRAL["bg5"],
         ["桩号锚定对齐（以 GE 线形为统一时空基准）→ 事件触发关联（LO 轴载 × RE 响应 × WE 环境）",
          "诊断三元组（荷载事件 × 响应特征 × 环境状态）＋ 融合规则配置化热更新",
          "输出写入 DE 域 diagnosis_result，供应用与反馈闭环共用"]),
        ("服务层 · M6 API 服务与指标语义层（底座对外的唯一出口）", "done", NEUTRAL["bg6"],
         ["FastAPI＋OpenAPI：对象查询／指标查询／动作层（501 占位）／订阅推送",
          "指标注册表三分支执行（直取／计算／模型）＋预警推送＋MCP 工具面（报告 §10.4）",
          "契约即平台契约；应用层全部经此消费底座，不直连存储"]),
        ("应用层 · M8 应用 ＋ M9 平台管理台（＋ M10 二期 Agent 引擎）", "plan", NEUTRAL["bg7"],
         ["M8：力学孪生 FEM（CalculiX/FEniCSx）· 承载力评估（sklearn 代理）· 养护决策（OR-Tools）",
          "M8：CesiumJS 三维可视化 ／ M9：设备/映射/质量/工单 UI＋权限＋集成测试报告",
          "M10（二期）：DeepSeek Harness＋MCP 工具面＋轨迹审计桥"]),
    ]
    # 接口契约（画在层间空隙）
    contracts = [
        "▲ 契约① MQTT topic 规范＋报文 JSON Schema（contracts/messages/）",
        "▲ 契约② DDL v0.1／数据字典（25 表，存库口径唯一真源）",
        "▲ 契约③ DAO 契约（7 域 repository 接口，M3 对外承诺，待定稿）",
        "▲ 契约④ M6 OpenAPI（contracts/openapi/m6-gateway.v0.1.yaml）",
        "▲ 应用只经 M6 取数（不直连存储）",
        "▲ M9 集成面：设备/映射/质量/工单 UI 与权限收口",
    ]

    top = h - 42
    bh, gap = 33.0, 8.6
    cw = 138.0                     # 右侧契约栏宽度
    lw_ = w - 6 - cw - 4
    for i, (name, st, bg, lines) in enumerate(bands):
        bt = top - i * (bh + gap)
        box(ax, 3, bt - bh, lw_, bh, bg, NEUTRAL["mid"], lw=0.5)
        ax.add_patch(Rectangle((3, bt - bh), 1.7, bh, facecolor=NEUTRAL["rule"],
                               edgecolor="none", zorder=3))
        txt(ax, 6.6, bt - 2.2, name, S_H, NEUTRAL["ink"], weight="bold", va="top")
        lab = {"done": "已落地", "part": "部分落地", "plan": "规划中"}[st]
        sx = 3 + lw_ - 3.4
        vmark(ax, sx - len(lab) * S_SMALL * 1.06 - 4.6, bt - 4.6, st)
        txt(ax, sx, bt - 2.6, lab, S_SMALL, ACCENT[st], weight="bold",
            va="top", ha="right")
        fit_lines(ax, 6.6, bt - 11.4, lines, lw_ - 6, S_BODY, S_BODY * 1.32)
        if i < len(contracts):
            gy = bt - bh - gap / 2
            ax.add_line(Line2D([10, 10], [gy - 2.4, gy + 2.4], color=NEUTRAL["mid"],
                               lw=0.8, zorder=3))
            txt(ax, 12.6, gy + 2.0, contracts[i], S_SMALL, "#8A3A1E", va="top")

    txt(ax, w - cw + 2, top - 2.2, "接口契约（同时序对应左栏层间▲）", S_H,
        NEUTRAL["ink"], weight="bold", va="top")
    clines = [
        "① MQTT topic 规范＋报文 JSON Schema",
        "    真源 contracts/messages/wim_axle.v1.schema.json",
        "    承诺方 M2；造数器与设备共同遵循",
        "② DDL v0.1＋数据字典（25 表）",
        "    真源 output/路面性能数据库-DDL-v0.1.sql",
        "    承诺方 M1；存库口径唯一真源",
        "③ DAO 契约（7 域 repository）",
        "    真源 待定稿；承诺方 M3",
        "④ M6 OpenAPI（全平台服务契约）",
        "    真源 contracts/openapi/m6-gateway.v0.1.yaml",
        "    承诺方 M6；应用层唯一入口",
        "· 契约测试三项：DDL ／ 报文 Schema ／ OpenAPI",
        "    脚本 scaffold/tests/contract/（零依赖可运行）",
    ]
    fit_lines(ax, w - cw + 2, top - 10.6, clines, cw - 5, S_SMALL, S_SMALL * 1.30)
    cbox_top = top - 9.4                                   # 表头之下
    cbox_bot = top - bh - (len(bands) - 1) * (bh + gap) - 2.0   # 末层之下
    box(ax, w - cw, cbox_bot, cw - 3, cbox_top - cbox_bot, "none",
        NEUTRAL["light"], lw=0.5, ls=(0, (2, 1.6)))
    return fig, "架构图A-平台分层架构与模块接口"


# ================================================================== 图2
def build_fig2():
    """7 大对象域数据流动。"""
    fig, ax, w, h = new_canvas(4.20)
    fig_title(ax, w, h,
              "图 B ｜ 7 大核心对象域的数据流动",
              "感知源 → 接入 → 双库存储 → 治理 → 融合与消费（同名色标＝对象域；红虚线＝反馈闭环，绿虚线＝工单闭环）")

    cols = [("对象域", 44, 0), ("感知源（设备与内容）", 116, 1), ("协议/频率", 52, 2),
            ("落库（双库存储）", 88, 3), ("治理（质量门）", 66, 4),
            ("融合与消费（谁在用）／M6 出口", 108, 5)]
    x0, x = 3.0, 3.0
    for name, cw, _ in cols:
        box(ax, x, h - 44, cw - 0.8, 11.5, NEUTRAL["bg2"], NEUTRAL["light"], lw=0.5)
        txt(ax, x + cw / 2 - 0.4, h - 35.0, name, S_SMALL, NEUTRAL["ink"],
            weight="bold", ha="center", va="top")
        x += cw

    lanes = [
        ("GE", "道路几何", ["设计/竣工资料解析", "平纵横线形·超高·结构层", "监测断面"],
         ["ETL 批量导入", "批次"], ["PG＋PostGIS", "（档案＋空间）"], ["设计值一致性"],
         ["★ 全域桩号锚定基准", "孪生与决策共用"], "孪生/决策"),
        ("SU", "路面表面", ["三维扫描车＋AI 巡检", "IRI/纹理/摩阻/病害", "三维模型"],
         ["批量上传对象存储", "周期＋事件触发"], ["MinIO（影像/模型）", "＋PG（元数据）"],
         ["覆盖率/里程连续"], ["IRI → RQI 养护评定", "反演标定；孪生底图"], "养护决策"),
        ("RE", "结构响应", ["埋入式应变/光纤/", "土压/振动/挠度"],
         ["MQTT／采集仪", "1k~128kHz"], ["IoTDB（高频）", "＋PG（测点档案）"], ["断流/漂移/量程"],
         ["与 LO 轴载事件对齐", "→ 响应特征与阈值"], "承载力/诊断"),
        ("LO", "交通荷载", ["WIM 轴载站", "轴载/轴型/车速/", "ESAL/轮迹分布"],
         ["MQTT 边缘汇聚", "秒级"], ["PG 按月分区", "＋时序明细"], ["轴数=长度·总重和"],
         ["事件源：超限触发", "→ 轴载谱/ESAL"], "承载力/孪生"),
        ("WE", "环境气象", ["路侧气象站", "温湿度/雨量/风/辐射"],
         ["MQTT / HTTP", "分钟级"], ["IoTDB（时序）"], ["缺测/极值/一致"],
         ["荷载-响应温度修正", "病害-气象关联"], "诊断"),
        ("TE", "试验检测", ["试验室系统导入", "试验项目-试样-指标", "（三级结构）"],
         ["ETL 批次导入", "批次"], ["PG（三级结构）", "＋MinIO（原件）"], ["批次溯源/单位归一"],
         ["实验室真值 ↔", "现场反演互校标定"], "承载力评估"),
        ("DE", "决策输出", ["底座内生成", "模型输出/诊断结论/", "养护建议/预警"],
         ["内部闭环", "事件/日批"], ["PG（决策链四表）"], ["人工复核/审计"],
         ["经反馈闭环回写底座", "→ 孪生标注与再训练"], "管理台/标注"),
    ]
    ly, lh, lgap = h - 58, 26.0, 2.0
    for i, (code, cn, src, proto, store, gov, fuse, out) in enumerate(lanes):
        y = ly - i * (lh + lgap)
        box(ax, x0, y - lh, w - 6, lh, "white", NEUTRAL["light"], lw=0.45)
        ax.add_patch(Rectangle((x0, y - lh), 1.6, lh, facecolor=DOMAIN[code],
                               edgecolor="none", zorder=3))
        txt(ax, x0 + 3.4, y - 3.0, code, 7.2, DOMAIN[code], weight="bold", va="top")
        txt(ax, x0 + 3.4, y - 13.0, cn, S_SMALL, NEUTRAL["ink"], va="top")
        cx = x0
        for j, (cname, cw, ci) in enumerate(cols):
            if ci != 0:
                content = {1: src, 2: proto, 3: store, 4: gov,
                           5: fuse + ["M6 → " + out]}[ci]
                fit_lines(ax, cx, y - 5.0, content[:3], cw - 3.5, S_SMALL, S_SMALL * 1.42)
            if j < len(cols) - 1:
                ax.add_patch(FancyArrow(cx + cw - 2.6, y - lh / 2, 2.0, 0,
                                        width=0.6, head_width=2.4, head_length=1.4,
                                        length_includes_head=True,
                                        facecolor=DOMAIN[code], edgecolor="none",
                                        alpha=0.85, zorder=4))
            cx += cw

    lb = ly - (len(lanes) - 1) * (lh + lgap) - lh          # 末条泳道底边
    by = lb - 13.0
    ax.add_patch(FancyArrow(w - 8, by, -(w - 76), 0, width=0.5, head_width=2.6,
                            head_length=2.4, length_includes_head=True,
                            facecolor="none", edgecolor=ACCENT["fb"], lw=0.9,
                            linestyle=(0, (3, 1.6)), zorder=4))
    txt(ax, w - 10, by + 7.0, "红虚线＝反馈闭环：诊断/养护/模型输出回写底座，支撑孪生标注与模型再训练",
        S_SMALL, "#B3261E", ha="right", va="top")
    by2 = lb - 25.0
    ax.add_patch(FancyArrow(8, by2, (w - 76), 0, width=0.5, head_width=2.6,
                            head_length=2.4, length_includes_head=True,
                            facecolor="none", edgecolor=ACCENT["ticket"], lw=0.9,
                            linestyle=(0, (3, 1.6)), zorder=4))
    txt(ax, w - 10, by2 + 7.0, "绿虚线＝数据需求工单闭环：未知指标/补采需求 → 感知源（M7 工单）",
        S_SMALL, "#1B6B33", ha="right", va="top")
    txt(ax, 3, by2 - 6.0,
        "末章扩展域 FA/VI/SA/QU/SE 见报告第八章（12 域全景）；本图只画 7 大核心对象域。"
        "★ 注：RE 的时序与 WE 全域在骨架栈中尚无落点（IoTDB 未接入）。",
        S_SMALL, ACCENT["ink2"], va="top")
    return fig, "架构图B-七域数据流动"


# ================================================================== 图3
def build_fig3():
    """落地范围、模块分工与契约清单。"""
    fig, ax, w, h = new_canvas(4.50)
    fig_title(ax, w, h,
              "图 C ｜ 落地范围、模块分工与契约清单",
              "骨架栈 6 服务 vs 报告 §6.1 的 16 条容器化条目 · M1–M10 主责学生与阶段 · 接口契约四件套")

    y = h - 30
    # --- 块1：骨架栈 6 服务
    b1h = 78.0
    box(ax, 3, y - b1h, w - 6, b1h, NEUTRAL["bg1"], NEUTRAL["light"], lw=0.5)
    txt(ax, 6, y - 3, "骨架栈 6 服务（walking skeleton：一条竖切跑通全链，覆盖 M1／M2／M6 ＋ 运维）",
        S_H, NEUTRAL["ink"], weight="bold", va="top")
    svc = [("pg", "postgis/postgis:16-3.4", "M1 业务主库：DDL＋按月分区＋种子自动初始化", "55432"),
           ("mqtt", "emqx/emqx:5.8", "M2 接入 Broker：现场报文统一入口（认证/ACL）", "18883"),
           ("minio", "minio/minio:latest", "对象存储：SU 影像／TE 原始文件（库内只存路径）", "9002"),
           ("grafana", "grafana/grafana:11.3.0", "运维监控面板（现直查 PG，违反“不直连存储”，待办②）", "3001"),
           ("ingest", "python:3.11-slim（自建）", "M2 接入服务：订阅→契约校验→落库＋质量日志＋批次台账", "8010"),
           ("api", "python:3.11-slim（自建）", "M6 出口服务：对象/指标查询＋动作层占位（501）", "8001")]
    for i, (n, img, desc, port) in enumerate(svc):
        yy = y - 12.6 - i * 10.6
        vmark(ax, 8.0, yy - 1.6, "done", 1.7)
        txt(ax, 11.5, yy, n, S_BODY, NEUTRAL["ink"], weight="bold", va="top")
        txt(ax, 34, yy, img, S_SMALL, ACCENT["ink2"], va="top")
        txt(ax, 116, yy, desc, S_SMALL, ACCENT["ink2"], va="top")
        txt(ax, w - 6, yy, ":" + port, S_SMALL, NEUTRAL["mid"], ha="right", va="top")
    y -= b1h + 5

    # --- 块2：M1–M10 分工
    b2h = 138.0
    box(ax, 3, y - b2h, w - 6, b2h, NEUTRAL["bg2"], NEUTRAL["light"], lw=0.5)
    txt(ax, 6, y - 3, "10 个开发模块 → 主责学生 × 实施阶段（关键路径 M1→M3→M4→M5→M6→M8/M9）",
        S_H, NEUTRAL["ink"], weight="bold", va="top")
    hdr = [("模块", 30), ("名称", 196), ("主责", 34), ("阶段", 42), ("骨架栈状态", 58)]
    hx = 6
    for t, cw in hdr:
        txt(ax, hx, y - 13.2, t, S_SMALL, NEUTRAL["mid"], weight="bold", va="top")
        hx += cw
    mods = [("M1", "平台基础设施与编排", "A", "P1", "已落地", "done"),
            ("M2", "数据接入与设备自管", "B", "P1", "已落地", "done"),
            ("M3", "对象域数据访问层（DAO 契约）", "A", "P1", "规划中", "plan"),
            ("M4", "数据治理 · 真数据门", "A", "P1·P2", "部分落地", "part"),
            ("M5", "融合辨析引擎", "C", "P2", "规划中", "plan"),
            ("M6", "API 服务与指标语义层", "D", "P2", "已落地", "done"),
            ("M7", "语义中枢 · 接入 Copilot", "待指派", "P4", "规划中", "plan"),
            ("M8", "应用层（FEM/承载力/决策/孪生）", "D", "P3", "规划中", "plan"),
            ("M9", "平台管理台（集成面）", "D", "P3·P4", "规划中", "plan"),
            ("M10", "Agent 执行引擎集成（二期）", "—", "P5", "规划中", "plan")]
    for i, (m, nm, own, ph, stt, kind) in enumerate(mods):
        yy = y - 23.0 - i * 9.6
        txt(ax, 6, yy, m, S_BODY, NEUTRAL["ink"], weight="bold", va="top")
        txt(ax, 36, yy, nm, S_SMALL, NEUTRAL["ink"], va="top")
        c = "#B3261E" if own == "待指派" else NEUTRAL["ink"]
        txt(ax, 232, yy, own, S_SMALL, c, weight="bold" if own == "待指派" else "normal", va="top")
        txt(ax, 266, yy, ph, S_SMALL, NEUTRAL["mid"], va="top")
        vmark(ax, 313, yy - 1.7, kind, 1.7)
        txt(ax, 317, yy, stt, S_SMALL, ACCENT[kind], va="top")
    warn_mark(ax, 7.8, y - b2h + 13.0, 2.0)
    txt(ax, 11.6, y - b2h + 10.0,
        "M7 在本计划第三章的任务分工表中未落到人头（M10 属二期）；建议 P1 结束前由导师指定——"
        "M7 依赖 M2/M3/M4，位置上靠近治理线。学生 A=数据底座 ｜ B=感知接入 ｜ C=融合诊断 ｜ D=应用决策。",
        S_SMALL, "#8A3A1E", va="top")
    y -= b2h + 5

    # --- 块3：契约清单
    b3h = h - 30 - 78 - 5 - b2h - 5 - 2
    box(ax, 3, y - b3h, w - 6, b3h, NEUTRAL["bg3"], NEUTRAL["light"], lw=0.5)
    txt(ax, 6, y - 3, "接口契约四件套（模块间只认契约，不认实现）", S_H, NEUTRAL["ink"],
        weight="bold", va="top")
    ct = [("① MQTT topic 规范＋报文 JSON Schema", "contracts/messages/wim_axle.v1.schema.json", "M2 承诺"),
          ("② DDL v0.1＋数据字典（25 表）", "output/路面性能数据库-DDL-v0.1.sql", "M1 承诺"),
          ("③ DAO 契约（7 域 repository）", "M3 对外接口（待定稿）", "M3 承诺"),
          ("④ M6 OpenAPI（服务契约）", "contracts/openapi/m6-gateway.v0.1.yaml", "M6 承诺")]
    for i, (nm, path, who) in enumerate(ct):
        yy = y - 13.6 - i * 11.4
        txt(ax, 6, yy, nm, S_BODY, NEUTRAL["ink"], weight="bold", va="top")
        txt(ax, 6, yy - 5.6, path, S_SMALL, "#7A2E14", va="top")
        txt(ax, w - 6, yy, who, S_SMALL, NEUTRAL["mid"], ha="right", va="top")
    return fig, "架构图C-落地范围与模块分工"


# ================================================================== 自检与导出
def audit_fit(fig, ax, name, pw, ph):
    """渲染后取真实包围盒，核对每段文字是否越出分配宽度、是否低于 5pt。"""
    fig.canvas.draw()
    r = fig.canvas.get_renderer()
    inv = ax.transData.inverted()
    bad = []
    for m in _MEASURE:
        bb = m["t"].get_window_extent(renderer=r).transformed(inv)
        m["bb"] = bb
        if m["size"] < FLOOR - 1e-9:
            bad.append(f"字号 {m['size']}pt < {FLOOR}pt：{m['s'][:28]}")
        if bb.x0 < -0.5 or bb.x1 > pw + 0.5 or bb.y0 < -0.5 or bb.y1 > ph + 0.5:
            bad.append(f"出画布({bb.x0:.1f},{bb.y0:.1f})-({bb.x1:.1f},{bb.y1:.1f})：{m['s'][:26]}")
        if m["kind"] == "fit":
            if bb.x0 < m["x0"] - 0.8 or bb.x1 > m["x0"] + m["w_avail"] + 0.8:
                bad.append(f"越界(宽 {bb.width:.1f} > {m['w_avail']:.1f})：{m['s'][:30]}")
    # 文本两两重叠（同纵向带内）
    tb = sorted([m for m in _MEASURE if "bb" in m], key=lambda m: -m["bb"].y1)
    for i in range(len(tb)):
        a = tb[i]["bb"]
        for j in range(i + 1, len(tb)):
            b = tb[j]["bb"]
            if a.y0 > b.y1 + 0.2:
                continue
            if a.y1 < b.y0 - 0.2:
                continue
            ox = min(a.x1, b.x1) - max(a.x0, b.x0)
            oy = min(a.y1, b.y1) - max(a.y0, b.y0)
            if ox > 1.0 and oy > 1.0:
                bad.append(f"重叠 {ox:.1f}×{oy:.1f}pt：「{tb[i]['s'][:20]}」×「{tb[j]['s'][:20]}」")
    print(f"  [{name}] 文本 {len(_MEASURE)} 段；问题 {len(bad)} 条")
    for b in bad[:12]:
        print("     ⚠", b)
    return len(bad)


def export(fig, stem):
    base = OUT / stem
    with warnings.catch_warnings(record=True) as wlist:
        warnings.simplefilter("always")
        # metadata 去掉时间戳（PDF 的 CreationDate / SVG 的 dc:date）
        meta = {"Date": None, "Creator": "gen_arch_figures.py"}
        fig.savefig(str(base) + ".pdf", metadata=meta)
        fig.savefig(str(base) + ".svg", metadata=meta)
        fig.savefig(str(base) + ".png", dpi=600)
        fig.savefig(str(base) + ".tiff", dpi=600,
                    pil_kwargs={"compression": "tiff_lzw"})
    miss = sorted({str(w.message) for w in wlist
                   if "missing from font" in str(w.message)})
    for m in miss:
        print("     ✗ 缺字形：", m.split("Glyph ")[-1][:70])
    print(f"  导出 {stem}.pdf/.svg/.png/.tiff  缺字形 {len(miss)} 类")
    return len(miss)


def main():
    total = 0
    for build in (build_fig1, build_fig2, build_fig3):
        _MEASURE.clear()
        fig, stem = build()
        total += audit_fit(fig, fig.axes[0], stem, W_IN * PT,
                           fig.get_size_inches()[1] * PT)
        total += export(fig, stem)
        plt.close(fig)
    print(f"合计问题 {total} 条")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
