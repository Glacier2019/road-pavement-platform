#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成《平台架构总图与模块接口（含 7 域数据流动）》。

真源（不凭印象画）：
  · output/路面性能数智化平台总体设计与模块搭建方案…v1.2.md  §1 总体架构 / §2 七域数据资源
    / §3 公共能力模块 / §6.1 容器化服务清单 / §6.2 开发模块拆分 M1–M10 / §10.4 最小 MVP
  · scaffold/docker-compose.skeleton.yml（6 服务骨架栈的实际落地范围）
  · scaffold/contracts/**（三份契约的真源文件名）

用法：python3 gen_arch_diagram.py
输出：output/图-平台架构与模块接口（含7域数据流动）.svg/.png
"""
from __future__ import annotations

import math
import pathlib
import subprocess

FONT = "Noto Sans CJK SC"
OUT = pathlib.Path("/data/cy/shujuku/output")
NAME = "图-平台架构与模块接口（含7域数据流动）"

# ----------------------------------------------------------------- 配色
# ① 按“模块作用”分色（架构分层用色）
ROLE = {
    "access": ("#0e7490", "#cffafe", "接入层"),
    "store":  ("#1d4ed8", "#dbeafe", "存储层"),
    "dao":    ("#4338ca", "#e0e7ff", "访问层"),
    "gov":    ("#b45309", "#fef3c7", "治理层"),
    "fusion": ("#047857", "#d1fae5", "融合层"),
    "api":    ("#be123c", "#ffe4e6", "服务层"),
    "app":    ("#a21caf", "#fae8ff", "应用层"),
    "ops":    ("#475569", "#e2e8f0", "运维"),
}
# ② 7 大核心对象域各自的流动色（数据流泳道用色）
DOM = {
    "GE": ("#0369a1", "#e0f2fe"),
    "SU": ("#7c3aed", "#ede9fe"),
    "RE": ("#be123c", "#ffe4e6"),
    "LO": ("#b45309", "#fef3c7"),
    "WE": ("#0f766e", "#ccfbf1"),
    "TE": ("#4d7c0f", "#ecfccb"),
    "DE": ("#9333ea", "#f3e8ff"),
}
INK, SUB, LINE = "#0f172a", "#475569", "#94a3b8"
WARN: list[str] = []


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tw(s: str, size: float) -> float:
    """估算文本宽度：CJK/全角按 1.0em，ASCII 按 0.55em。用于越界自检。"""
    w = 0.0
    for ch in s:
        w += size * (1.0 if ord(ch) > 0x2E80 else 0.55)
    return w


def box(x, y, w, h, fill, stroke, rx=8, sw=1.6, dash=None, op=1.0):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" fill-opacity="{op}" stroke="{stroke}" stroke-width="{sw}"{d}/>')


TB: list[tuple[float, float, float, str]] = []          # 文本包围盒自检：(x0, y_top, x1, 文本)


def txt(x, y, s, size=12.5, fill=INK, anchor="start", weight="normal", op=1.0):
    w = tw(s, size)
    x0 = x if anchor == "start" else (x - w / 2 if anchor == "middle" else x - w)
    TB.append((x0, y - size, x0 + w, s))
    return (f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
            f'fill="{fill}" text-anchor="{anchor}" font-weight="{weight}" '
            f'opacity="{op}">{esc(s)}</text>')


def fit(x, y, lines, w, size=12.5, pad=10, fill=INK, weight="normal", lh=None):
    """左对齐多行文本，并自检不越出给定宽度。"""
    lh = lh or size * 1.42
    out = []
    for i, ln in enumerate(lines):
        if tw(ln, size) > w - 2 * pad:
            WARN.append(f"文本溢出 {w}px：{ln[:40]}")
        out.append(txt(x + pad, y + i * lh, ln, size, fill, weight=weight))
    return "".join(out)


def arrow(x1, y1, x2, y2, color=LINE, sw=2.0, dash=None, marker="arrow"):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
            f'stroke-width="{sw}"{d} marker-end="url(#{marker})"/>')


def badge(x, y, text, fill, color="#fff", size=11, pad=9, h=21):
    w = tw(text, size) + 2 * pad
    return (box(x, y, w, h, fill, fill, rx=h / 2, sw=0) +
            txt(x + w / 2, y + h * 0.71, text, size, color, anchor="middle", weight="bold")), w


def vmark(cx, cy, kind, r=7.5):
    """状态标记一律矢量绘制（不用 emoji 字体）：完成=圆勾，部分=半圆，规划=空心圆加横杠。"""
    if kind == "done":
        return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#15803d"/>'
                f'<path d="M {cx-3.7} {cy+0.3} l 2.6 2.7 l 4.9 -6.1" stroke="#ffffff" '
                f'stroke-width="2.1" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
    if kind == "part":
        return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#fef3c7" stroke="#b45309" stroke-width="1.7"/>'
                f'<path d="M {cx} {cy-r} a {r} {r} 0 0 1 0 {2*r} z" fill="#b45309"/>')
    return (f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#f1f5f9" stroke="#94a3b8" stroke-width="1.7"/>'
            f'<line x1="{cx-3.4}" y1="{cy}" x2="{cx+3.4}" y2="{cy}" stroke="#94a3b8" '
            f'stroke-width="2" stroke-linecap="round"/>')


ST_KIND = {"已落地": "done", "规划": "plan", "部分落地": "part"}
ST_COLOR = {"已落地": "#15803d", "规划": "#94a3b8", "部分落地": "#b45309"}


def status_right(x_right, y, status, size=12.5):
    """在 x_right 处右对齐地画「标记＋状态文字」。"""
    w = tw(status, size)
    out = txt(x_right, y, status, size, ST_COLOR[status], anchor="end", weight="bold")
    return out + vmark(x_right - w - 15, y - 4.4, ST_KIND[status])


S: list[str] = []
W, H = 2040, 1740


def add(s: str) -> None:
    S.append(s)


# ================================================================= 画布与标记
add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">')
add('<defs>'
    f'<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    f'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="{LINE}"/></marker>'
    '<marker id="arrowR" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#dc2626"/></marker>'
    '<marker id="arrowG" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
    'orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#16a34a"/></marker>'
    '</defs>')
add(f'<rect width="{W}" height="{H}" fill="#ffffff"/>')

# ================================================================= 标题
add(txt(40, 46, "路面性能数智化平台 · 架构总图、模块接口与 7 域数据流动", 30, INK, weight="bold"))
add(txt(40, 74, "2025Y095 研究内容3 ｜ G228 福州滨海大道试验段 ｜ 7 大核心对象域（GE/SU/RE/LO/WE/TE/DE）"
                "＋ 10 个开发模块（M1–M10）＋ 6 服务骨架栈已落地", 14.5, SUB))
add(box(40, 22, 6, 54, "#0f172a", "#0f172a", rx=3, sw=0))

# ================================================================= 图例
ly = 88
add(box(40, ly, W - 80, 62, "#f8fafc", "#cbd5e1", rx=10, sw=1.2))
lx = 58
add(txt(lx, ly + 24, "色标（按模块作用）", 12.5, SUB, weight="bold"))
lx += 150
for k in ("access", "store", "dao", "gov", "fusion", "api", "app", "ops"):
    st, bg, label = ROLE[k]
    add(box(lx, ly + 12, 20, 14, bg, st, rx=3, sw=1.4))
    add(txt(lx + 26, ly + 24, label, 12.5, INK))
    lx += tw(label, 12.5) + 50
add(txt(lx + 6, ly + 24, "｜  泳道色（7 对象域）", 12.5, SUB, weight="bold"))
lx += 190
for k in DOM:
    st, bg = DOM[k]
    add(box(lx, ly + 12, 20, 14, bg, st, rx=3, sw=1.4))
    add(txt(lx + 25, ly + 24, k, 12.5, INK, weight="bold"))
    lx += 48
add(txt(W - 452, ly + 25, "状态：", 12.5, SUB, weight="bold"))
add(vmark(W - 388, ly + 21, "done") + txt(W - 374, ly + 25, "已落地 6 服务", 12.2, INK))
add(vmark(W - 258, ly + 21, "part") + txt(W - 244, ly + 25, "部分落地", 12.2, INK))
add(vmark(W - 158, ly + 21, "plan") + txt(W - 144, ly + 25, "规划中", 12.2, INK))
add(txt(58, ly + 50, "接口契约以 ◆ 标注；实线＝数据流，红虚线＝反馈闭环（结果回写底座），"
                     "绿虚线＝数据需求工单闭环（未知指标→感知源补采）", 12, SUB))

# ================================================================= A 面板：分层架构
AX, AW = 40, 970
PANEL_Y, PANEL_H = 176, 886            # 由行布局推出：末行底 1046 + 16 留白
add(box(AX, PANEL_Y, AW, PANEL_H, "#ffffff", "#94a3b8", rx=12, sw=1.4, dash="6 4"))
add(txt(AX + 16, 204, "A ｜ 分层架构与模块接口（数据自下而上单向流动，应用层不直连存储）",
        15.5, INK, weight="bold"))

ROWS = [
    # key, y, h, 标题, 状态, 内容行
    ("access", 224, 96, "接入层 · M2 数据接入与设备自管",
     "已落地",
     ["▸ EMQX 5.8 —— MQTT Broker＋规则引擎（主题路由／边缘缓存／断点续传）",
      "▸ 设备自管：device_status 心跳 ＋ EMQX 认证/ACL（凭证存 PG，可轮换）",
      "▸ 备选 Kafka 削峰（可选）｜ 骨架栈的接入逻辑实现于 ingest 服务：订阅→校验→落库"]),
    ("store", 348, 108, "双库存储层 · M1 平台基础设施与编排（三库分工）",
     "已落地",
     ["▸ PostgreSQL 16 ＋ PostGIS —— GE/SU(元数据)/LO(分区)/TE/DE/业务档案（业务主库）",
      "▸ Apache IoTDB —— RE 高频时序 / WE / LO 流量明细（1k~128kHz）",
      "▸ MinIO 对象存储 —— SU 影像与三维模型 / TE 原始文件（库内只存路径索引）"]),
    ("dao", 484, 58, "M3 对象域数据访问层（DAO 契约 · 库内模块，不单独起服务）",
     "规划",
     ["▸ 7 域 repository 封装 ＋ PG/IoTDB/MinIO 统一访问 ＋ 分区与聚合接口——数据出口契约"]),
    ("gov", 570, 108, "治理层 · M4 真数据门 ＋ M7 语义中枢（接入 Copilot）",
     "部分落地",
     ["▸ M4：GE(校验套件)＋Airflow(DAG 调度)＋质量日志/truth_flag/校准补偿/待检区",
      "▸ 骨架栈已实现其“接入侧切片”：JSON Schema＋Pydantic 双实现校验、质量日志、批次台账",
      "▸ M7：结构探查→规则→LLM 候选→人工确认→mapping_set 持久化（上传表头/单位/桩号）"]),
    ("fusion", 706, 96, "融合辨析层 · M5 融合辨析引擎",
     "规划",
     ["▸ 桩号锚定对齐（以 GE 线形为统一时空基准）→ 事件触发关联（LO 轴载 × RE 响应 × WE 环境）",
      "▸ 诊断三元组（荷载事件 × 响应特征 × 环境状态）＋ 融合规则配置化热更新",
      "▸ 输出写入 DE 域 diagnosis_result，供应用与反馈闭环共用"]),
    ("api", 830, 96, "服务层 · M6 API 服务与指标语义层（底座对外的唯一出口）",
     "已落地",
     ["▸ FastAPI ＋ OpenAPI：对象查询／指标查询／动作层（501 占位）／订阅推送",
      "▸ 指标注册表三分支执行（直取／计算／模型）＋ 预警推送 ＋ MCP 工具面（10.4）",
      "▸ 契约即平台契约；应用层全部经此消费底座"]),
    ("app", 954, 92, "应用层 · M8 应用 ＋ M9 平台管理台（＋ M10 二期 Agent 引擎）",
     "规划",
     ["▸ M8：力学孪生 FEM（CalculiX/FEniCSx）· 承载力评估（sklearn 代理）· 养护决策（OR-Tools）",
      "▸ M8：CesiumJS 三维可视化 ／ M9：设备/映射/质量/工单 UI ＋ 权限 ＋ 集成测试",
      "▸ M10（二期）：DeepSeek Harness ＋ MCP 工具面 ＋ 轨迹审计桥"]),
]
for key, y, h, title, status, lines in ROWS:
    st, bg, _ = ROLE[key]
    add(box(AX + 16, y, AW - 32, h, bg, st, rx=9, sw=1.6))
    add(box(AX + 16, y, 7, h, st, st, rx=3, sw=0))
    add(txt(AX + 34, y + 24, title, 14.5, INK, weight="bold"))
    add(status_right(AX + AW - 36, y + 24, status))
    add(fit(AX + 16, y + 46, lines, AW - 32, 12.2, fill="#1e293b"))

# 层间契约标注（画在 A 面板右侧留白处的竖向连接线上）
CONTRACTS = [
    (320, "▲ 契约①MQTT topic 规范 ＋ 报文 JSON Schema（契约真源：contracts/messages/）"),
    (456, "▲ 契约②DDL v0.1 ／ 数据字典（物理 30 表；7 域逻辑视图 25 表）"),
    (542, "▲ 契约③DAO 契约（7 域 repository 接口，M3 对外承诺）"),
    (678, "▲ 契约④M6 OpenAPI（全平台服务契约：contracts/openapi/m6-gateway.v0.1.yaml）"),
    (802, "▲ 应用只经 M6 取数（不直连存储）"),
]
for yy, label in CONTRACTS:
    add(arrow(AX + 60, yy - 2, AX + 60, yy + 16, LINE, 1.8))
    if tw(label, 11.3) > (AX + AW - 10 - (AX + 74)):
        WARN.append(f"契约标注偏长：{label[:30]}")
    add(txt(AX + 74, yy + 14, label, 11.3, "#7c2d12"))

# ================================================================= C 面板：6 服务 / 模块分工 / 契约
CX, CW = 1036, 964
add(box(CX, PANEL_Y, CW, PANEL_H, "#ffffff", "#94a3b8", rx=12, sw=1.4, dash="6 4"))
add(txt(CX + 16, 204, "C ｜ 落地范围 · 模块分工 · 契约清单", 15.5, INK, weight="bold"))

# --- C1 六个服务
add(box(CX + 16, 220, CW - 32, 214, "#f0fdf4", "#15803d", rx=9, sw=1.5))
add(txt(CX + 32, 244, "骨架栈 6 服务（walking skeleton：一条竖切跑通全链，覆盖 M1/M2/M6＋运维）",
        13.2, "#14532d", weight="bold"))
svc = [
    ("pg", "postgis/postgis:16-3.4", "M1 业务主库：DDL＋分区＋种子自动初始化", "55432"),
    ("mqtt", "emqx/emqx:5.8", "M2 接入 Broker：现场报文统一入口", "18883/18083"),
    ("minio", "minio/minio:latest", "对象存储：SU 影像 / TE 原始文件", "9002/9003"),
    ("grafana", "grafana/grafana:11.3.0", "运维监控：质量与链路面板", "3001"),
    ("ingest", "python:3.11-slim（自建）", "M2 接入服务：订阅→契约校验→落库＋质量日志＋批次台账", "8010"),
    ("api", "python:3.11-slim（自建）", "M6 出口服务：对象/指标查询＋动作层占位（501）", "8001"),
]
for i, (n, img, desc, port) in enumerate(svc):
    yy = 258 + i * 29
    add(vmark(CX + 38, yy + 8, "done", 6))
    add(txt(CX + 50, yy + 12, n, 12.6, INK, weight="bold"))
    add(txt(CX + 130, yy + 12, img, 11.6, "#334155"))
    add(txt(CX + 400, yy + 12, desc, 11.6, "#334155"))
    add(txt(CX + CW - 48, yy + 12, f":{port}", 11.4, SUB, anchor="end"))

# --- C2 M1–M10 分工
add(box(CX + 16, 446, CW - 32, 362, "#eff6ff", "#1d4ed8", rx=9, sw=1.5))
add(txt(CX + 32, 470, "10 个开发模块 → 主责学生 × 实施阶段（关键路径 M1→M3→M4→M5→M6→M8/M9）",
        13.2, "#1e3a8a", weight="bold"))
mods = [
    ("M1", "平台基础设施与编排", "A", "P1", "已落地", "#15803d"),
    ("M2", "数据接入与设备自管", "B", "P1", "已落地", "#15803d"),
    ("M3", "对象域数据访问层（DAO 契约）", "A", "P1", "规划", "#94a3b8"),
    ("M4", "数据治理 · 真数据门", "A", "P1", "部分落地", "#b45309"),
    ("M5", "融合辨析引擎", "C", "P2", "规划", "#94a3b8"),
    ("M6", "API 服务与指标语义层", "D", "P2", "已落地", "#15803d"),
    ("M7", "语义中枢 · 接入 Copilot", "待指派", "P4", "规划", "#94a3b8"),
    ("M8", "应用层（FEM/承载力/决策/孪生）", "D", "P3", "规划", "#94a3b8"),
    ("M9", "平台管理台（集成面）", "D", "P3·P4", "规划", "#94a3b8"),
    ("M10", "Agent 执行引擎集成（二期）", "—", "P4", "规划", "#94a3b8"),
]
add(txt(CX + 46, 494, "模块", 11.4, SUB, weight="bold"))
add(txt(CX + 108, 494, "名称", 11.4, SUB, weight="bold"))
add(txt(CX + 452, 494, "主责", 11.4, SUB, weight="bold"))
add(txt(CX + 530, 494, "阶段", 11.4, SUB, weight="bold"))
add(txt(CX + 610, 494, "骨架栈状态", 11.4, SUB, weight="bold"))
for i, (m, nm, own, ph, stt, col) in enumerate(mods):
    yy = 502 + i * 26
    if i % 2 == 0:
        add(box(CX + 30, yy, CW - 60, 26, "#ffffff", "#ffffff", rx=4, sw=0, op=0.7))
    add(txt(CX + 46, yy + 18, m, 12.2, "#1d4ed8", weight="bold"))
    add(txt(CX + 108, yy + 18, nm, 12.0, INK))
    add(txt(CX + 452, yy + 18, own, 12.0, INK))
    add(txt(CX + 530, yy + 18, ph, 11.6, SUB))
    add(txt(CX + 610, yy + 18, stt, 11.6, col, weight="bold"))
add(txt(CX + 32, 778, "学生 A=数据底座 ｜ B=感知接入 ｜ C=融合诊断 ｜ D=应用决策；"
                      "三/四人版调整见《研究生任务分配与个人实施计划》第六、九章", 11.4, "#1e3a8a"))
add(txt(CX + 32, 796, "⚠ M7 在本计划第三章的任务分工表中未落到人头，M10 为二期；"
                      "建议由导师指定（M7 依赖 M2/M3/M4，靠近治理线）", 11.4, "#b45309"))

# --- C3 契约清单
add(box(CX + 16, 820, CW - 32, 226, "#fef2f2", "#be123c", rx=9, sw=1.5))
add(txt(CX + 32, 846, "接口契约四件套（模块间只认契约，不认实现）", 13.2, "#881337", weight="bold"))
for i, (nm, path, who) in enumerate([
    ("① MQTT topic 规范 ＋ 报文 JSON Schema", "contracts/messages/wim_axle.v1.schema.json", "M2 承诺 · 造数器/设备遵循"),
    ("② DDL v0.1 ＋ 数据字典（物理 30 表）", "output/路面性能数据库-DDL-v0.1.sql", "M1 承诺 · 存库口径唯一真源"),
    ("③ DAO 契约（7 域 repository）", "M3 对外接口（待定稿）", "M3 承诺 · 数据出口"),
    ("④ M6 OpenAPI（服务契约）", "contracts/openapi/m6-gateway.v0.1.yaml", "M6 承诺 · 全平台服务契约"),
]):
    yy = 862 + i * 42
    add(txt(CX + 34, yy + 16, nm, 12.2, INK, weight="bold"))
    add(txt(CX + 34, yy + 32, path, 11.0, "#7f1d1d"))
    add(txt(CX + CW - 46, yy + 16, who, 11.2, SUB, anchor="end"))

# ================================================================= B 面板：7 域数据流动
BX, BW = 40, 1960
B_PY = 1090
LANE_H, LANE_GAP = 54, 7
HEAD_Y = B_PY + 42
LANE_Y0 = B_PY + 78
BY = LANE_Y0 + 7 * (LANE_H + LANE_GAP) + 12      # 双闭环标注带
B_PH = (BY + 72) - B_PY                          # 面板高度由内容反推，避免标注越框
add(box(BX, B_PY, BW, B_PH, "#ffffff", "#94a3b8", rx=12, sw=1.4, dash="6 4"))
add(txt(BX + 16, B_PY + 28, "B ｜ 7 大核心对象域的数据流动（感知源 → 接入 → 双库存储 → 治理 → 融合 → M6 出口 → 应用）",
        15.5, INK, weight="bold"))

STAGE_X = [60, 316, 716, 936, 1246, 1496, 1816, 2000]
STAGE_W = [240, 384, 204, 294, 234, 304, 168]
HEADERS = ["对象域", "感知源（设备与内容）", "协议/频率", "落库（双库存储）", "治理（质量门）",
           "融合与消费（谁在用）", "M6 出口"]

for i, hdtxt in enumerate(HEADERS):
    add(box(STAGE_X[i], HEAD_Y, STAGE_W[i], 26, "#f1f5f9", "#cbd5e1", rx=5, sw=1.1))
    add(txt(STAGE_X[i] + STAGE_W[i] / 2, HEAD_Y + 18, hdtxt, 12.0, "#334155",
            anchor="middle", weight="bold"))

LANES = [
    ("GE", "道路几何", ["设计/竣工资料解析", "平纵横线形·超高·结构层·监测断面", "设计参数可解析求得"],
     ["ETL 批量导入", "批次"], "PG ＋ PostGIS（档案＋空间）", "设计值一致性校验",
     "★ 全部监测数据的桩号锚定基准（供全域消费）", "孪生/决策"),
    ("SU", "路面表面", ["三维扫描车 ＋ AI 巡检", "IRI/纹理/摩阻/病害/三维模型", "扫描一次多端受益"],
     ["批量上传对象存储", "周期＋事件触发"], "MinIO（影像/模型）＋ PG（元数据）", "覆盖率/里程连续性",
     "IRI → RQI 养护评定；反演标定；三维孪生底图", "养护决策"),
    ("RE", "结构响应", ["埋入式应变/光纤/土压/振动/挠度", "内部温湿度（毫秒级高频通道）", "荷载-响应机理唯一来源"],
     ["MQTT／采集仪", "1k~128kHz 事件＋连续"], "IoTDB（高频）＋ PG（测点档案）", "断流/漂移/量程/时钟",
     "与 LO 轴载事件触发对齐 → 响应特征与阈值", "承载力/诊断"),
    ("LO", "交通荷载", ["WIM 轴载站", "轴载/轴型/车速/车牌/ESAL/轮迹分布", "秒级连续"],
     ["MQTT 边缘汇聚", "秒级"], "PG 按月分区 ＋ 时序明细", "轴数=长度·总重=各轴之和",
     "事件源：超限触发 → 轴载谱/ESAL/DLC 修正", "承载力/孪生"),
    ("WE", "环境气象", ["路侧气象站", "温湿度/雨量/风/辐射（响应边界条件）", "分钟级"],
     ["MQTT / HTTP", "分钟级"], "IoTDB（时序）", "缺测/极值/站点一致性",
     "荷载-响应温度修正；病害-气象关联；事件天气标注", "诊断"),
    ("TE", "试验检测", ["试验室系统导入", "试验项目-试样-指标结果（三级结构）", "承载力/力学/耐久性"],
     ["ETL 批次导入", "批次"], "PG（三级结构）＋ MinIO（原始文件）", "批次溯源/单位归一",
     "实验室真值 ↔ 现场反演 互校与标定", "承载力评估"),
    ("DE", "决策输出", ["底座内生成", "模型输出/诊断结论/养护建议/预警", "路面性能库分析产物链"],
     ["内部闭环", "事件/日批"], "PG（决策链四表）", "人工复核/审计留痕",
     "经反馈闭环回写底座 → 孪生标注与模型再训练", "管理台/标注"),
]
ly0, lh, gap = LANE_Y0, LANE_H, LANE_GAP
for idx, (code, cn, src, proto, store, gov, fuse, out) in enumerate(LANES):
    st, bg = DOM[code]
    y = ly0 + idx * (lh + gap)
    add(box(BX + 20, y, BW - 40, lh, bg, st, rx=7, sw=1.3, op=0.55))
    add(box(BX + 20, y, 6, lh, st, st, rx=3, sw=0))
    add(txt(BX + 36, y + 22, code, 16, st, weight="bold"))
    add(txt(BX + 36, y + 41, cn, 11.6, INK))
    cells = [src, proto, [store], [gov], [fuse], [out]]
    for ci, cell in enumerate(cells):
        x = STAGE_X[ci + 1]
        add(fit(x, y + 12, cell, STAGE_W[ci + 1], 11.5, pad=8, lh=14))
        if ci < len(cells) - 1:
            gx = x + STAGE_W[ci + 1] + (STAGE_X[ci + 2] - (x + STAGE_W[ci + 1])) / 2
            add(f'<path d="M {gx-6} {y+lh/2-4} l 8 4 l -8 4 z" fill="{st}" opacity="0.8"/>')

# 双闭环（by 由泳道布局推出，面板高度再按它反推，保证不越框）
by = BY
add(arrow(BX + 1260, by + 8, BX + 250, by + 8, "#dc2626", 2.4, dash="9 5", marker="arrowR"))
add(txt(BX + 1300, by + 13, "红色虚线＝反馈闭环：诊断/养护/模型输出回写底座，支撑孪生标注与模型再训练",
        12.0, "#b91c1c", weight="bold"))
add(arrow(BX + 250, by + 32, BX + 1260, by + 32, "#16a34a", 2.4, dash="9 5", marker="arrowG"))
add(txt(BX + 1300, by + 37, "绿色虚线＝数据需求工单闭环：未知指标/补采需求 → 感知源（M7 工单）",
        12.0, "#15803d", weight="bold"))
add(txt(BX + 60, by + 13, "① 反馈闭环", 12.2, "#334155", weight="bold"))
add(txt(BX + 60, by + 37, "② 工单闭环", 12.2, "#334155", weight="bold"))
add(txt(BX + 60, by + 60, "末章扩展域 FA/VI/SA/QU/SE 见报告第八章（12 域全景）；本图只画 7 大核心对象域",
        11.6, SUB))

add('</svg>')

svg = "\n".join(S)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / f"{NAME}.svg").write_text(svg, encoding="utf-8")

# ----------------------------------------------------------------- 越界自检
import re
for m in re.finditer(r'<rect x="([-\d.]+)" y="([-\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg):
    x, y, w, h = map(float, m.groups())
    if x < 0 or y < 0 or x + w > W or y + h > H:
        WARN.append(f"图元越界：x={x} y={y} w={w} h={h}")
# 文本包围盒：① 不越画布 ② 不越所属面板
PANELS = [("A", AX, PANEL_Y, AW, PANEL_H), ("C", CX, PANEL_Y, CW, PANEL_H),
          ("B", BX, B_PY, BW, B_PH)]
for x0, y0, x1, s in TB:
    if x0 < 2 or y0 < 0 or x1 > W - 2:
        WARN.append(f"文本横向越界：{s[:36]}")
for nm, px, py, pw, ph in PANELS:
    if py + ph > H - 8:
        WARN.append(f"{nm} 面板越出画布：底 {py + ph} > {H - 8}")
for x0, y0, x1, s in TB:
    cx, cy = (x0 + x1) / 2, y0 + 7
    owners = [(nm, px, py, pw, ph) for nm, px, py, pw, ph in PANELS
              if px <= cx <= px + pw and py <= cy <= py + ph]
    if not owners:                                   # 标题/图例等面板外文本，只受画布约束
        continue
    nm, px, py, pw, ph = owners[0]
    if x0 < px - 1 or x1 > px + pw + 1 or y0 < py - 1 or y0 + 14 > py + ph:
        WARN.append(f"文本越出 {nm} 面板：{s[:36]}")

# 文本两两重叠检测（纵向 12px 内的相邻行才比较）
TB.sort(key=lambda t: (t[1], t[0]))
for i in range(len(TB)):
    ax0, ay0, ax1, a = TB[i]
    for j in range(i + 1, len(TB)):
        bx0, by0, bx1, b = TB[j]
        if by0 > ay0 + 12:
            break
        ov = min(ax1, bx1) - max(ax0, bx0)
        if ov > 6:
            WARN.append(f"文本重叠 {ov:.0f}px：「{a[:24]}」×「{b[:24]}」")
print(f"画布 {W}×{H}；B 面板底 {B_PY + B_PH}；越界/溢出告警 {len(WARN)} 条")
for w_ in WARN[:25]:
    print("  ⚠", w_)

r = subprocess.run(["inkscape", str(OUT / f"{NAME}.svg"), "--export-type=png",
                    f"--export-filename={OUT / (NAME + '.png')}", "--export-width=3400"],
                   capture_output=True, text=True, timeout=300)
print("inkscape:", "OK" if r.returncode == 0 else r.stderr[-400:])
p = OUT / f"{NAME}.png"
print("PNG:", p, p.stat().st_size if p.exists() else "缺失")
