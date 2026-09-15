-- ============================================================================
-- 路面性能数据库（Road Pavement Performance Database）— DDL v0.2
-- ============================================================================
-- 项目：福建省交通运输科技计划项目 2025Y095《智慧公路路面结构断面监测与
--       数据融合养护管理技术研究》研究内容（3）路面性能数据库科学架构
-- 依据：开题报告(2026-03-30) / 立项申请书 / G228二期升级改造可研 / 城头服务区
--       信息化设计 / 行业标准（T/CECS 数据交换规程·公路数智化规范·桥梁监测
--       数据管理规程·边坡智能监测规程等）
-- 方言：PostgreSQL（>= 14）。移植说明：
--       * MySQL : GENERATED ALWAYS AS IDENTITY -> BIGINT AUTO_INCREMENT；
--                 jsonb -> json；text 长度需指定；PARTITION BY 语法不同
--       * 达梦/金仓(人大金仓V8R6) : 兼容 PG 模式可基本直接执行
-- 约定：库名 road_pavement（关系库）；时序库独立（IoTDB/TimescaleDB 待选型）
--       主键 bigint；时间戳统一 timestamptz；枚举优先字典表 + code 短值
--       命名：英文 snake_case，COMMENT 中文（对齐 T/CECS 数据字典风格）
-- ----------------------------------------------------------------------------
-- 版本变更记录
--   v0.1（初稿）  30 表：A 空间档案 6 ＋ B 字典 5 ＋ C 轴载交通 3 ＋ D 表观病害影像 4
--                       ＋ E 试验 3 ＋ F 模型诊断决策 5 ＋ G 质量反馈 4
--   v0.2（本版）  32 表：v0.1 全部保留（未改一列）＋ 新增
--                       G5 quality_rule  数据质量规则库（M4 真数据门的规则定义与标定状态）
--                       H1 mapping_set    语义映射集（M7 语义中枢的产出落点）
--                 依据：契约变更工单 #1（M4/M7 从机械层进入业务层的前置条件）
--                 兼容：纯新增，无破坏性变更；回滚 = DROP TABLE quality_rule, mapping_set;
-- ============================================================================

BEGIN;

-- ######################## A. 空间与档案（静态基础数据） ########################

-- A1. 路线表（如 G228 国道福清滨海大通道）
CREATE TABLE IF NOT EXISTS road_line (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    line_code     varchar(32)  NOT NULL UNIQUE,          -- 路线代码（如 G228）
    line_name     varchar(128) NOT NULL,                 -- 路线名称
    admin_region  varchar(64),                           -- 行政区域（福州市福清市）
    road_class    varchar(32),                           -- 公路等级（国道/省道/普通公路…）
    design_speed  smallint,                              -- 设计速度 km/h
    design_load   varchar(64),                           -- 设计荷载等级（如 BZZ-100）
    lane_count    smallint,                              -- 车道数
    lane_width_m  numeric(5,2),                          -- 单车道宽 m（试验段 3.75）
    manage_org    varchar(128),                          -- 管养单位（福清公路中心）
    remark        text
);
COMMENT ON TABLE  road_line IS '路线档案（一级）';
COMMENT ON COLUMN road_line.design_load IS '设计荷载等级';

-- A2. 路段表
CREATE TABLE IF NOT EXISTS road_section (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    line_id       bigint NOT NULL REFERENCES road_line(id),
    section_name  varchar(128) NOT NULL,                 -- 路段名称（滨海大通道试验段）
    start_stake   varchar(32),                           -- 起点桩号（K4635+000）
    end_stake     varchar(32),                           -- 终点桩号（K4654+701）
    start_km      numeric(10,3),                         -- 起点数值桩号 4635.000
    end_km        numeric(10,3),                         -- 终点数值桩号
    length_m      numeric(10,1),                         -- 路段长度 m
    direction     varchar(16),                           -- 方向（上行/下行）
    pavement_type varchar(64),                           -- 路面结构类型（沥青混凝土）
    climate_zone  varchar(64),                           -- 气候分区（南方湿热滨海）
    remark        text
);
COMMENT ON TABLE  road_section IS '路段档案（路线→路段）';
COMMENT ON COLUMN road_section.start_km IS '数值桩号便于区间排序检索';
CREATE INDEX IF NOT EXISTS idx_section_line ON road_section(line_id);

-- A3. 路面结构层表（断面分层结构，支撑数字孪生分层可视化）
CREATE TABLE IF NOT EXISTS structure_layer (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id    bigint NOT NULL REFERENCES road_section(id),
    layer_no      smallint NOT NULL,                     -- 自上而下层序号 1,2,3…
    layer_name    varchar(64),                           -- 层名（AC-13C 改性沥青抗滑表层）
    layer_role    varchar(32),                           -- 层位角色：面层/基层/底基层/路基
    material      varchar(128),                          -- 材料（改性沥青混凝土/5%水稳碎石…）
    thickness_cm  numeric(6,2),                          -- 厚度 cm
    remark        text,
    UNIQUE (section_id, layer_no)
);
COMMENT ON TABLE structure_layer IS '路面结构层（G228试验段示例：4cm AC-13C + 6cm AC-20C + 12cm ATB-25 + 16cm级配碎石 + 1cm沥青表处 + 32cm 5%水稳底基层）';
CREATE INDEX IF NOT EXISTS idx_layer_section ON structure_layer(section_id);

