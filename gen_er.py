#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成完整 ER 图（12 对象域 35 表）+ 路面性能库 ER 图（对象域版）"""
import os
os.environ["PYTHONPATH"] = ""

# ---------------- 公共：表定义 ----------------
DOM = {
 'GE':('#6ee7b7','rgba(6,78,59,0.14)','#34d399'),'SU':('#6ee7b7','rgba(6,78,59,0.14)','#34d399'),
 'RE':('#fda4af','rgba(136,19,55,0.12)','#fb7185'),'LO':('#fda4af','rgba(136,19,55,0.12)','#fb7185'),
 'FA':('#fdba74','rgba(251,146,60,0.12)','#fb923c'),'SA':('#fda4af','rgba(136,19,55,0.10)','#fb7185'),
 'WE':('#6ee7b7','rgba(6,78,59,0.10)','#34d399'),'TE':('#fda4af','rgba(136,19,55,0.10)','#fb7185'),
 'DE':('#67e8f9','rgba(8,51,68,0.12)','#22d3ee'),'QU':('#e9d5ff','rgba(76,29,149,0.12)','#a78bfa'),
 'SE':('#67e8f9','rgba(8,51,68,0.12)','#22d3ee'),
}
T = {}
def t(tid, dom, name, fields, w=235):
    T[tid] = dict(dom=dom, name=name, fields=fields, w=w)

