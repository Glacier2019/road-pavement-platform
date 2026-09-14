#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图7 → 图8：去掉'七站/站①~⑦'框架，统一为 12 对象域 + 公共能力模块；加 12 对象域贯穿条"""
import re, os
os.environ["PYTHONPATH"] = ""

SRC = "/data/cy/shujuku/output/图7-平台模块搭建结构图.html"
DST = "/data/cy/shujuku/output/图8-平台模块搭建结构图.html"

html = open(SRC, encoding="utf-8").read()

REPL = [
    # 标题/副标题
    ("<title>路面性能数智化平台 · 七站模块搭建结构图（含数据流动）</title>",
     "<title>路面性能数智化平台 · 模块搭建结构图（12 对象域 · 含数据流动）</title>"),
    ("<h1>路面性能数智化平台 · 七站模块搭建结构图（含数据流动）</h1>",
     "<h1>路面性能数智化平台 · 模块搭建结构图（12 对象域为纲 · 含数据流动）</h1>"),
    ('<p class="sub">七站整体 = 平台/系统 ｜ 数据底座（玫红框）= 站②采集传输 + 站③双库存储 + 站④真数据门(含语义适配) + 站⑤多源融合辨析 + 站⑦服务输出API(含指标语义层) ｜ 应用（琥珀）= 数字孪生/承载力评估/养护决策 ｜ 红=反馈闭环 绿=数据需求工单闭环 ｜ LLM双通道：本地ollama/云端API</p>',
     '<p class="sub">平台 ＝ 数据底座 ＋ 底座之上的应用 ＋ 感知源 ｜ 数据组织主线＝12 对象域（统一标准） ｜ 数据底座（玫红框）＝ 接入＋双库存储＋治理(真数据门·语义适配)＋融合辨析＋API服务(指标语义层) ｜ 红=反馈闭环 绿=数据需求工单闭环 ｜ LLM双通道：本地ollama/云端API</p>'),
    # 平台总框
    ("<!-- ===== 平台总框（七站整体） ===== -->", "<!-- ===== 平台总框 ===== -->"),
    ('<text x="38" y="116" fill="#e2e8f0" font-size="13" font-weight="700">路面性能数智化平台（七站整体）＝ 数据底座 ＋ 底座之上的应用 ＋ 感知源</text>',
     '<text x="38" y="116" fill="#e2e8f0" font-size="13" font-weight="700">路面性能数智化平台 ＝ 数据底座 ＋ 底座之上的应用 ＋ 感知源（数据组织主线＝12 对象域）</text>'),
    # 感知源标题
    ('<text x="52" y="162" fill="#6ee7b7" font-size="10.5" font-weight="700">站① 感知源</text>',
     '<text x="52" y="162" fill="#6ee7b7" font-size="10.5" font-weight="700">感知源（数据入口）</text>'),
    # 底座大框
    ('<text x="284" y="164" fill="#fda4af" font-size="12" font-weight="700">数据底座（平台地基）＝ 站②采集传输 ＋ 站③双库存储 ＋ 站④处理·真数据门 ＋ 站⑤多源融合辨析 ＋ 站⑦服务输出(API)</text>',
     '<text x="284" y="164" fill="#fda4af" font-size="12" font-weight="700">数据底座（平台地基）＝ 数据接入 ＋ 双库存储 ＋ 数据治理·真数据门 ＋ 多源融合辨析 ＋ API 服务（含指标语义层）</text>'),
    # 模块标题
    ('<text x="302" y="202" fill="#67e8f9" font-size="10" font-weight="700">站② 采集传输</text>',
     '<text x="302" y="202" fill="#67e8f9" font-size="10" font-weight="700">数据接入模块</text>'),
    ('<text x="648" y="202" fill="#e9d5ff" font-size="10" font-weight="700">站③ 双库存储</text>',
     '<text x="648" y="202" fill="#e9d5ff" font-size="10" font-weight="700">双库存储模块</text>'),
    ('<text x="302" y="395" fill="#fdba74" font-size="10" font-weight="700">站④ 处理 · 真数据门（含语义适配）</text>',
     '<text x="302" y="395" fill="#fdba74" font-size="10" font-weight="700">数据治理 · 真数据门（含语义适配）</text>'),
    ('<text x="648" y="395" fill="#fda4af" font-size="10" font-weight="700">站⑤ 多源融合辨析</text>',
     '<text x="648" y="395" fill="#fda4af" font-size="10" font-weight="700">多源融合辨析模块</text>'),
    ('<text x="998" y="202" fill="#67e8f9" font-size="10" font-weight="700">站⑦ 服务输出(API)</text>',
     '<text x="998" y="202" fill="#67e8f9" font-size="10" font-weight="700">API 服务模块（含指标语义层）</text>'),
    ('<text x="1286" y="162" fill="#fde68a" font-size="10.5" font-weight="700">站⑥ 分析应用</text>',
     '<text x="1286" y="162" fill="#fde68a" font-size="10.5" font-weight="700">分析应用</text>'),
    # 框内残留
    ('<text x="1006" y="498" fill="#4ade80" font-size="7.6">未知→数据需求工单→站①</text>',
     '<text x="1006" y="498" fill="#4ade80" font-size="7.6">未知→数据需求工单→感知源</text>'),
    ('<text x="1008" y="644" fill="#4ade80" font-size="8.5" font-weight="700">② 数据需求工单：未知指标 → 站① 感知源</text>',
     '<text x="1008" y="644" fill="#4ade80" font-size="8.5" font-weight="700">数据需求工单：未知指标 → 感知源</text>'),
    ('<text x="1288" y="852" fill="#f87171" font-size="9" font-weight="700">反馈闭环：诊断结论·养护建议·模型输出·众包回流 → 反写底座（站②/③回接），驱动数据资产增值</text>',
     '<text x="1288" y="852" fill="#f87171" font-size="9" font-weight="700">反馈闭环：诊断结论·养护建议·模型输出·众包回流 → 反写底座（存储/治理回接），驱动数据资产增值</text>'),
    ('<text x="1288" y="870" fill="#fca5a5" font-size="7.8">应用(站⑥)产生的结果回写数据底座 → 支持数字孪生回溯、模型再训练、安全评价更新 —— 数据飞轮</text>',
     '<text x="1288" y="870" fill="#fca5a5" font-size="7.8">应用产生的结果回写数据底座 → 支持数字孪生回溯、模型再训练、安全评价更新 —— 数据飞轮</text>'),
    # 图例
    ('<text x="64" y="986" fill="#e2e8f0" font-size="9">数据底座（玫红框内 ②③④⑤+⑦API）</text>',
     '<text x="64" y="986" fill="#e2e8f0" font-size="9">数据底座（玫红框内：接入/存储/治理/融合/API服务）</text>'),
    ('<text x="384" y="986" fill="#e2e8f0" font-size="9">站⑥ 应用（底座之上）</text>',
     '<text x="384" y="986" fill="#e2e8f0" font-size="9">应用（底座之上）</text>'),
    ('<text x="604" y="986" fill="#e2e8f0" font-size="9">站① 感知源</text>',
     '<text x="604" y="986" fill="#e2e8f0" font-size="9">感知源（数据入口）</text>'),
    ('数据需求工单闭环(站⑦服务Copilot→站①补采)',
     '数据需求工单闭环(服务Copilot→感知源补采)'),
    ('注：站③双库实测核验（DDL 30+ 表 docker 验证通过）；选型细项与对比见报告正文「模块搭建与开源选型」章',
     '注：双库实测核验（DDL 30+ 表 docker 验证通过）；选型细项与对比见报告正文「模块实现与开源选型」章'),
    ('2025Y095 · 路面性能数据库科学架构 → 平台化总体设计 v1 ｜ 七站=平台 ｜ 底座=②~⑤+⑦API ｜ 应用：数字孪生/承载力评估/养护决策（+安全评价/智驾C端拓展）',
     '2025Y095 · 路面性能数据库科学架构 → 平台化总体设计 v2 ｜ 12 对象域统一标准 ｜ 底座=接入/存储/治理/融合/API服务 ｜ 应用：数字孪生/承载力评估/养护决策（+安全评价/智驾C端拓展）'),
]

cnt = 0
for old, new in REPL:
    if old in html:
        html = html.replace(old, new); cnt += 1
    else:
        print("!! 未匹配:", old[:60])

# 12 对象域贯穿条（插在红色闭环说明前）
bar = ('<rect x="290" y="742" width="946" height="20" rx="4" fill="rgba(148,163,184,.10)" stroke="#64748b" stroke-width=".7"/>\n'
       '<text x="300" y="756" fill="#cbd5e1" font-size="8">12 对象域贯穿底座：GE 道路几何 · SU 路面表面 · RE 结构响应⭐ · LO 交通荷载⭐ · FA 交通设施 · VI 视野感知 · SA 交通安全⭐ · WE 环境气象 · TE 试验检测⭐ · DE 决策输出 · QU 数据质量 · SE 众包服务⭐</text>\n')
anchor = '<text x="1288" y="852"'
assert anchor in html
html = html.replace(anchor, bar + anchor, 1)

open(DST, "w", encoding="utf-8").write(html)
print("图8 生成完成, 替换:", cnt, "/", len(REPL), ", 残留'站':", html.count("站①") + html.count("站②") + html.count("站③") + html.count("站④") + html.count("站⑤") + html.count("站⑥") + html.count("站⑦") + html.count("七站"))