-- A4. 监测断面表（核心空间对象：桩号×车道×埋设组）
CREATE TABLE IF NOT EXISTS monitor_cross_section (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id    bigint NOT NULL REFERENCES road_section(id),
    stake_text    varchar(32) NOT NULL,                  -- 桩号 K4635+710
    stake_km      numeric(10,3) NOT NULL,                -- 4635.710
    lane_no       smallint,                              -- 车道序号（1=最外侧…）
    lon           numeric(10,7),                         -- 经度（CGCS2000/WGS84）
    lat           numeric(10,7),                         -- 纬度
    purpose       varchar(128),                          -- 用途（轴载调查/结构响应监测/应变断面）
    install_date  date,                                  -- 埋设/建成日期（2025 年埋设）
    status        varchar(16) DEFAULT 'active',          -- active/archived
    remark        text
);
COMMENT ON TABLE monitor_cross_section IS '监测断面（内观+表面监测的空间锚点）';
CREATE INDEX IF NOT EXISTS idx_cs_section_stake ON monitor_cross_section(section_id, stake_km);

-- A5. 传感器布点表（物理安装 → 内观：埋入式应变/土压/加速度/光纤；表面：相机/扫描）
CREATE TABLE IF NOT EXISTS sensor_install (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cross_section_id bigint NOT NULL REFERENCES monitor_cross_section(id),
    sensor_type_code varchar(32) NOT NULL,               -- 见 dict_sensor_type
    sensor_model     varchar(64),                        -- 型号（ZDG-40-SY-2…）
    manufacturer     varchar(128),
    serial_no        varchar(64),                        -- 出厂编号
    install_mode     varchar(16),                        -- 埋入式/表面式/车载式/非接触
    structure_layer_id bigint REFERENCES structure_layer(id), -- 埋设结构层（内观定位）
    install_depth_cm numeric(6,2),                       -- 距路表深度 cm
    position_desc    varchar(128),                       -- 布设位置描述（行车道轮迹带…）
    install_date     date,
    status           varchar(16) DEFAULT 'active',       -- active/fault/lost/retired
    remark           text
);
COMMENT ON TABLE sensor_install IS '传感器物理布点（内观监测：埋入式应变计/土压力盒/加速度/光纤；表面：视觉/声学/气象）';
COMMENT ON COLUMN sensor_install.sensor_type_code IS '石英称重/地感线圈/应变计(水泥/沥青)/土压力盒/三向加速度/钢筋计/温湿度/风速风向/雨量计/辐射/紫外/传声器/视觉相机/视频球机';
CREATE INDEX IF NOT EXISTS idx_install_cs ON sensor_install(cross_section_id);

-- A6. 监测通道表（逻辑测点：一安装点可多物理量通道，如三向加速度=3 通道）
CREATE TABLE IF NOT EXISTS sensor_channel (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    install_id        bigint NOT NULL REFERENCES sensor_install(id),
    channel_no        smallint DEFAULT 1,                -- 通道序号
    quantity_code     varchar(32) NOT NULL,              -- 物理量（应变/应力/土压/加速度/温度/湿度/雨量/挠度/声压级…）
    unit              varchar(16),                       -- 单位 με/MPa/kPa/g/℃/%RH/mm
    range_low         numeric(14,4),                     -- 量程下限（应变计 ±5000με）
    range_high        numeric(14,4),                     -- 量程上限
    sample_rate_hz    numeric(10,2),                     -- 标称采样率 Hz（1k~128k 分档）
    acquire_mode      varchar(16) DEFAULT 'continuous',  -- continuous 连续/trigger 自动触发/periodic 定时
    storage_policy    varchar(16) DEFAULT 'full',        -- full 全存/feature 仅特征值/ratio 稀释
    dilution_rule     jsonb,                             -- 稀释规则（PHM：分析点数/稀释比例）
    alarm_enable      boolean DEFAULT true,
    remark            text,
    UNIQUE (install_id, channel_no)
);
COMMENT ON TABLE sensor_channel IS '监测通道=逻辑测点；storage_policy 对应二期 PHM「数据稀释规则自定义，优化存储结构」';
CREATE INDEX IF NOT EXISTS idx_channel_install ON sensor_channel(install_id);

-- ######################## B. 字典表 ########################

CREATE TABLE IF NOT EXISTS dict_sensor_type (
    code    varchar(32) PRIMARY KEY,                     -- 如 WIM_QUARTZ / STRAIN_ASPHALT / ACCEL_3AXIS
    name    varchar(64) NOT NULL,                        -- 中文名
    category varchar(32),                                -- 交通荷载/气象环境/结构响应/表观巡检/声学
    remark  text
);
COMMENT ON TABLE dict_sensor_type IS '传感器类型字典';