# ---- GE 道路几何 ----
t('road_line','GE','road_line 路线',[('id','pk'),('line_code','u'),('line_name',''),('road_class',''),('lane_count',''),('lane_width_m','')],235)
t('road_section','GE','road_section 路段',[('id','pk'),('line_id','fk'),('section_name',''),('start_km',''),('end_km',''),('pavement_type','')],235)
t('structure_layer','GE','structure_layer 结构层',[('id','pk'),('section_id','fk'),('layer_no',''),('layer_role',''),('material',''),('thickness_cm','')],235)
t('monitor_cross','GE','monitor_cross_section 监测断面',[('id','pk'),('section_id','fk'),('stake_text',''),('stake_km',''),('lon/lat',''),('purpose','')],245)
t('geometry_point','GE','geometry_point 逐桩号线形',[('id','pk'),('section_id','fk'),('stake_km',''),('h_radius',''),('v_radius',''),('grade/superelev',''),('azimuth/elev','')],245)
# ---- SU 路面表面 ----
t('pavement_profile','SU','pavement_profile IRI',[('id','pk'),('section_id','fk'),('stake_km',''),('iri',''),('rqi',''),('psd_json',''),('measure_time','')],235)
t('disease_record','SU','disease_record 病害',[('id','pk'),('task_id','fk'),('section_id','fk'),('stake_km',''),('disease_code','fk'),('severity',''),('length/area','')],245)
t('scan3d_model','SU','scan3d_model 三维模型',[('id','pk'),('section_id','fk'),('scan_time',''),('file_path',''),('precision_mm',''),('coverage','')],235)
t('inspect_task','SU','inspect_task 巡检任务',[('id','pk'),('section_id','fk'),('task_type',''),('task_time',''),('vehicle_no',''),('status','')],235)
# ---- RE 结构响应 ----
t('sensor_install','RE','sensor_install 传感器布点',[('id','pk'),('cross_section_id','fk'),('sensor_type','fk'),('model/serial',''),('layer_id','fk'),('depth_cm',''),('status','')],250)
t('sensor_channel','RE','sensor_channel 监测通道',[('id','pk'),('install_id','fk'),('quantity_code','fk'),('unit/range',''),('sample_rate_hz',''),('acquire_mode',''),('storage_policy','')],250)
# ---- LO 交通荷载 ----
t('wim_axle_record','LO','wim_axle_record 车辆轴载(分区)',[('id','pk'),('cross_section_id','fk'),('pass_time','pk2'),('axle_type_code','fk'),('gross_weight_kg',''),('speed/plate',''),('dlc/esal_corr','')],255)
t('wim_axle_detail','LO','wim_axle_detail 轴组明细',[('id','pk'),('record_id','fk'),('pass_time','fk2'),('axle_seq',''),('axle_weight_kg',''),('axle_dist_mm','')],245)
t('traffic_daily_stat','LO','traffic_daily_stat 交通日统计',[('id','pk'),('cross_section_id','fk'),('stat_date',''),('total/heavy_cnt',''),('overload_cnt',''),('esal_total','')],245)
t('traffic_volume','LO','traffic_volume 交通量(L)',[('id','pk'),('section_id','fk'),('stake_km',''),('volume_time',''),('volume',''),('speed_v85','')],245)
t('wheel_path_dist','LO','wheel_path_distribution 轮迹分布',[('id','pk'),('section_id','fk'),('stake_from/to',''),('inner/outer_pct',''),('marking_score','')],250)
# ---- FA 交通设施 ----
t('traffic_sign','FA','traffic_sign 标志',[('id','pk'),('section_id','fk'),('stake_km',''),('sign_type',''),('content',''),('retroreflect',''),('condition','')],240)
t('safety_facility','FA','safety_facility 护栏',[('id','pk'),('section_id','fk'),('stake_from/to',''),('facility_type',''),('guardrail_level',''),('condition','')],250)
t('signal_control','FA','signal_control 信号',[('id','pk'),('section_id','fk'),('stake_km',''),('signal_type',''),('timing_json',''),('v2x_enabled','')],240)
t('v2x_facility','FA','v2x_facility RSU',[('id','pk'),('section_id','fk'),('stake_km',''),('facility_type',''),('comm_protocol',''),('coverage/status','')],250)
t('lane_marking','FA','lane_marking_state 标线',[('id','pk'),('section_id','fk'),('stake_from/to',''),('marking_type',''),('condition_score',''),('retroreflect','')],255)
# ---- SA 交通安全 ----
t('accident_record','SA','accident_record 事故(L)',[('id','pk'),('section_id','fk'),('stake_km',''),('accident_time',''),('severity',''),('weather/light',''),('cause','')],250)
t('conflict_event','SA','conflict_event 冲突(L)',[('id','pk'),('section_id','fk'),('stake_km',''),('event_time',''),('ttc',''),('source','')],245)
# ---- WE 环境 ----
t('weather_log','WE','weather 气象时序',[('id','pk'),('section_id','fk'),('stake_km',''),('obs_time',''),('temp/rh/wind',''),('rain/radiation','')],245)
# ---- TE 试验 ----
t('test_project','TE','test_project 试验项目',[('id','pk'),('test_no','u'),('test_type',''),('org_name',''),('standard_ref',''),('begin/end_time','')],250)
t('test_sample','TE','test_sample 试样',[('id','pk'),('project_id','fk'),('sample_no',''),('source_desc',''),('material_desc',''),('spec_desc','')],245)
t('test_result','TE','test_result 指标结果',[('id','pk'),('project_id','fk'),('sample_id','fk'),('indicator_code',''),('value/unit',''),('result_file','')],245)
# ---- DE 决策 ----
t('model_output','DE','model_output 模型输出',[('id','pk'),('object_type/id',''),('model_code',''),('output_type',''),('calc_time',''),('value/json','')],250)
t('diagnosis_result','DE','diagnosis_result 诊断',[('id','pk'),('object_type/id',''),('diagnose_time',''),('conclusion',''),('confidence',''),('basis','')],250)
t('maintenance_advice','DE','maintenance_advice 养护建议',[('id','pk'),('diagnosis_id','fk'),('section_id','fk'),('advice_type',''),('content',''),('priority/cost','')],255)
t('alarm_rule','DE','alarm_rule 预警规则',[('id','pk'),('channel_id','fk'),('rule_type',''),('params',''),('enabled','')],235)
t('alarm_record','DE','alarm_record 预警记录',[('id','pk'),('rule_id','fk'),('channel_id','fk'),('alarm_time',''),('level',''),('value/threshold','')],245)
# ---- QU 质量 ----
t('data_quality_log','QU','data_quality_log 质量日志',[('id','pk'),('channel_id','fk'),('period',''),('raw/valid_cnt',''),('issue_code',''),('action_code','')],250)
t('calibration_log','QU','calibration_log 校准',[('id','pk'),('channel_id','fk'),('calib_time',''),('calib_type',''),('factor',''),('operator','')],245)
t('data_import_batch','QU','data_import_batch 接入批次',[('id','pk'),('batch_no','u'),('source_type',''),('raw/valid_cnt',''),('truth_flag',''),('quality_code','')],250)
t('feedback_record','QU','feedback_record 反馈闭环',[('id','pk'),('feedback_time',''),('from/to_module',''),('biz_type/id',''),('content',''),('status/effect','')],250)
# ---- QU2 语义中枢配置（新增） ----
t('mapping_set','QU','mapping_set 映射配置集',[('id','pk'),('source_name',''),('source_type',''),('target_table',''),('mapping_json',''),('status',''),('confirm_level','')],225)
t('field_synonym','QU','field_synonym 字段同义词',[('id','pk'),('term',''),('standard_field',''),('standard_table',''),('lang',''),('synonym_type','')],225)
t('unit_conv','QU','unit_conv 单位换算',[('id','pk'),('from_unit',''),('to_unit',''),('factor',''),('category',''),('note','')],225)
t('stake_template','QU','stake_template 桩号模板',[('id','pk'),('pattern',''),('sample_text',''),('system_id',''),('is_regex',''),('parse_rule','')],225)
t('metric_registry','QU','metric_registry 指标注册表',[('id','pk'),('metric_code','u'),('metric_name',''),('metric_type',''),('formula',''),('source_fields','')],225)
t('data_request','QU','data_request 数据需求工单',[('id','pk'),('request_time',''),('metric_ref','fk'),('requester',''),('scope_desc',''),('status',''),('resolution','')],235)
# ---- SE 众包 ----
t('crowd_source','SE','crowd_source_ingest 众包回流',[('id','pk'),('section_id','fk'),('stake_km',''),('source_vendor',''),('data_type',''),('geo_encrypted',''),('quality_score','')],255)
t('hdmap_update','SE','hdmap_layer_update 高精图层',[('id','pk'),('layer_name',''),('update_type',''),('payload_json',''),('version',''),('consumer_id','')],255)
t('lane_detect','SE','lane_detectability 车道保持',[('id','pk'),('section_id','fk'),('stake_from/to',''),('marking_contrast',''),('detect_score',''),('weather_sens','')],255)