CREATE TABLE IF NOT EXISTS dict_quantity (
    code    varchar(32) PRIMARY KEY,                     -- strain / stress / soil_pressure / accel / temp / rh / rain / wind_speed / deflection / spl…
    name    varchar(64) NOT NULL,
    unit    varchar(16),
    remark  text
);
COMMENT ON TABLE dict_quantity IS '物理量字典';

CREATE TABLE IF NOT EXISTS dict_disease_type (
    code    varchar(32) PRIMARY KEY,                     -- crack / pothole / rutting / patching / loose …
    name    varchar(64) NOT NULL,
    remark  text
);
COMMENT ON TABLE dict_disease_type IS '路面病害类型字典（裂缝/坑槽/车辙/修补/松散，城头养护巡查分类）';

CREATE TABLE IF NOT EXISTS dict_axle_type (
    code    varchar(16) PRIMARY KEY,                     -- A2 / T3 / T4 / T5 / T6
    name    varchar(32) NOT NULL,                        -- 2轴汽车/3轴货车/4轴货车/5轴货车/6轴货车
    remark  text
);
COMMENT ON TABLE dict_axle_type IS '车辆轴型字典（G228 实测轴型统计口径）';

CREATE TABLE IF NOT EXISTS dict_stat_metric (
    code    varchar(16) PRIMARY KEY,                     -- max/min/mean/rms/std/p95/esal_cnt
    name    varchar(64) NOT NULL,
    remark  text
);
COMMENT ON TABLE dict_stat_metric IS '特征值统计指标字典';

-- ######################## C. 轴载与交通（WIM，事件型高频） ########################

-- C1. 车辆轴载记录（海量：按月分区；对应 ZDG-40-SY-2 线圈330Hz/压电50kHz 自动触发）
CREATE TABLE IF NOT EXISTS wim_axle_record (
    id                bigint GENERATED ALWAYS AS IDENTITY,
    cross_section_id  bigint NOT NULL REFERENCES monitor_cross_section(id),
    pass_time         timestamptz NOT NULL,              -- 通过时刻（毫秒级）
    PRIMARY KEY (id, pass_time),                         -- 分区表 PK 必须含分区键
    lane_no           smallint,                          -- 车道
    direction         varchar(8),                        -- up/down
    axle_type_code    varchar(16) REFERENCES dict_axle_type(code), -- 轴型（2~6轴）
    axle_num          smallint,                          -- 轴数
    speed_kmh         numeric(5,1),                      -- 车速（1~200km/h，误差≤±1）
    gross_weight_kg   numeric(8,1),                      -- 总重 kg（单轴30t/过载200%）
    overload_flag     boolean,                           -- 是否超载
    overload_rate     numeric(6,2),                      -- 超载率 %（超载30%/50% 统计口径）
    esal              numeric(10,2),                     -- 当量轴次 ESAL
    plate_no          varchar(16),                       -- 车牌（与抓拍匹配，可空）
    video_media_id    bigint,                            -- 关联过车抓拍影像
    data_source       varchar(64),                       -- 采集设备/批次
    quality_code      varchar(8) DEFAULT 'OK',           -- OK/ERR/UNCERTAIN
    remark            text
) PARTITION BY RANGE (pass_time);
COMMENT ON TABLE wim_axle_record IS '车辆轴载记录（WIM，触发式事件数据）。G228 月均 1.3~1.9 万辆次、重载占 46~48%；每车 1 条主记录';
-- 月度分区示例（按月建分区；生产建议提前建 12 个月 + 默认分区）
-- CREATE TABLE wim_axle_record_p202511 PARTITION OF wim_axle_record
--   FOR VALUES FROM ('2025-11-01') TO ('2025-12-01');
-- …（需脚本化批量建分区；MySQL 对应 PARTITION BY RANGE(TO_DAYS(pass_time))）
CREATE UNIQUE INDEX IF NOT EXISTS uq_wim_id ON wim_axle_record(id, pass_time); -- 供 FK 引用（含分区键）
CREATE INDEX IF NOT EXISTS idx_wim_ts ON wim_axle_record(pass_time);
CREATE INDEX IF NOT EXISTS idx_wim_axle ON wim_axle_record(axle_type_code, pass_time);

-- C2. 轴组明细（每车各轴/轴组重量与轴距）
CREATE TABLE IF NOT EXISTS wim_axle_detail (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    record_id      bigint NOT NULL,
    pass_time      timestamptz NOT NULL,                 -- 冗余分区键，供复合 FK 引用分区表
    axle_seq       smallint NOT NULL,                    -- 第几轴（1..n）
    group_seq      smallint,                             -- 轴组序号
    axle_weight_kg numeric(8,1),                         -- 轴重 kg
    group_weight_kg numeric(8,1),                        -- 轴组重 kg
    axle_dist_mm   numeric(8,1),                         -- 与前轴轴距 mm（误差≤±100）
    FOREIGN KEY (record_id, pass_time) REFERENCES wim_axle_record(id, pass_time),
    UNIQUE (record_id, axle_seq)
);
COMMENT ON TABLE wim_axle_detail IS '轴/轴组明细（轴重+轴距，车辆分类与超载分析用）';
CREATE INDEX IF NOT EXISTS idx_wimd_rec ON wim_axle_detail(record_id);