# ---------------- 关系 ---------------- 
EDGES = [
 ('road_line','road_section','1:N'),('road_section','structure_layer','1:N'),
 ('road_section','monitor_cross','1:N'),('road_section','geometry_point','1:N'),
 ('road_section','pavement_profile','1:N'),('road_section','inspect_task','1:N'),
 ('road_section','traffic_volume','1:N'),('road_section','wheel_path_dist','1:N'),
 ('road_section','traffic_sign','1:N'),('road_section','safety_facility','1:N'),
 ('road_section','signal_control','1:N'),('road_section','v2x_facility','1:N'),
 ('road_section','lane_marking','1:N'),('road_section','accident_record','1:N'),
 ('road_section','conflict_event','1:N'),('road_section','weather_log','1:N'),
 ('road_section','lane_detect','1:N'),('road_section','crowd_source','1:N'),
 ('monitor_cross','sensor_install','1:N'),('monitor_cross','wim_axle_record','1:N'),
 ('monitor_cross','traffic_daily_stat','1:N'),
 ('sensor_install','sensor_channel','1:N'),('sensor_install','structure_layer','N:1埋设层'),
 ('sensor_channel','alarm_rule','1:N'),('sensor_channel','alarm_record','1:N'),
 ('sensor_channel','data_quality_log','1:N'),('sensor_channel','calibration_log','1:N'),
 ('wim_axle_record','wim_axle_detail','1:N复合FK'),
 ('inspect_task','disease_record','1:N'),('disease_record','scan3d_model','N:1'),
 ('test_project','test_sample','1:N'),('test_project','test_result','1:N'),('test_sample','test_result','1:N'),
 ('diagnosis_result','maintenance_advice','1:N'),('alarm_rule','alarm_record','1:N'),
 ('model_output','diagnosis_result','N:1'),('data_import_batch','wim_axle_record','溯源'),
 ('feedback_record','diagnosis_result','闭环'),
]