-- C3. 交通量统计（日聚合，对应开题表5/表6 统计口径，供科研引用免扫明细）
CREATE TABLE IF NOT EXISTS traffic_daily_stat (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cross_section_id  bigint NOT NULL REFERENCES monitor_cross_section(id),
    stat_date         date NOT NULL,
    total_cnt         integer,                           -- 车辆总数
    truck_cnt         integer,                           -- 货车数
    heavy_cnt         integer,                           -- 重载车辆数
    overload_cnt      integer,                           -- 超载车辆数
    overload30_cnt    integer,                           -- 超载≥30% 数
    overload50_cnt    integer,                           -- 超载≥50% 数
    esal_total        numeric(14,2),                     -- 累计当量轴次
    by_axle_type      jsonb,                             -- {"A2":2434,"T6":8805,…} 分轴型计数
    overload_by_axle  jsonb,                             -- 分轴型超载占比（科研输出用）
    remark            text,
    UNIQUE (cross_section_id, stat_date)
);
COMMENT ON TABLE traffic_daily_stat IS '交通量日统计（科研常用聚合层，降明细查询压力）';

-- ######################## D. 表观普检 / 病害 / 影像（表面监测轨） ########################

-- D1. 巡检任务（三维高精扫描/车载 AI 巡查/人工）
CREATE TABLE IF NOT EXISTS inspect_task (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id    bigint NOT NULL REFERENCES road_section(id),
    task_type     varchar(32),                           -- scan3d / ai_vehicle / manual / uav
    task_time     timestamptz NOT NULL,
    vehicle_no    varchar(32),                           -- 巡检车/设备编号
    route_desc    varchar(256),                          -- 覆盖范围（起点桩号-终点桩号/车道）
    org           varchar(128),                          -- 执行机构
    status        varchar(16) DEFAULT 'done',
    remark        text
);
COMMENT ON TABLE inspect_task IS '巡检/普检任务（路段普检数据轨，与长期监测数据融合辨析）';
CREATE INDEX IF NOT EXISTS idx_task_sec_time ON inspect_task(section_id, task_time);

-- D2. 病害记录（表观监测结果）
CREATE TABLE IF NOT EXISTS disease_record (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id        bigint REFERENCES inspect_task(id),   -- 来源任务（可空=历史导入）
    section_id     bigint NOT NULL REFERENCES road_section(id),
    stake_text     varchar(32),                          -- 桩号
    stake_km       numeric(10,3),
    lane_no        smallint,
    disease_code   varchar(32) REFERENCES dict_disease_type(code), -- 病害类型
    severity       varchar(8),                           -- light/moderate/severe
    length_m       numeric(8,2),                         -- 长度 m（裂缝类）
    width_m        numeric(8,2),                         -- 宽度 m
    area_m2        numeric(10,2),                        -- 面积 m²（坑槽/修补类）
    image_media_ids jsonb,                               -- 病害图像附件 id 数组
    source         varchar(32),                          -- scan3d/ai/uav/manual
    status         varchar(16) DEFAULT 'pending',        -- pending/processing/done
    first_seen     timestamptz,                          -- 首次发现时间（病害观测演变）
    handle_time    timestamptz,
    remark         text
);
COMMENT ON TABLE disease_record IS '路面病害记录（表观/表面监测轨；支持病害演变观测=同一病害多期影像对比）';
CREATE INDEX IF NOT EXISTS idx_disease_sec_stake ON disease_record(section_id, stake_km);
CREATE INDEX IF NOT EXISTS idx_disease_code ON disease_record(disease_code);

-- D3. 三维扫描模型（路段普检：高精扫描+快速建模）
CREATE TABLE IF NOT EXISTS scan3d_model (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id    bigint NOT NULL REFERENCES road_section(id),
    scan_time     timestamptz NOT NULL,
    model_name    varchar(128),
    file_path     varchar(512) NOT NULL,                 -- 文件外置存储，库内索引路径
    file_format   varchar(16),                           -- .las/.ply/.obj/.glb…
    precision_mm  numeric(6,2),                          -- 精度 mm（本科研扫描达毫米级）
    coverage_from varchar(32),                           -- 覆盖起止桩号
    coverage_to   varchar(32),
    size_bytes    bigint,
    remark        text
);
COMMENT ON TABLE scan3d_model IS '三维高精扫描模型元数据（文件本体外置）';

-- D4. 影像/文件附件表（通用：病害图/巡检视频/抓拍/监测视频/现场照片）
CREATE TABLE IF NOT EXISTS media_file (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    biz_table    varchar(64),                            -- 业务表名（disease_record/scan3d_model/wim_axle_record…）
    biz_id       bigint,                                 -- 业务记录 id
    media_type   varchar(16),                            -- image/video/model/doc
    file_path    varchar(512) NOT NULL,
    file_name    varchar(256),
    size_bytes   bigint,
    width_px     integer,
    height_px    integer,
    duration_s   numeric(10,2),                          -- 视频时长
    taken_time   timestamptz,                            -- 拍摄时间
    stake_text   varchar(32),                            -- 拍摄桩号
    md5          char(32),                               -- 校验
    remark       text
);
COMMENT ON TABLE media_file IS '影像/文件元数据表（文件本体外置对象存储；对应手稿「图像数据」+城头影像方案）';
CREATE INDEX IF NOT EXISTS idx_media_biz ON media_file(biz_table, biz_id);