# ---------------- 布局 ----------------
ROWS = [
 ('GE','GE 道路几何对象','road_line','road_section','structure_layer','monitor_cross','geometry_point'),
 ('SU','SU 路面表面对象','pavement_profile','disease_record','scan3d_model','inspect_task'),
 ('RE','RE 结构响应对象 ⭐路面性能独占','sensor_install','sensor_channel'),
 ('LO','LO 交通荷载对象 ⭐路面性能独占','wim_axle_record','wim_axle_detail','traffic_daily_stat','traffic_volume','wheel_path_dist'),
 ('FA','FA 交通设施对象','traffic_sign','safety_facility','signal_control','v2x_facility','lane_marking'),
 ('SA','SA 交通安全对象 ⭐安全评价独占','accident_record','conflict_event'),
 ('WE','WE 环境气象对象','weather_log'),
 ('TE','TE 试验检测对象 ⭐路面性能独占','test_project','test_sample','test_result'),
 ('DE','DE 决策输出对象','model_output','diagnosis_result','maintenance_advice','alarm_rule','alarm_record'),
 ('QU','QU 数据质量对象','data_quality_log','calibration_log','data_import_batch','feedback_record'),
 ('QU','QU 语义中枢配置(新增6表·接入/服务Copilot)','mapping_set','field_synonym','unit_conv','stake_template','metric_registry','data_request'),
 ('SE','SE 众包服务对象 ⭐智驾独占','crowd_source','hdmap_update','lane_detect'),
]

def build_svg(W=1520, H=None, highlight=None):
    svg = []
    if H is None:
        rows_cnt = len([r for r in ROWS if not highlight or r[0] in highlight])
        H = 60 + rows_cnt * 150 + 60
    svg.append(f'<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg">')
    svg.append('<defs><marker id="arr" markerWidth="8" markerHeight="6" refX="7" refY="3" orient="auto"><polygon points="0 0,8 3,0 6" fill="#64748b"/></marker>'
               '<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M 40 0 L 0 0 0 40" fill="none" stroke="#1e293b" stroke-width="0.5"/></pattern></defs>')
    svg.append(f'<rect width="{W}" height="{H}" fill="url(#grid)"/>')
    y = 30
    for dom, label, *tids in ROWS:
        if highlight and dom not in highlight: continue
        fg,bg,st = DOM[dom]
        n = len(tids)
        total_w = sum(T[t]['w'] for t in tids) + (n-1)*18
        x0 = 40
        svg.append(f'<rect x="{x0}" y="{y}" width="{total_w}" height="132" rx="10" fill="{bg}" stroke="{st}" stroke-width="1" stroke-dasharray="6,3"/>')
        svg.append(f'<text x="{x0+10}" y="{y+18}" fill="{fg}" font-size="10" font-weight="700">{label}</text>')
        cx = x0
        for tid in tids:
            tb = T[tid]; w = tb['w']
            svg.append(f'<rect x="{cx}" y="{y+28}" width="{w}" height="96" rx="6" fill="#0f172a" stroke="{st}" stroke-width="1.2"/>')
            svg.append(f'<rect x="{cx}" y="{y+28}" width="{w}" height="20" rx="6" fill="{bg}" stroke="{st}" stroke-width="1.2"/>')
            svg.append(f'<text x="{cx+8}" y="{y+42}" fill="{fg}" font-size="8.5" font-weight="600">{tb["name"]}</text>')
            fy = y+48
            for fname, tag in tb['fields']:
                mark = {'pk':' 🔑','pk2':' 🔑','fk':' 🔗','fk2':' 🔗','u':' ⚡'}.get(tag,'')
                col = {'pk':'#fbbf24','pk2':'#fbbf24','fk':'#67e8f9','fk2':'#67e8f9','u':'#a78bfa'}.get(tag,'#94a3b8')
                svg.append(f'<text x="{cx+8}" y="{fy}" fill="{col}" font-size="7">{fname}{mark}</text>')
                fy += 10
            T[tid]['cx'] = cx; T[tid]['cy'] = y+28; T[tid]['cy_b'] = y+124
            cx += w + 18
        y += 150
    return svg, y