-- ######################## E. 试验检测数据 ########################

-- E1. 试验项目
CREATE TABLE IF NOT EXISTS test_project (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    test_no       varchar(64) UNIQUE,                    -- 试验编号
    test_type     varchar(32),                           -- 承载力静载/承载力动载/力学试验/材料试验/耐久性振动/室内试验/现场试验
    title         varchar(256) NOT NULL,
    org_name      varchar(128),                          -- 试验机构（福州大学/多机构对比）
    standard_ref  varchar(256),                          -- 依据标准
    research_ref  varchar(128),                          -- 关联科研项目（2025Y095）
    site_desc     varchar(256),                          -- 地点（试验段桩号/试验室）
    begin_time    timestamptz,
    end_time      timestamptz,
    status        varchar(16) DEFAULT 'planned',
    remark        text
);
COMMENT ON TABLE test_project IS '试验项目（承载力/力学/材料/传感器耐久性振动试验——DHDAS 动态信号采集）';

-- E2. 试样
CREATE TABLE IF NOT EXISTS test_sample (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id    bigint NOT NULL REFERENCES test_project(id),
    sample_no     varchar(64),
    source_desc   varchar(256),                          -- 来源（现场芯样桩号/构件/材料批次）
    material_desc varchar(256),                          -- 材料/结构描述
    spec_desc     varchar(256),                          -- 规格尺寸
    remark        text
);

-- E3. 试验指标结果
CREATE TABLE IF NOT EXISTS test_result (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id    bigint NOT NULL REFERENCES test_project(id),
    sample_id     bigint REFERENCES test_sample(id),
    indicator_code varchar(32),                          -- 指标（弯沉mm/模量MPa/承载力kN/共振频率Hz/失效判据…）
    value         numeric(14,4),
    unit          varchar(16),
    method_desc   varchar(256),                          -- 试验方法
    result_file   varchar(512),                          -- 原始记录文件（DHDAS 输出等）
    remark        text
);
COMMENT ON TABLE test_result IS '试验指标结果（多机构/多构件对比试验；静载/动载）';
CREATE INDEX IF NOT EXISTS idx_tres_proj ON test_result(project_id);

-- ######################## F. 模型输出 / 诊断 / 养护决策 ########################

-- F1. 模型/仿真输出（有限元理论值、大模型预测、寿命预测）→ 理论值-实测值对照
CREATE TABLE IF NOT EXISTS model_output (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    object_type   varchar(24) NOT NULL,                  -- section/cross_section/channel/network
    object_id     bigint NOT NULL,
    model_code    varchar(64),                           -- FEA_static/FEA_dynamic/LLM_pred/life_pred
    output_type   varchar(64),                           -- theory_strain/theory_stress/deflection/pci/寿命/风险
    calc_time     timestamptz NOT NULL,
    value         numeric(14,4),                         -- 标量结果
    value_json    jsonb,                                 -- 复杂结果（应力场/时间序列/概率）
    params        jsonb,                                 -- 工况参数（轴载/温度/边界）
    source_batch  varchar(128),                          -- 计算批次引用
    remark        text
);
COMMENT ON TABLE model_output IS '模型/仿真输出（预警机制「测试值与有限元计算值相结合」的理论值来源）';
CREATE INDEX IF NOT EXISTS idx_mo_obj ON model_output(object_type, object_id, calc_time);

-- F2. 融合诊断结果（监测+普检数据融合的辨析诊断）
CREATE TABLE IF NOT EXISTS diagnosis_result (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    object_type   varchar(24) NOT NULL,                  -- section/cross_section
    object_id     bigint NOT NULL,
    diagnose_time timestamptz NOT NULL,
    method_code   varchar(64),                           -- 融合辨析模型/健康诊断模型
    conclusion_code varchar(32),                         -- healthy/watch/warning/risky
    conclusion    text,                                  -- 结论描述（状态/风险/劣化模式）
    confidence    numeric(4,3),                          -- 置信度
    basis_summary text,                                  -- 依据（监测+普检数据摘要）
    report_media_id bigint,                              -- 诊断报告附件
    remark        text
);
COMMENT ON TABLE diagnosis_result IS '监测-普检融合辨析/诊断结论（手稿「精准-关联-参数」语境落点）';
CREATE INDEX IF NOT EXISTS idx_diag_obj_time ON diagnosis_result(object_type, object_id, diagnose_time);

-- F3. 养护决策建议（含预防性养护调控）
CREATE TABLE IF NOT EXISTS maintenance_advice (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    diagnosis_id  bigint REFERENCES diagnosis_result(id),
    section_id    bigint REFERENCES road_section(id),
    advice_type   varchar(32),                           -- preventive 预防性养护/repair 修复/strengthen 补强强化/monitor 加密监测
    content       text NOT NULL,                         -- 决策建议内容
    priority      smallint DEFAULT 3,                    -- 1~5 优先级
    cost_est      numeric(12,2),                         -- 费用估算 万元
    suggest_time  timestamptz DEFAULT now(),
    status        varchar(16) DEFAULT 'pending',         -- pending/approved/executing/done/evaluated
    effect_feedback text,                                -- 实施效果反馈（实时监测养护效果→优化计划）
    remark        text
);
COMMENT ON TABLE maintenance_advice IS '养护智慧优化决策建议（手稿「强化/承载力评估」与预防性养护调控输出）';

-- F4. 预警规则（超限/变化率/趋势/图谱对比/机器学习，PHM 模式）
CREATE TABLE IF NOT EXISTS alarm_rule (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    channel_id    bigint REFERENCES sensor_channel(id),  -- 通道级（可空=对象级）
    object_type   varchar(24),                           -- section/cross_section
    object_id     bigint,
    rule_type     varchar(24) NOT NULL,                  -- overrun/rate/trend/spectrum/ml
    params        jsonb NOT NULL,                        -- 阈值/窗口/模型引用
    enabled       boolean DEFAULT true,
    push_channel  varchar(64),                           -- popup/sms/email/app
    remark        text
);
COMMENT ON TABLE alarm_rule IS '预警规则库（阈值来自「测试值与有限元计算值结合」的应变预警值设置）';

-- F5. 预警记录
CREATE TABLE IF NOT EXISTS alarm_record (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_id       bigint REFERENCES alarm_rule(id),
    channel_id    bigint REFERENCES sensor_channel(id),
    alarm_time    timestamptz NOT NULL,
    level         varchar(8) NOT NULL,                   -- yellow/red（黄红二级）
    actual_value  numeric(14,4),
    threshold     numeric(14,4),
    content       text,
    status        varchar(16) DEFAULT 'unconfirmed',     -- unconfirmed/confirmed/handled
    confirm_by    varchar(64),
    confirm_time  timestamptz,
    handle_desc   text,
    remark        text
);
COMMENT ON TABLE alarm_record IS '预警记录（黄/红二级报警，弹窗/短信/邮件/APP 推送）';
CREATE INDEX IF NOT EXISTS idx_alarm_time ON alarm_record(alarm_time);
CREATE INDEX IF NOT EXISTS idx_alarm_chn ON alarm_record(channel_id, alarm_time);

-- ######################## G. 数据质量 / 校准 / 反馈闭环 / 接入批次 ########################

-- G1. 数据质量日志（真数据治理）
CREATE TABLE IF NOT EXISTS data_quality_log (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    channel_id    bigint NOT NULL REFERENCES sensor_channel(id),
    period_start  timestamptz,
    period_end    timestamptz,
    raw_count     bigint,                                -- 原始点数
    valid_count   bigint,                                -- 有效点数
    issue_code    varchar(32),                           -- missing/overrange/step/drift/abnormal
    issue_desc    text,
    action_code   varchar(16),                           -- drop/interpolate/calibrate/mark
    process_time  timestamptz,
    processor     varchar(64),
    remark        text
);
COMMENT ON TABLE data_quality_log IS '数据质量评估日志（T/CECS DataQuality 字段落地；预处理-清洗→「真数据」入库）';
CREATE INDEX IF NOT EXISTS idx_dql_chn_time ON data_quality_log(channel_id, period_start);

-- G2. 校准记录（数据处理层：变形校准/应力校准/温湿度场校准）
CREATE TABLE IF NOT EXISTS calibration_log (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    channel_id    bigint NOT NULL REFERENCES sensor_channel(id),
    calib_time    timestamptz NOT NULL,
    calib_type    varchar(32),                           -- deformation/stress/temp_humidity/zero
    value_before  numeric(14,4),
    value_after   numeric(14,4),
    factor        numeric(12,6),                         -- 校准系数
    method_desc   text,
    operator      varchar(64),
    remark        text
);
COMMENT ON TABLE calibration_log IS '校准记录（开题七层架构「数据处理层：变形/应力/温湿度场校准」落地）';

-- G3. 反馈回流记录（手稿第8条闭环：发送、反馈信息→设计数据库）
CREATE TABLE IF NOT EXISTS feedback_record (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    feedback_time timestamptz NOT NULL DEFAULT now(),
    from_module   varchar(32),                           -- 监测/诊断/养护决策/大模型/工程应用
    to_module     varchar(32),                           -- design 设计/采集配置/database 数据库/标准
    biz_type      varchar(32),                           -- 关联业务（diagnosis/advice/alarm/stat…）
    biz_id        bigint,
    content       text NOT NULL,                         -- 反馈内容（养护效果/设计优化建议/采集方案调整）
    status        varchar(16) DEFAULT 'open',            -- open/accepted/done
    effect_desc   text,                                  -- 落地效果（反哺工程设计）
    remark        text
);
COMMENT ON TABLE feedback_record IS '反馈闭环：分析/应用结果回流，反哺工程设计（「反馈给设计数据库」）';
CREATE INDEX IF NOT EXISTS idx_fb_time ON feedback_record(feedback_time);