def add_edges(svg, edge_filter=None):
    for f, tid, label in EDGES:
        if f not in T or tid not in T: continue
        if edge_filter and (f, tid) not in edge_filter and (tid, f) not in edge_filter: continue
        x1 = T[f]['cx']+T[f]['w']/2; y1 = T[f]['cy_b']
        x2 = T[tid]['cx']+T[tid]['w']/2; y2 = T[tid]['cy']-2
        svg.append(f'<line x1="{x1:.0f}" y1="{y1}" x2="{x2:.0f}" y2="{y2}" stroke="#64748b" stroke-width="0.9" marker-end="url(#arr)"/>')
        mx = (x1+x2)/2; my = (y1+y2)/2
        svg.append(f'<text x="{mx:.0f}" y="{my:.0f}" fill="#64748b" font-size="6.5" text-anchor="middle">{label}</text>')

def wrap(title, sub, svg, H, footer):
    return f'''<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>{title}</title>
<style>*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'JetBrains Mono','Noto Sans SC',monospace;background:#020617;padding:1.5rem;color:white}}
.container{{max-width:1560px;margin:0 auto}}
h1{{font-size:1.3rem;margin-bottom:.4rem}}.sub{{color:#94a3b8;font-size:.8rem;margin-bottom:1rem}}
.diagram{{background:rgba(15,23,42,.5);border:1px solid #1e293b;border-radius:1rem;padding:1rem;overflow-x:auto}}
svg{{min-width:1500px;display:block}}
.footer{{text-align:center;color:#475569;font-size:.75rem;margin-top:1rem}}</style></head><body>
<div class="container"><h1>{title}</h1>
<p class="sub">{sub}</p>
<div class="diagram">{''.join(svg)}</div>
<p class="footer">{footer}</p>
</div></body></html>'''

# ============ 图5：完整 ER ============
svg, H = build_svg()
add_edges(svg)
svg.append('<text x="1180" y="52" fill="white" font-size="9" font-weight="600">图例</text>')
svg.append('<text x="1180" y="68" fill="#fbbf24" font-size="7.5">🔑 主键 PK</text>')
svg.append('<text x="1180" y="82" fill="#67e8f9" font-size="7.5">🔗 外键 FK</text>')
svg.append('<text x="1180" y="96" fill="#a78bfa" font-size="7.5">⚡ 唯一 UQ</text>')
svg.append('<text x="1180" y="110" fill="#94a3b8" font-size="7.5">— 1:N 关系</text>')
svg.append('</svg>')
html = wrap('公路数据底座 · 完整 ER 图（12 对象域 · 41 表）',
    '对象域版：GE/SU/RE/LO/FA/SA/WE/TE/DE/QU/SE ｜ ⭐玫红=板块独占域 ｜ 外键以 🔗 标注 ｜ 新增 QU 语义中枢配置 6 表',
    svg, H, '2025Y095 路面性能数据库科学架构 · 对象域分类 v2 ｜ 完整 ER（逻辑层，物理 DDL 见 DDL v0.1+扩充）｜ QU 语义配置=mapping_set/field_synonym/unit_conv/stake_template/metric_registry/data_request')
with open('/data/cy/shujuku/output/图5-完整ER图-对象域版.html','w',encoding='utf-8') as f:
    f.write(html)
print("图5 完整ER OK, H=", H)