-- G4. 数据接入批次（MQTT/文件/API 接入溯源）
CREATE TABLE IF NOT EXISTS data_import_batch (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_no        varchar(64) UNIQUE,
    source_type     varchar(24),                         -- mqtt/file/api/manual
    source_desc     varchar(256),                        -- 采集设备/采集站/机柜/文件路径
    channel_count   integer,                             -- 涉及通道数
    raw_count       bigint,                              -- 接收原始条数
    valid_count     bigint,                              -- 通过质量门条数
    truth_flag      boolean DEFAULT false,               -- 是否已晋升「真数据」（可入库科研）
    import_start    timestamptz,
    import_end      timestamptz,
    quality_code    varchar(8) DEFAULT 'OK',
    handler         varchar(64),
    remark          text
);
COMMENT ON TABLE data_import_batch IS '数据接入批次（MQTT 上传溯源；「数据库挖掘→真数据」质量门落地）';

-- G5. 数据质量规则库（M4 真数据门：规则定义 + 标定状态）  —— v0.2 新增
--
-- 为什么需要这张表：M4 的核心动作是「用真实数据标定阈值」，而在 v0.1 里规则只存在于
-- 配置文件 modules/M4-governance/config/quality_rules.yaml —— 库里没有一条记录，于是
-- 「这条规则标定过没有、谁标的、依据是什么」无法审计，calibrated 只是个 YAML 布尔值。
--
-- ★ 关键约束 ck_qr_code_layer：规则码的前缀必须等于 layer 字段。
--   这使「同一条规则在 M2（接入时点）与 M4（批次时点）用了不同的码」这种漂移
--   **在数据库层面就写不进去**，而不是靠人记得对齐或靠契约测试事后抓。
--   （背景：曾出现 26 条违约全记成同一个 issue_code，3 类违约在日志里分不出来。）
CREATE TABLE IF NOT EXISTS quality_rule (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code          varchar(32) NOT NULL UNIQUE,            -- 稳定规则码，如 cross.gross_vs_axle_sum
    layer         varchar(16) NOT NULL,                   -- 码前缀：schema/missing/range/enum/cross/time
    suite         varchar(16) NOT NULL,                   -- 套件：缺测/越界/离群/跨字段/时序
    domain_code   varchar(4),                             -- GE/SU/RE/LO/WE/TE/DE；NULL=跨域
    target_table  varchar(64),                            -- 作用表
    target_field  varchar(64),                            -- 作用字段；NULL=整行或跨字段
    rule_type     varchar(24) NOT NULL,                   -- range/enum/missing/outlier/cross/trend
    severity      varchar(16) NOT NULL,                   -- block 拒收 / warn 告警 / mark 仅标记
    params        jsonb NOT NULL DEFAULT '{}'::jsonb,     -- 阈值·窗口·表达式；标定前为 {}
    calibrated    boolean NOT NULL DEFAULT false,         -- 是否已用**真实数据**标定
    calibrated_at timestamptz,
    calibrated_by varchar(64),
    evidence      jsonb,                                  -- 标定依据：样本量/分位数/数据来源
    enabled       boolean NOT NULL DEFAULT true,
    rule_version  varchar(16) NOT NULL DEFAULT 'v0.1',
    remark        text,
    CONSTRAINT ck_qr_code_fmt   CHECK (code ~ '^[a-z_]+\.[a-z0-9_]+$'),
    CONSTRAINT ck_qr_layer      CHECK (layer IN ('schema','missing','range','enum','cross','time')),
    CONSTRAINT ck_qr_code_layer CHECK (code LIKE layer || '.%'),
    CONSTRAINT ck_qr_severity   CHECK (severity IN ('block','warn','mark')),
    CONSTRAINT ck_qr_calibrated CHECK (NOT calibrated OR calibrated_at IS NOT NULL)
);
COMMENT ON TABLE quality_rule IS '数据质量规则库（M4 真数据门；calibrated=false 表示阈值尚未用真实数据标定，此时门禁关闭、接口返 503）';
COMMENT ON COLUMN quality_rule.code IS '稳定规则码，前缀即 layer；与 M2 违约分类器共用同一命名空间，两层统计因此可对齐';
COMMENT ON COLUMN quality_rule.evidence IS '标定依据（样本量/分位数/数据来源），供复查「这个阈值凭什么定在这里」';
CREATE INDEX IF NOT EXISTS idx_qr_suite ON quality_rule(suite, enabled);
CREATE INDEX IF NOT EXISTS idx_qr_calib ON quality_rule(calibrated) WHERE calibrated = false;

-- ============================================================================
-- H. 语义与映射（M7 语义中枢）                                    —— v0.2 新增段
-- ============================================================================
--
-- M7 解决的是「真实表格与库内字段**不对应**」：表头名不同、桩号写法不同、单位不同、
-- 编码不同。它的产出物叫 mapping_set，架构文档与 JSON Schema 都已用这个名字，
-- 但在 v0.1 里**库里没有落点** —— M7 的产出无处可存。本段补上。