# ============ 图6：路面性能库 ER（独占域 RE/LO/TE + 关联 GE/SU/DE/QU） ============
svg, H = build_svg(highlight={'GE','SU','RE','LO','TE','DE','QU'})
# 只保留路面性能库相关连线
keep = set()
for f, tid, label in EDGES:
    s1 = T[f]['dom']; s2 = T[tid]['dom']
    both = {s1, s2}
    if both & {'RE','LO','TE'} or (both <= {'GE','SU','RE','LO','TE','DE','QU'} and (f,tid) in [
        ('road_line','road_section'),('road_section','geometry_point'),('sensor_install','structure_layer'),
        ('monitor_cross','sensor_install'),('monitor_cross','wim_axle_record'),
        ('sensor_install','sensor_channel'),('wim_axle_record','wim_axle_detail'),
        ('sensor_channel','alarm_rule'),('sensor_channel','alarm_record'),
        ('sensor_channel','data_quality_log'),('sensor_channel','calibration_log'),
        ('test_project','test_sample'),('test_project','test_result'),('test_sample','test_result'),
        ('diagnosis_result','maintenance_advice'),('alarm_rule','alarm_record'),
        ('model_output','diagnosis_result'),('data_import_batch','wim_axle_record'),
        ('feedback_record','diagnosis_result'),('road_section','pavement_profile'),
        ('road_section','structure_layer'),('road_section','monitor_cross'),
        ('disease_record','scan3d_model'),('road_section','disease_record'),
    ]):
        keep.add((f,tid))
add_edges(svg, edge_filter=keep)
svg.append('<text x="1180" y="52" fill="white" font-size="9" font-weight="600">图例</text>')
svg.append('<text x="1180" y="68" fill="#fbbf24" font-size="7.5">🔑 主键 PK</text>')
svg.append('<text x="1180" y="82" fill="#67e8f9" font-size="7.5">🔗 外键 FK</text>')
svg.append('<text x="1180" y="96" fill="#fda4af" font-size="7.5">⭐ 独占域（护城河）</text>')
svg.append('<text x="1180" y="110" fill="#94a3b8" font-size="7.5">绿/青/紫 = 共享关联域</text>')
svg.append('</svg>')
html = wrap('路面性能数据库 · ER 图（对象域版 · 独占核心 RE/LO/TE）',
    '⭐独占域：RE 结构响应 + LO 交通荷载 + TE 试验（护城河）｜ 关联：GE/SU/DE/QU ｜ 外键 🔗 标注',
    svg, H, '路面性能数据库 = 底座初始板块 · 对象域 v2 ｜ RE/LO/TE 是独占护城河，GE/SU/QU 是共享地基')
with open('/data/cy/shujuku/output/图6-路面性能库ER图.html','w',encoding='utf-8') as f:
    f.write(html)
print("图6 路面性能库ER OK, H=", H)

# ============ 图6-7核心域：路面性能库 ER（7 大核心对象域主体 · 25 表） ============
SEVEN = {'GE', 'SU', 'RE', 'LO', 'WE', 'TE', 'DE'}
svg7, H7 = build_svg(highlight=SEVEN)
keep7 = {(f, tid) for f, tid, _ in EDGES if T[f]['dom'] in SEVEN and T[tid]['dom'] in SEVEN}
add_edges(svg7, edge_filter=keep7)
svg7.append('<text x="1180" y="52" fill="white" font-size="9" font-weight="600">图例</text>')
svg7.append('<text x="1180" y="68" fill="#fbbf24" font-size="7.5">🔑 主键 PK</text>')
svg7.append('<text x="1180" y="82" fill="#67e8f9" font-size="7.5">🔗 外键 FK</text>')
svg7.append('<text x="1180" y="96" fill="#a78bfa" font-size="7.5">⚡ 唯一 UQ</text>')
svg7.append('<text x="1180" y="110" fill="#94a3b8" font-size="7.5">— 1:N 关系（7 域内部边）</text>')
svg7.append('</svg>')
html7 = wrap('路面性能数据库 ER 图（7 大核心对象域主体 · 25 表）',
    '主体域：GE 道路几何/SU 路面表面/RE 结构响应/LO 交通荷载/WE 环境气象/TE 试验检测/DE 决策输出 ｜ FA 交安设施等扩展域见末章 12 域全景',
    svg7, H7, '2025Y095 路面性能数据库科学架构 · 7 大核心对象域主体视图（严格执行路面性能库本体，交安设施等归扩展） ｜ 物理 DDL 见 v0.1 实测')
with open('/data/cy/shujuku/output/图6-路面性能库ER-7核心域.html', 'w', encoding='utf-8') as f:
    f.write(html7)
print("图6-7核心域 OK, H=", H7)