-- H1. 语义映射集（M7 语义中枢的产出）
--
-- ★ 字段严格对齐已定稿的 JSON Schema（scaffold/contracts/semantic/mapping_set.v0.1.schema.json），
--   不另起炉灶；且把 schema 里的 allOf/if-then（「已确认」必须带确认人）**同时落成库约束** ——
--   因为 M7 的写入可能来自导入脚本而不经 API，schema 未必跑得到，库约束才是兜底。
CREATE TABLE IF NOT EXISTS mapping_set (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    mapping_id    varchar(64) NOT NULL,                   -- 映射集标识，与 JSON Schema 同名同义
    version       integer NOT NULL DEFAULT 1,             -- 同一映射可迭代
    source_kind   varchar(8) NOT NULL,                    -- 表头/桩号/单位/编码（解决哪一类「不对应」）
    source_sample varchar(255),                           -- 样表来源（文件名/表名），便于复查
    source_hash   varchar(64),                            -- 源结构指纹（表头名+顺序），用于复用判定
    target_table  varchar(64),                            -- 落到哪张表
    entries       jsonb NOT NULL,                         -- [{raw,normalized,target_field,unit,transform}]
    status        varchar(8) NOT NULL DEFAULT '候选',      -- 候选/已确认/已废弃
    confirmed_by  varchar(64),                            -- 确认人；status=已确认 时必填
    confirmed_at  timestamptz,
    proposed_by   varchar(16) NOT NULL DEFAULT 'rule',    -- rule/llm/human（谁提出的候选）
    confidence    numeric(4,3),                           -- 整体置信度
    created_at    timestamptz NOT NULL DEFAULT now(),
    remark        text,
    CONSTRAINT ck_ms_kind      CHECK (source_kind IN ('表头','桩号','单位','编码')),
    CONSTRAINT ck_ms_status    CHECK (status IN ('候选','已确认','已废弃')),
    CONSTRAINT ck_ms_confirmed CHECK (status <> '已确认'
                                      OR (confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL)),
    CONSTRAINT ck_ms_proposed  CHECK (proposed_by IN ('rule','llm','human')),
    CONSTRAINT ck_ms_entries   CHECK (jsonb_typeof(entries) = 'array'
                                      AND jsonb_array_length(entries) >= 1),
    CONSTRAINT uq_ms_id_ver    UNIQUE (mapping_id, version)
);
COMMENT ON TABLE mapping_set IS '语义映射集（M7 语义中枢：表头/桩号/单位/编码的对应关系；只有 status=已确认 的可被 M2/M4 引用）';
COMMENT ON COLUMN mapping_set.source_hash IS '源结构指纹（表头名+顺序的哈希）：同一结构第二次上传可直接复用映射，不必再问人';
COMMENT ON COLUMN mapping_set.entries IS '映射条目数组 [{raw 原始写法, normalized 归一化, target_field 库内字段, unit, transform}]';
CREATE INDEX IF NOT EXISTS idx_ms_status ON mapping_set(status, source_kind);
CREATE INDEX IF NOT EXISTS idx_ms_hash ON mapping_set(source_hash);

COMMIT;

-- ============================================================================
-- 时序库建模方案（关系库 DDL 之外，双库架构的时序侧）
-- ============================================================================
-- 高频数据（应变/振动/土压/加速度/挠度/声学特征/气象分钟流）不落关系库，
-- 进独立时序库；关系库仅存档案/字典/事件/聚合/质量，双向按 channel_id 关联。
--
-- 方案A：IoTDB（行业材料备选，端边云协同）
--   树形模型：root.road.<line_code>.<stake>.<install_id>.<quantity>
--   例：root.road.g228.k4635710.s1001.strain
--         root.road.g228.k4635710.s1001.accel_x
--   存储组建议：root.road.g228（按路线分）；TTL：原始 3 级稀释
--   物化视图/降采样：平均值/极值/均方根 1min~1h 特征序列（对应 dict_stat_metric）
--
-- 方案B：TimescaleDB（PostgreSQL 扩展，与关系库同生态）
--   CREATE TABLE ts_sample (
--     channel_id bigint NOT NULL REFERENCES sensor_channel(id),
--     ts         timestamptz NOT NULL,
--     value      double precision NOT NULL,
--     quality    smallint DEFAULT 0
--   );
--   SELECT create_hypertable('ts_sample','ts', chunk_time_interval => INTERVAL '1 day');
--   高频通道（应变 1k~128k Hz）单通道日数据量 8.6 亿点（128kHz）——需按「原始+稀释」双层：
--   原始层保留 1~2 周滚动、特征值层永久（PHM 稀释规则）；
--
-- 通用要点：
--   1. 写入侧：采集站批量乱序写入需去重（时间戳+通道唯一约束/幂等）；
--   2. 查询侧：科研按 断面×时段×物理量 取数，预建宽表视图（pivot）；
--   3. 特征值层永久保留（min/max/mean/rms/std/p95 每通道每分钟~每小时）；
--   4. 秒/毫秒级高频与分钟级气象分开序列组，避免压缩率互相拖累；
--   5. 时序库与关系库一致性：批次号（data_import_batch.id）贯穿两侧，便于对账。
-- ============================================================================
