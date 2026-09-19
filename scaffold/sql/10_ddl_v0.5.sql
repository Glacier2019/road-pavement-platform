-- ============================================================================
-- 路面性能数据库（Road Pavement Performance Database）— DDL v0.5
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
--   v0.2          32 表：v0.1 全部保留（未改一列）＋ 新增
--                       G5 quality_rule  数据质量规则库（M4 真数据门的规则定义与标定状态）
--                       H1 mapping_set    语义映射集（M7 语义中枢的产出落点）
--                 依据：契约变更工单 #1（M4/M7 从机械层进入业务层的前置条件）
--                 兼容：纯新增，无破坏性变更；回滚 = DROP TABLE quality_rule, mapping_set;
--   v0.4          53 表：v0.3 全部保留（未改一列）＋ 新开 I 节「设计控制参数（.CTR）」9 张
--                       I1 slope_segment          路基边坡分段（.CTR 的 ZTFBP/YTFBP/ZWFBP/YWFBP）
--                       I2 ditch_segment          边沟/排水沟断面（ZBGXS/YBGXS/ZPSGXS/YPSGXS）
--                       I3 standard_cross_section 路基标准断面（ZBZDM/YBZDM）★A17 的独立校验源
--                       I4 roadbed_trench         路槽分段（ZLCSD/YLCSD）
--                       I5 structure_control      桥涵构造物（QHSJ 桥/HDSJ 涵洞/SUIDAO 隧道）
--                       I6 earthwork_composition  挖方土石成份（TFFD）
--                       I7 land_use_width         附加用地宽度（ZYDK/YYDK）
--                       I8 extra_fill             超填/清除表土（ZCHT/YCHT/DCHT/QCHBT）
--                       I9 design_control_text    地质概况/水准点（DZGK/SHUIZHUNDIAN）
--                 依据：纬地教程 v5.88 §13.10（18 类格式 / 36 个关键字）。本工程 .CTR 实测
--                       36 个关键字：19 个有数据、17 个为空。本版 9 张表覆盖**有数据的全部**；
--                       17 个空关键字只在 contracts/design-import/README.md 里登记，不建表。
--                       其中 ZDMDG.DAT 说明书全文未定义、20 行数据经比对既不等于 .DMX 地面线
--                       （46~124）也不等于 .ZDM 设计标高（54~84），经确认**跳过**，仅登记。
--                 为什么单开 I 节：A 节是「空间与档案」（几何实体本身），本节是**设计控制参数**
--                       （边坡/边沟/路槽/构造物等"怎么修"的控制量）。两者都由 .STA 桩号寻址，
--                       但性质不同。
--                 兼容：纯新增，无破坏性变更。回滚 = DROP TABLE slope_segment, ditch_segment,
--                       standard_cross_section, roadbed_trench, structure_control,
--                       earthwork_composition, land_use_width, extra_fill, design_control_text;
--   v0.5（本版）  55 表：v0.4 全部保留（未改一列）＋ 新开 J 节「逐桩土方与路基设计断面」2 张
--                       J1 earthwork_section        逐桩土方断面（.tf，**74 列**）
--                       J2 roadbed_design_point     逐桩路基设计断面（.lj，**24 列**）
--                 依据：纬地教程 v5.88 §13.9（土方数据文件）、§13.6（路基设计中间数据）。
--                 ★★ 建表原则（用户明确要求）：**照数据文件的样式，好追溯** ——
--                       文件里有的列全建（.tf 74 列里 44 列本工程全 0 也建）、
--                       列名用文件自带表头逐条译（.tf 是唯一自带完整表头的纬地文件，
--                       故映射没有猜的成分）、与 .DMX/.ZDM 重复的量照样存。
--                 ★★ 两处说明书与实测对不上（都用数据判过）：
--                       (1) .tf 第 2/3 列：说明书正文写「[桩号][填方][挖方]」，
--                           文件自带表头写「[桩号][挖方][填方]」。用 .lj 的
--                           设计标高 vs 地面标高独立判填挖，比 332 行：
--                           按文件表头 326/332 吻合、按说明书 6/332。**文件表头对**。
--                       (2) .lj 列数：说明书说 20 项，实测 24 列；且说明书给的
--                           宽度列序把「中分带」与「路面」写反；第 9、11 列在说明书里
--                           没有对应项（本工程恒 0），不猜，按位置命名标待考。
--                 为什么这两张能用 station_id 外键：它们是**逐桩**表，332 行正好是
--                       .STA 桩号序列的 332 个。I 节（.CTR）不能，因为 .CTR 的
--                       分段桩号**不是** .STA 桩号序列的子集。
--                 兼容：纯新增，无破坏性变更。回滚 = DROP TABLE earthwork_section,
--                       roadbed_design_point;
--                 迁移：已存在的库执行 sql/86_migrate_v04_ctr.sql（幂等）
--                 待办：第二批（横断面/路基土方/构造物）顺延至 v0.5
--                       说明：原 v0.3 待办写"随 v0.4"，但 v0.4 位次让给了 .CTR（教程 §13.10）。
--                             其中「横断面地面线（.HDM）」经确认**不做**（2026-xx-xx 用户指示）。
--   v0.3          44 表：v0.2 全部保留 ＋ 第一批 10 张新表（GE 域完整化）
--                       ＋ A16 superelev_transition 超高过渡（原列在 v0.4 待办，提前落地）
--                       ＋ A17 roadbed_width 路幅宽度（原列在 v0.4 待办，提前落地）
--                       A0  design_project                          设计项目
--                       A7  design_file                             设计文件台账（含桩号覆盖区间）
--                       A8  section_design_attr                     路段设计属性（.PRJ 分段）
--                       A9  station_sequence    ★全线桩号基准（一等实体，.STA）
--                       A10 station_equation    ★断链（长链/短链）
--                       A11 alignment_pi / A12 alignment_element    平面线形（.JD/.PM）
--                       A13 profile_grade_point / A14 profile_ground_point  纵断面（.ZDM/.DMX）
--                       A15 geometry_point      ★逐桩号 κ/G/E 函数库（七文件融合）
--                       A16 superelev_transition 超高过渡变化点（.SUP）
--                       A17 roadbed_width       路幅宽度（.WID）★设计输入，一行一个桩号
--                 改名：stake_* → station_*（9 列 + 2 索引）；road_section 加 design_project_id
--                 依据：契约变更工单 #2（导师指示：stake 是"物理标桩"，station 才是"桩号值"）
--                 兼容：⚠️ 本次为破坏性改名。v02_compat_* 视图保留旧列名一版（只读）
--                 回滚：ALTER TABLE ... RENAME COLUMN 反向执行；或 git revert 工单 #2 提交
--                 待办：第二批 9 张（L6–L9：横断面/路基土方/构造物）→ 已顺延至 v0.5
--                       说明：原为 11 张，"超高"已按教程 §13.5 提前落到本版 A16，
--                             "路幅宽度"已按教程 §13.4 提前落到本版 A17
--                       为什么路幅宽度要提前：section_design_attr.roadway_width_m 是**标量**，
--                             而路幅宽度**本来就随桩号变**（加宽/匝道/交叉口/变速车道），
--                             一个标量装不下分段变化。按「存设计输入、导出派生量」：
--                             .WID 是设计输入（A17），roadway_width_m 是由它导出的派生标量
--                       修订：A16/A17 两表的**键都改为桩号**（原 A16 用 transition_seq、
--                             A17 用 (side, interval_seq) 且存 start/end 区间）。
--                             起因：逐桩数据表应当按桩号寻址（GE 域其余表皆如此，
--                             A9 注释即写明"其余逐桩数据表以 station_id FK 锚定"），
--                             而区间寻址等于在 GE 域另立一套寻址方式，且组内两行
--                             本来就可不同（列 4），折叠成区间必然丢一个值。
--                 修订：桩号列精度 numeric(10,3)/numeric(12,3) → numeric(12,6)（14 列）
--                       原因：.STA 实测 332 个桩号中 81 个非 20 m 等距点（66 种间距），
--                             最短间距仅 0.083 m —— 1659.917 与 1660.000 在 km 3 位小数下
--                             同为 1.660，直接撞 UNIQUE(section_id, station_local_km)。
--                             km 6 位小数 = 毫米级，足以区分源文件（米制 3 位小数）的全部取值。
--                       说明：DDL 早已预留加桩概念（station_type='jiazi'、is_integer_station），
--                             但原精度表达不了它——预留了字段，没预留分辨率。
--                       不变：station_interval_m numeric(8,2)（间距用米，语义不同，保持不动）
--                 修订：A11/A12 平面几何两表改为「单元链为真源、交点为派生」（工单 #3）
--                       起因：审阅发现 A11 的列注释把**缓和曲线参数 A** 标成"切线长"、
--                             把**缓和曲线长 Ls** 标成"转角"。追问后确认问题不在注释，
--                             而在**这两列本来就不该这么存**——A = √(R·Ls) 是纯导出量，
--                             冗余值可以被贴错标签而无人察觉（因为它"本来就有个合理的数"）。
--                       实测（G228 滨海大道试验段，33 单元 → 8 交点）：
--                         · A11 的全部字段可由 A12 的单元链推出，交点坐标偏差 3×10⁻⁸ m，
--                           转角/切线长/交点桩号/A 逐位相同 → A11 改判为**派生表**，
--                           导入器只从单元链推导，.JD 文件降为**验算**（独立来源的第二意见）
--                         · A12 缺失原始量、且有一列表达不了自己的对象：
--                           新增 center_x/center_y（圆心）、radius_start_m/radius_end_m、
--                           end_azimuth_deg、section_id、length_m(生成列)；
--                           **删除 curvature_1pm** —— 缓和曲线单元内 κ 由 0 连续变到 1/R，
--                           不是常数，单列必然失真（逐桩的 A15.curvature_1pm 则正确，不动）
--                         · A11.spiral_a1/a2 由手填列改为 GENERATED（sqrt(R·Ls)），
--                           写出去了（可查）、但结构上不可能与 R、Ls 不一致
--                         · A11 新增 spiral_ls1_m/spiral_ls2_m（原始量，原先只存了导出的 A）、
--                           tangent_len2_m、arc_len_m、prev_tangent_len_m、external_m
--                       外距算法：**不用**教科书的 (R+ΔR)/cos(α/2)−R（级数近似，
--                         实测对 PI8 差 1.24 mm），改用圆心＋角平分线的精确几何距离（差 6×10⁻⁹ m）
--                       兼容：⚠️ 破坏性。curvature_1pm 删除、spiral_a1/a2 变生成列、
--                             A12 加 NOT NULL/UNIQUE(section_id, element_seq)。
--                             库尚无真实数据（GE 域为空表运行），重建即可；无需迁移脚本。
--                       回滚：git revert 工单 #3；live 库删卷重建
--                       未做（留给 v0.4 定）：x_coord/y_coord 与 start_x/start_y 的命名统一
--                 修订：按《纬地道路辅助设计系统教程 v5.88》第十三章校正 GE 域超高相关实体
--                       起因：读教程 13.4/13.5 逐列定义，发现 A15 的两列**与源文件对不上**——
--                         · 教程 13.4：*.wid 的 7 列是「起终点桩号、中央分隔带宽度、半侧路面宽度、
--                           有无附加车道标识、硬路肩宽度、土路肩宽度、附加车道项目文件名」
--                           —— **全是宽度，没有坡度**。而 A15 却把 *.wid 记作 crossfall_pct
--                           的来源。该列自 v0.3 起就没有真实来源，属**来源注错**。
--                         · 教程 13.5：*.sup 每行 7 项「前三项与后三项绕第四项呈对称排列」，
--                           依次为 左侧土路肩横坡、左侧硬路肩横坡、左侧行车道（路面）横坡、
--                           **桩号**、右侧行车道横坡、右侧硬路肩横坡、右侧土路肩横坡。
--                           —— 超高是**左右各一个行车道横坡**，A15 用**单列** superelev_pct
--                           存，左右必有一侧丢失；且六个量在教程里统称"横坡"，
--                           单列 crossfall_pct 的语义无法确定。
--                         · 教程 13.5 同时定义 9999 = "可以忽略此数据"，横坡渐变至此位置时
--                           系统**跳过该列的计算继续过渡** —— 是"此点不约束该列"，
--                           既不是"沿用上值"也不是 NULL（.SUP 每格非数即 9999，故 NULL 无歧义）。
--                       改法（经确认选"甲"）：
--                         · 新增 A16 superelev_transition —— **一行一个超高过渡变化点**，
--                           忠实保存 .SUP 原始六列与 9999（记 NULL）。实测本工程 76 个变化点，
--                           而 A9 逐桩是 332 个 —— 两者粒度不同，故分表。
--                         · A15 的 superelev_pct/crossfall_pct 两列**删除**，改为教程原样的
--                           六列（左/右 × 土路肩/硬路肩/行车道），存**插值到该桩号**后的结果。
--                         · A13 profile_grade_point 补两列 offset_station_km/offset_elev_m
--                           —— 教程 13.3：.ZDM 每行第 4、5 项是「标高错台位置的桩号及错台
--                           的标高差值（向上为正、向下为负，单位米）」，一般公路主线输 0。
--                           原表**静默丢弃**了这两列。
--                       依据：纬地教程 v5.88 §13.3/§13.4/§13.5（本工程实测文件版本 5.83/6.00，
--                             教程为 5.8 代；*.wid 的 6.00 格式教程未覆盖，适配器另注依据）
--                       兼容：⚠️ 破坏性（删 2 列、加 6 列、加 1 表）。
--                             ⚠ **实库已有真实数据**（实测：station_sequence 332 行、
--                               alignment_element 33、alignment_pi 8、profile_grade_point 12、
--                               profile_ground_point 332），故**不能删卷重建**——
--                               要么 ALTER 迁移，要么重跑幂等导入。
--                             （此处初稿曾写"GE 域尚无真实数据、重建即可"，是凭记忆写的，
--                               实测推翻了它。删 2 列对已导入的 geometry_point 无影响：
--                               该表实测 0 行——但 A13/A15 的其余列有数据。）
--                       回滚：git revert 本次提交；live 库删卷重建
-- ============================================================================

BEGIN;

-- PostGIS 扩展。★ 本版 DDL 自身**不用** PostGIS 类型（坐标一律 numeric，便于跨库移植），
--   但实库（rp-pg）里它是装着的、而全新构建没有——两者因此不一致。
--   这里显式声明，使「全新构建 ≡ 实库」成立，也为后续空间功能留好接口。
--   若将来确定不做空间分析，可换成 postgres:16 镜像并删掉本行。
CREATE EXTENSION IF NOT EXISTS postgis;

-- ######################## A. 空间与档案（静态基础数据） ########################

-- ---- A0 / A7. 设计项目与设计文件台账（GE 域完整化 · 纬地文件接入）—— v0.3 新增 ----
-- 注意：本段物理位置在 A1 之前 —— 因 road_section 需 FK 引用 design_project，
--       建表顺序必须先于路段表（编号顺序≠文件顺序，此处以 FK 依赖为准）。
-- 来源：纬地(HintCAD)设计工程 .PRJ 总项目文件〔项目设置〕/〔文件名〕两段
-- 依据：契约变更工单 #2

-- A0. 设计项目 design_project
CREATE TABLE IF NOT EXISTS design_project (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_uid        varchar(64) UNIQUE,                -- .PRJ 项目ID（UUID）
    project_name       varchar(128) NOT NULL,             -- 项目名（含设计人/院校）
    project_type       varchar(64),                       -- 公路主线 / 互通式立体交叉 …
    station_interval_m numeric(8,2),                       -- 桩号间隔 m（毕设工程＝20）
    earthwork_method   varchar(64),                       -- 土方计算方式（平均断面法…）
    designer           varchar(128),                      -- 设计人
    design_org         varchar(128),                      -- 设计单位
    design_stage       varchar(64),                       -- 设计阶段（初步设计/施工图…）
    source_file        varchar(255),                      -- 来源 .PRJ 文件名
    remark             text
);
COMMENT ON TABLE design_project IS '设计项目台账（设计期数据血缘起点）；一个项目对应一套纬地工程文件';
COMMENT ON COLUMN design_project.station_interval_m IS '桩号间隔，决定逐桩数据的默认密度';

-- A7. 设计文件台账 design_file
CREATE TABLE IF NOT EXISTS design_file (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    design_project_id        bigint NOT NULL REFERENCES design_project(id),
    file_kind_code           varchar(16) NOT NULL,        -- .PRJ〔文件名〕键号（101/102/…）
    file_kind_name           varchar(64) NOT NULL,        -- 文件类型名（平面线形文件(*.PM)…）
    file_name                varchar(255) NOT NULL,       -- 实际文件名
    rel_path                 varchar(512),                -- 相对工程目录的路径
    coverage_from_station_km numeric(12,6),               -- ★该文件覆盖起点桩号
    coverage_to_station_km   numeric(12,6),               -- ★该文件覆盖终点桩号
    parse_status             varchar(16) DEFAULT 'pending', -- ok 明文可解析/blocked 二进制或专有/pending 未验
    parse_note               text,                        -- 不可解析原因（zlib 二进制结构体 / 无已知压缩魔数 …）
    remark                   text,
    UNIQUE (design_project_id, file_kind_code)
);
COMMENT ON TABLE design_file IS '设计文件台账（.PRJ〔文件名〕段）；★coverage_* 使"某文件覆盖哪一段桩号"成为可查询事实——实测 .WID 只到 5701.461 而全线 5805.421，末段 104m 无路幅数据';
COMMENT ON COLUMN design_file.parse_status IS 'ok=明文可解析(14个) / blocked=二进制或专有(5个) / pending；Access(.TSF) 需 mdbtools';


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
    design_project_id bigint REFERENCES design_project(id),  -- v0.3 新增：设计项目血缘
    section_name  varchar(128) NOT NULL,                 -- 路段名称（滨海大通道试验段）
    start_station_text   varchar(32),                           -- 起点桩号（K4635+000）
    end_station_text     varchar(32),                           -- 终点桩号（K4654+701）
    start_station_km      numeric(12,6),                         -- 起点数值桩号 4635.000
    end_station_km        numeric(12,6),                         -- 终点数值桩号
    length_m      numeric(10,1),                         -- 路段长度 m
    direction     varchar(16),                           -- 方向（上行/下行）
    pavement_type varchar(64),                           -- 路面结构类型（沥青混凝土）
    climate_zone  varchar(64),                           -- 气候分区（南方湿热滨海）
    remark        text
);
COMMENT ON TABLE  road_section IS '路段档案（路线→路段）';
COMMENT ON COLUMN road_section.start_station_km IS '数值桩号便于区间排序检索';
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
    station_text    varchar(32) NOT NULL,                  -- 桩号 K4635+710
    station_km      numeric(12,6) NOT NULL,                -- 4635.710
    lane_no       smallint,                              -- 车道序号（1=最外侧…）
    lon           numeric(10,7),                         -- 经度（CGCS2000/WGS84）
    lat           numeric(10,7),                         -- 纬度
    purpose       varchar(128),                          -- 用途（轴载调查/结构响应监测/应变断面）
    install_date  date,                                  -- 埋设/建成日期（2025 年埋设）
    status        varchar(16) DEFAULT 'active',          -- active/archived
    remark        text
);
COMMENT ON TABLE monitor_cross_section IS '监测断面（内观+表面监测的空间锚点）';
CREATE INDEX IF NOT EXISTS idx_cs_section_station ON monitor_cross_section(section_id, station_km);

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


-- ---- A8–A15. 道路几何设计数据（GE 域完整化 · 纬地文件接入）—— v0.3 新增 ----
-- 来源：纬地(HintCAD)设计工程明文文件（.STA/.PM/.JD/.ZDM/.DMX/.PRJ/.SUP/.WID/.LJ/.TF）
-- 依据：契约变更工单 #2 ／ 设计：output/GE域完整化设计-基于纬地设计文件.md
-- 说明：桩号（station）为一等实体 —— station_sequence 是全线基准，
--       逐桩数据表以 FK 锚定其上，锚定关系成为数据库约束而非文档约定。

-- A8. 路段设计属性（来自 .PRJ〔项目分段〕：公路等级/计算车速/路幅/横坡/超高/加宽）
CREATE TABLE IF NOT EXISTS section_design_attr (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id               bigint NOT NULL UNIQUE REFERENCES road_section(id),
    road_grade               varchar(32),                 -- 公路等级（二级公路）
    design_speed_kmh         smallint,                    -- 计算车速 km/h（60）
    cross_section_form       varchar(64),                 -- 横断面形式（2车道）
    roadway_width_m          numeric(6,2),                -- 路幅宽度 m（10.000）
    carriageway_crossfall_pct numeric(4,2),               -- 行车道横坡 %（2.0）
    shoulder_crossfall_pct   numeric(4,2),                -- 土路肩横坡 %（3.0）
    median_width_m           numeric(6,2),                -- 中间带宽度 m（0.00）
    max_superelev_pct        numeric(4,2),                -- 最大超高 %（8.0）
    superelev_rotate_mode    varchar(64),                 -- 超高旋转方式（绕曲线内侧行车道边缘旋转）
    superelev_gradient_mode  varchar(64),                 -- 超高渐变方式（线性）
    widening_mode            varchar(64),                 -- 加宽类型（不设置加宽）
    widening_gradient_mode   varchar(64),                 -- 加宽渐变方式
    source_file              varchar(255),
    remark                   text
);
COMMENT ON TABLE section_design_attr IS '路段设计属性（.PRJ〔项目分段〕）；一改线形即新增冗余列，故独立成表而非并入 road_section';

-- A9. 桩号序列 ★全线桩号基准（一等实体）
CREATE TABLE IF NOT EXISTS station_sequence (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id          bigint NOT NULL REFERENCES road_section(id),
    station_seq_no      integer NOT NULL,                 -- .STA 原始编号
    station_local_km    numeric(12,6) NOT NULL,           -- 相对桩号（0 起，设计口径）
    station_absolute_km numeric(12,6),                    -- 绝对桩号（路网统一口径，如 4635.710）
    station_text        varchar(32) NOT NULL,             -- K 格式文本（K4+635.000 / K4635+710）
    station_type        varchar(16) DEFAULT 'integer',    -- integer 整桩/jiazi 加桩/equation 断链点/endpoint 起终点
    is_integer_station  boolean DEFAULT true,             -- 是否整桩（20m 整桩 vs 加桩）
    remark              text,
    UNIQUE (section_id, station_local_km)
);
COMMENT ON TABLE station_sequence IS '桩号序列＝全线桩号基准（一等实体）；来源 .STA 逐桩号序列。其余逐桩数据表以 station_id FK 锚定本表';
COMMENT ON COLUMN station_sequence.station_local_km IS '相对桩号：设计口径，0 起（纬地工程 0.000→5805.421）';
COMMENT ON COLUMN station_sequence.station_absolute_km IS '绝对桩号：路网统一口径（G228 试验段 K4635+000 系）。★相对/绝对双列显式物化，不存单个 offset 由读时计算——有断链时线性假设不成立';
CREATE INDEX IF NOT EXISTS idx_station_section_local ON station_sequence(section_id, station_local_km);
CREATE INDEX IF NOT EXISTS idx_station_absolute      ON station_sequence(station_absolute_km);

-- A10. 断链（长链/短链）★一等实体的必要配套
CREATE TABLE IF NOT EXISTS station_equation (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id          bigint NOT NULL REFERENCES road_section(id),
    equation_station_km numeric(12,6) NOT NULL,           -- 断链点（相对桩号）
    station_before_km   numeric(12,6) NOT NULL,           -- 断链前桩号（绝对）
    station_after_km    numeric(12,6) NOT NULL,           -- 断链后桩号（绝对）
    equation_type       varchar(16) NOT NULL,             -- long 长链 / short 短链
    equation_len_m      numeric(10,3),                    -- 链长 m
    station_text        varchar(32),                      -- 断链标注（K10+000=K10+050）
    remark              text
);
COMMENT ON TABLE station_equation IS '断链（长链/短链）：同一物理位置有两套合法桩号。★缺此表则里程统计静默多算/少算、新旧数据按桩号 join 在断链处错位，且不报错。本毕设工程无断链（空表运行），G228 复测修正必用';

-- A11. 平面交点（来自 .JD）★**派生表**：全部字段可由 A12 的单元链推出（实测偏差 3×10⁻⁸ m）
--      折点是线的摘要 —— 两条相邻切线求交即得。故 .JD 只作**验算**（独立来源的第二意见），
--      不作数据入口：一个"看起来也有值"的入口，只会多一处能悄悄写歪的地方。
CREATE TABLE IF NOT EXISTS alignment_pi (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id         bigint NOT NULL REFERENCES road_section(id),
    pi_seq             smallint NOT NULL,                 -- 交点序号（.JD 交点个数=10）
    pi_type            varchar(16) NOT NULL,              -- QD 起点 / ZD 终点 / JD 交点
                                                          --   沿用源文件写法（.JD 里就写 QD/ZD）。
                                                          --   同域 element_type 用英文，此处例外是**故意的**
    x_coord            numeric(16,6),                     -- 交点大地坐标 X（两切线求交，派生）
    y_coord            numeric(16,6),                     -- 交点大地坐标 Y
    radius_m           numeric(12,4),                     -- 圆曲线半径（450）；派生自 A12 的 circular 单元
    spiral_ls1_m       numeric(12,4),                     -- 入口缓和曲线长 Ls1（60）　★原始量
    spiral_ls2_m       numeric(12,4),                     -- 出口缓和曲线长 Ls2（60）　★原始量
    spiral_a1          numeric(14,8) GENERATED ALWAYS AS (sqrt(radius_m * spiral_ls1_m)) STORED,
                                                          -- 缓和曲线参数 A1 = √(R·Ls1)，**导出量**：
                                                          --   原为手填列，可被写成任何值（注释就曾把它
                                                          --   误标成"切线长(164.31676725)"）。改成生成列后
                                                          --   结构上不可能与 R、Ls 不一致。
    spiral_a2          numeric(14,8) GENERATED ALWAYS AS (sqrt(radius_m * spiral_ls2_m)) STORED,
    prev_tangent_len_m numeric(12,4),                     -- 前段直线长（PI2 = 674.493）。⚠ 源文件把它与
                                                          --   特征点桩号放在同一列，极易误读为桩号
    tangent_len_m      numeric(12,4),                     -- 切线长 = 交点桩号 − ZH 桩号（117.54242652）
                                                          --   ⚠ 原注释误标为 164.31676725（那是 A）
    tangent_len2_m     numeric(12,4),                     -- 出口切线长
    arc_len_m          numeric(12,4),                     -- 圆曲线弧长（112.80867934）
    curve_len_m        numeric(12,4),                     -- 曲线总长 = 2·Ls + 弧长（232.80867934）
    deflection_deg     numeric(10,6),                     -- 转角（+22.002684）
                                                          --   ⚠ 原注释误标为 -60.0（那是 Ls）
    external_m         numeric(12,6),                     -- 外距（8.76412020）。★必须用**精确几何**：
                                                          --   圆心+角平分线量距离。教科书的
                                                          --   (R+ΔR)/cos(α/2)−R 是级数近似，实测差 1.24 mm
    remark             text,
    UNIQUE (section_id, pi_seq)
);
COMMENT ON TABLE alignment_pi IS '平面交点（.JD）★派生表：全部字段可由 alignment_element 的单元链推出（实测偏差 3×10⁻⁸ m），**不得手工填写**，.JD 文件只作验算。切线长与转角两条列注释原先把缓和曲线参数 A 和缓和曲线长 Ls 张冠李戴，已修正';

-- A12. 平面线形单元（来自 .PM：逐段方位角/曲率）★平面几何的**真源表**
--      A11 的交点、A15 的 κ(s) 都由本表推出；本表不是任何东西的摘要。
CREATE TABLE IF NOT EXISTS alignment_element (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id       bigint NOT NULL REFERENCES road_section(id),
                                                          -- 新增：末端直线不属于任何交点，
                                                          --   原先只能靠 pi_id 反推路段，会够不着
    pi_id            bigint REFERENCES alignment_pi(id),  -- 可空：引道/末端直线没有对应交点
    element_seq      smallint NOT NULL,                   -- 线形单元序号（.PM 共 33 段）
    element_type     varchar(24) NOT NULL,                -- line 直线 / circular 圆曲线 / transition 缓和曲线
    start_station_km numeric(12,6) NOT NULL,              -- 单元起点桩号
    end_station_km   numeric(12,6) NOT NULL,              -- 单元终点桩号
    length_m         numeric(12,6) GENERATED ALWAYS AS
                     ((end_station_km - start_station_km) * 1000) STORED,
                                                          -- 单元长 ≡ 桩号差，**导出量**，不许手填
    start_x          numeric(16,6),
    start_y          numeric(16,6),
    end_x            numeric(16,6),
    end_y            numeric(16,6),
    center_x         numeric(16,6),                       -- 圆心（新增）。直线/切线端为 NULL
    center_y         numeric(16,6),
    azimuth_deg      numeric(10,6),                       -- 起点方位角
    end_azimuth_deg  numeric(10,6),                       -- 终点方位角（新增；直线单元 = 起点方位角）
    radius_start_m   numeric(12,4),                       -- 起点曲率半径（新增）。NULL = ∞，即直线端
    radius_end_m     numeric(12,4),                       -- 终点曲率半径（新增）
    remark           text,
    UNIQUE (section_id, element_seq)
);
COMMENT ON TABLE alignment_element IS '平面线形单元（.PM）★平面几何真源表。原 curvature_1pm 单列已删：缓和曲线单元内 κ 从 0 连续变到 1/R，**非常数**，一列必然失真；改由 radius_start_m/radius_end_m 表达。A11 交点与 A15 κ(s) 皆由本表推出';
CREATE INDEX IF NOT EXISTS idx_alignment_elem_station ON alignment_element(start_station_km, end_station_km);
CREATE INDEX IF NOT EXISTS idx_alignment_elem_pi ON alignment_element(pi_id);

-- A13. 纵断面变坡点（来自 .ZDM：桩号/高程/竖曲线半径/坡度）
CREATE TABLE IF NOT EXISTS profile_grade_point (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id              bigint NOT NULL REFERENCES road_section(id),
    vpi_seq                 smallint NOT NULL,            -- 变坡点序号（.ZDM 共 12 个）
    station_km              numeric(12,6) NOT NULL,       -- 变坡点桩号（300.000）
    elevation_m             numeric(10,4),                -- 变坡点高程（58.822）
    vertical_curve_radius_m numeric(12,4),                -- 竖曲线半径（6000）
    grade_in_pct            numeric(6,3),                 -- 前坡坡度 %
    grade_out_pct           numeric(6,3),                 -- 后坡坡度 %
    grade_len_m             numeric(10,3),                -- 坡长 m
    offset_station_km       numeric(12,6),                -- 错台位置桩号（.ZDM 第4项；一般公路主线为 0）
    offset_elev_m           numeric(8,4),                 -- 错台高差 m（正=向上错开，负=向下；主线为 0）
    remark                  text,
    UNIQUE (section_id, vpi_seq)
);
COMMENT ON TABLE profile_grade_point IS '纵断面变坡点（.ZDM）；G(s) 纵坡函数由此表竖曲线推导';
COMMENT ON COLUMN profile_grade_point.offset_station_km IS '标高错台位置桩号（教程 §13.3：.ZDM 每行第 4 项）。错台是互通立交匝道上出现的标高突变；一般公路主线此列为 0';
COMMENT ON COLUMN profile_grade_point.offset_elev_m IS '标高错台高差 m（教程 §13.3：.ZDM 每行第 5 项）。向上错开为正、向下为负；一般公路主线此列为 0。本工程（毕设，二级公路主线）12 个变坡点全为 0';
CREATE INDEX IF NOT EXISTS idx_grade_point_station ON profile_grade_point(section_id, station_km);

-- A14. 纵断面地面线（来自 .DMX：逐桩地面高程）
CREATE TABLE IF NOT EXISTS profile_ground_point (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station_id    bigint NOT NULL UNIQUE REFERENCES station_sequence(id),
    ground_elev_m numeric(10,4) NOT NULL,                 -- 地面高程 m
    remark        text
);
COMMENT ON TABLE profile_ground_point IS '纵断面地面线（.DMX 逐桩地面高程）；与 design 高程对比得填挖深度';
CREATE INDEX IF NOT EXISTS idx_profile_ground_station ON profile_ground_point(station_id);

-- A15. 逐桩号线形 ★核心：κ(s)/G(s)/E(s) 函数库（课题甲/乙共同消费接口）
--      ★**派生缓存**：全部由 A12（κ/坐标/方位角）＋ A13/A14（高程）逐桩算出。
--      之所以"存"而不是"每次算"：缓和曲线要数值积分，逐桩重算代价高——这是合理的缓存，
--      不是冗余。但有两条要求：① 必须**可重建**（导入器要能一键重算，并断言重算结果一致）；
--      ② 必须与 A12 同批次生成，不允许只改 A12 不改本表。
--      ⚠ 注意与 A12 的差别：本表的 curvature_1pm 是**逐桩**单值，正确；
--        A12 原先那个 curvature_1pm 是**逐单元**单值，对缓和曲线是错的，故已删。
CREATE TABLE IF NOT EXISTS geometry_point (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    station_id     bigint NOT NULL UNIQUE REFERENCES station_sequence(id),  -- ★锚在桩号基准上
    x_coord        numeric(16,6),                         -- 平面坐标 X（.PM）
    y_coord        numeric(16,6),                         -- 平面坐标 Y（.PM）
    azimuth_deg    numeric(10,6),                         -- 方位角（.PM）
    curvature_1pm  numeric(14,10),                        -- ★曲率 κ 1/m（.PM＋.JD 推导）
    design_elev_m  numeric(10,4),                         -- 设计高程（.ZDM 竖曲线）
    ground_elev_m  numeric(10,4),                         -- 地面高程（.DMX）
    grade_pct      numeric(6,3),                          -- ★纵坡 G %（.ZDM 推导）
    h_radius_m     numeric(12,4),                         -- 平曲线半径（.JD）
    v_radius_m     numeric(12,4),                         -- 竖曲线半径（.ZDM）
    -- ★超高横坡六列 —— 教程 §13.5 对 .SUP 的**原样**命名，左右对称，缺一不可。
    --   原先的 superelev_pct（单列）存不下"左右各一个行车道横坡"，
    --   crossfall_pct 则把 .WID（只有宽度、没有坡度）错记成来源，两列均已删除。
    --   本表存的是**插值到该桩号**后的结果；原始过渡变化点见 A16 superelev_transition。
    earth_shoulder_left_pct  numeric(5,2),                -- 左侧土路肩横坡 %
    hard_shoulder_left_pct   numeric(5,2),                -- 左侧硬路肩横坡 %
    lane_left_pct            numeric(5,2),                -- ★左侧行车道（路面）横坡 % = 左超高
    lane_right_pct           numeric(5,2),                -- ★右侧行车道（路面）横坡 % = 右超高
    hard_shoulder_right_pct  numeric(5,2),                -- 右侧硬路肩横坡 %
    earth_shoulder_right_pct numeric(5,2),                -- 右侧土路肩横坡 %
    remark         text
);
COMMENT ON TABLE geometry_point IS '逐桩号线形＝κ(s)/G(s)/E(s) 函数库（课题甲/乙共同消费接口）；κ 来自 .PM+.JD，G 来自 .ZDM，E 来自 .SUP（经 A16 过渡变化点插值到逐桩），三者对齐到 station_sequence 同一基准。对应 ASAM OpenDRIVE 的 s = station_absolute_km × 1000（米）';
COMMENT ON COLUMN geometry_point.lane_left_pct IS '左侧行车道（路面）横坡 %，即左半幅超高。教程 §13.5 定义 .SUP 第 3 项';
COMMENT ON COLUMN geometry_point.lane_right_pct IS '右侧行车道（路面）横坡 %，即右半幅超高。教程 §13.5 定义 .SUP 第 5 项。上坡路段左右同号，超高段左右异号（单向横坡）';
CREATE INDEX IF NOT EXISTS idx_geometry_curvature ON geometry_point(curvature_1pm);

-- A16. 超高过渡 ★原始设计输入（来自 .SUP：一行一个过渡变化点）
--      依据：纬地教程 v5.88 §13.5 ——「每一行前三项数据与后三项数据绕第四项数据呈对称位置
--      排列。分别为 左侧土路肩的横坡值、左侧硬路肩的横坡值、左侧行车道（路面）的横坡值、
--      桩号、右侧行车道横坡值、右侧硬路肩的横坡值、右侧土路肩横坡值。其中数据 9999 表示
--      **可以忽略此数据**，横坡渐变至此位置时，系统**跳过此数据的计算继续进行横坡的超高渐变**。」
--      ★9999 → NULL：.SUP 每格非数即 9999，故 NULL 与"缺值"无歧义，正是"此点不约束该列"。
--      ★与 A15 的分工：本表是**设计输入**（变化点，实测 76 行），A15 是**派生结果**（逐桩，332 行）。
--        粒度不同（76 ≠ 332，且本表桩号只有 34 个落在逐桩上），故必须分表——
--        合成一张会把"过渡过程"压没，且 9999 的"此处不约束"信息无法保留。
--      来源文件：纬地 *.sup（本工程实测 magic = HINTCAD5.83_SUP_SHUJU，版本 5.83）
CREATE TABLE IF NOT EXISTS superelev_transition (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id               bigint NOT NULL REFERENCES road_section(id),
    transition_seq           smallint NOT NULL,           -- 过渡变化点序号（.SUP 行序，从 1 起）
    station_km               numeric(12,6) NOT NULL,      -- 桩号（.SUP 第 4 项，本工程 76 个）
    earth_shoulder_left_pct  numeric(5,2),                -- 左侧土路肩横坡 %（9999 → NULL）
    hard_shoulder_left_pct   numeric(5,2),                -- 左侧硬路肩横坡 %（9999 → NULL）
    lane_left_pct            numeric(5,2),                -- 左侧行车道（路面）横坡 %（9999 → NULL）
    lane_right_pct           numeric(5,2),                -- 右侧行车道（路面）横坡 %（9999 → NULL）
    hard_shoulder_right_pct  numeric(5,2),                -- 右侧硬路肩横坡 %（9999 → NULL）
    earth_shoulder_right_pct numeric(5,2),                -- 右侧土路肩横坡 %（9999 → NULL）
    remark                   text,
    -- ★键是**桩号**，不是行序：过渡变化点就是"在这个桩号上横坡变了"，
    --   行序只是源文件里的排列，换个导出顺序就变了，不该拿它当身份。
    UNIQUE (section_id, station_km)
);
COMMENT ON TABLE superelev_transition IS '超高过渡（.SUP）★设计输入，一行一个过渡变化点。六列横坡绕桩号左右对称；NULL 表示源文件写了 9999「可以忽略此数据」，即该列在此位置不参与约束、过渡照常继续（不是缺值、也不是沿用上值）。教程 §13.5';
CREATE INDEX IF NOT EXISTS idx_superelev_trans_station ON superelev_transition(section_id, station_km);

-- A17. 路幅宽度（.WID）★设计输入
-- ---------------------------------------------------------------------------
-- 为什么必须有这张表：`section_design_attr.roadway_width_m` 是**一个标量**，
-- 而路幅宽度**本来就随桩号变**（加宽段、匝道、交叉口、变速车道），一个标量
-- 装不下分段变化 —— 那是会丢数据的简化。按本项目「存设计输入、导出派生量」的
-- 规矩：.WID 是**设计输入**（本表），roadway_width_m 是由它导出的**派生标量**。
--
-- ★一行 = **一个桩号**（一侧），与 A16 superelev_transition 同一个形状。
--   教程 §13.4 说「数据每两行为一组，说明路基一侧某个桩号区间内的路幅宽度
--   变化情况」—— 但**每一行都有自己的桩号**，两行是"这个区间的起、终点"。
--   本表**照原样一行一行存**，不折叠成区间：
--     · 与 GE 域其余逐桩数据同一寻址方式（按桩号点查），不另立一套区间寻址；
--     · 组内两行**可以不同**（列 4：有附加车道时上一行为 1/2、下一行为 0），
--       折叠成区间就必须丢一个值；
--     · A15 geometry_point 要按桩号取宽度，点查才能直接对齐。
--   `group_seq` 保留"它是第几个区间"这个信息（= 教程的"第几组"），
--   `seq_no` 是该侧第几个数据行。区间起终点由同侧相邻两行**推得**，不落库。
--
-- ⚠ **不挂 station_id 外键**，与 A16 同理，且已用实库数据验证过：
--   .SUP 的 76 个变化点里只有 34 个落在 station_sequence 里，.WID 的 5701.461
--   也不在。**设计变化点的桩号本来就不是 .STA 那个桩号序列**——硬挂外键就得先
--   往 station_sequence 里塞"不是桩号序列的点"，那是污染一等实体。
CREATE TABLE IF NOT EXISTS roadbed_width (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id               bigint NOT NULL REFERENCES road_section(id),
    side                     varchar(8) NOT NULL,          -- left 左侧 / right 右侧（教程的 Z/Y 行；实测为 [LEFT]/[RIGHT]）
    seq_no                   smallint NOT NULL,            -- 该侧第几个数据行（源文件行序，从 1 起）
    group_seq                smallint NOT NULL,            -- 该侧第几个桩号区间（教程「每两行为一组」的组号）
    station_km               numeric(12,6) NOT NULL,       -- ★桩号（本行自己的桩号；键的一部分）
    median_width_m           numeric(6,3),                 -- 中央分隔带宽度（0 = 无中央分隔带，即"不同类型"之一）
    half_carriageway_width_m numeric(6,3),                 -- 半侧路面（行车道＋内侧路缘带）宽度
    extra_lane_flag          smallint,                     -- 有无附加车道：0 无 / 1 有 / 2 有（其下一行 0 表示主线外侧路缘带宽度）
    hard_shoulder_width_m    numeric(6,3),                 -- 硬路肩宽度（折线法标注时该列是楔形端部鼻端半径）
    earth_shoulder_width_m   numeric(6,3),                 -- 土路肩宽度（同上）
    extra_lane_file          text,                         -- 有附加车道时的项目文件名；无则源文件写 0，此处存 NULL
    remark                   text,
    -- ★键是**桩号**（一侧一个桩号一行），不是行序、也不是区间号。
    --   左右侧可以同桩号（本工程 0.000 两侧都有），故 side 必须进键。
    UNIQUE (section_id, side, station_km)
);
COMMENT ON TABLE roadbed_width IS '路幅宽度（.WID）★设计输入，一行 = 一侧的一个桩号。教程 §13.4 的 7 列原样保存。★与 A16 superelev_transition 同形：**键是桩号**，值自本桩号起保持到同侧下一个桩号（分段常量，不是渐变）。区间起终点由相邻两行推得，不落库。★本表**不存**路基总宽：总宽 = 中央分隔带 + 2×(半侧路面 + 硬路肩 + 土路肩)，是跨左右两行的派生量，GENERATED 列也表达不了（生成列不能跨行），故不落库';
COMMENT ON COLUMN roadbed_width.side IS '路基侧别：left 左侧 / right 右侧。教程 §13.4 用一行"Z"/"Y"引出其后数据；实测 6.00 版用 [LEFT]/[RIGHT] 段标题行（教程全文无此写法）';
COMMENT ON COLUMN roadbed_width.station_km IS '★桩号（本行自己的桩号）。⚠ 与 A16 一样**不挂 station_id 外键**：设计变化点的桩号不是 .STA 桩号序列的子集（实测 .SUP 76 点只有 34 点在序列里）';
COMMENT ON COLUMN roadbed_width.group_seq IS '该侧第几个桩号区间（教程 §13.4「数据每两行为一组」的组号）。同一 group_seq 的两行 = 这个区间的起、终点';
COMMENT ON COLUMN roadbed_width.half_carriageway_width_m IS '半侧路面宽度＝行车道＋内侧路缘带（教程 §13.4 第 3 列）。本工程 3.500 m，即路面 2×3.5＝7 m，与 section_design_attr.roadway_width_m=10.00（含硬路肩/土路肩）自洽';
COMMENT ON COLUMN roadbed_width.extra_lane_flag IS '有无附加车道标识（教程 §13.4 第 4 列）：0 无附加车道 / 1 或 2 有；为 2 时其下一行的 0 表示主线外侧路缘带宽度。★这一列**有意**允许同组两行不同，故本表不折叠成区间';
CREATE INDEX IF NOT EXISTS idx_roadbed_width_station ON roadbed_width(section_id, side, station_km);

-- ######################## I. 设计控制参数（.CTR） ########################

-- 为什么单开一节而不并进 A：A 节是「空间与档案」——几何实体本身；
-- 本节是**设计控制参数**——边坡/边沟/路槽/构造物等"怎么修"的控制量。
-- 两者都由 .STA 桩号寻址，但性质不同，故分节。
--
-- ★ 全部 9 张表统一约定（与 A16/A17 一致）：
--     · `section_id` + `station_km` 锚定，**键是桩号**；
--     · **不挂 station_id 外键** —— .CTR 的分段桩号不是 .STA 桩号序列的子集
--       （实测：.CTR 的 ZDMDG 20 个桩号里 1.790/2.610/3.130 都不在序列里）；
--     · 侧别用 left/right（源文件用 Z=左 / Y=右 的前缀，解析时归一）；
--     · 坡度一律是**简写 m 值**（教程 §13.10：坡度 1:m 只写 m），有正负，
--       0 = 从中央水平向外（碎落台/护坡道，此时"坡高"实为宽度），
--       9999 / -9999 = 垂直向上 / 向下。**9999 是哨兵不是数值**，按 NULL 存。
--
-- 依据：纬地教程 v5.88 §13.10《设计参数控制数据文件（*.ctr）》18 类格式 / 36 个关键字。
--       本工程 .CTR 实测 36 个关键字，19 个有数据、17 个为空。
--       下面 9 张表覆盖其中**有数据的全部**；空关键字只在
--       contracts/design-import/README.md 里登记，不建表。

-- I1. 路基边坡分段（.CTR 的 ZTFBP/YTFBP/ZWFBP/YWFBP）
-- ---------------------------------------------------------------------------
-- 教程格式：分段终点桩号，本段变化组数，坡度，控制坡高，最大坡高，砌护控制，…
-- 一组 = 一级边坡。分级放坡：第一级放到最大坡高仍不交地面线就接第二级。
-- 本工程：填方 5 级（-1.500/0/1.500/0/-1.750 与 -2.000 收尾）、挖方 6 级。
CREATE TABLE IF NOT EXISTS slope_segment (
    id               bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id       bigint NOT NULL REFERENCES road_section(id),
    side             varchar(8) NOT NULL,          -- left 左侧（源文件 Z 前缀）/ right 右侧（Y 前缀）
    slope_kind       varchar(8) NOT NULL,          -- fill 填方（T）/ cut 挖方（W）
    station_km       numeric(12,6) NOT NULL,       -- ★分段终点桩号（本行自己的桩号；键的一部分）
    group_seq        smallint NOT NULL,            -- 本段内第几级边坡（源文件"变化组数"的序号，从 1 起）
    slope_ratio      numeric(8,3),                 -- 坡度简写 m（1:m 的 m）。正=向上 / 负=向下 / 0=水平向外 / 9999=垂直向上 / -9999=垂直向下
    control_height_m numeric(8,3),                 -- 控制坡高（0 = 不控制，一般项目都填 0）
    max_height_m     numeric(8,3),                 -- 最大坡高（坡度为 0 时，此列表示碎落台/护坡道的**宽度**）
    protection       smallint,                     -- 砌护控制：0 不砌护 / 1 砌护
    remark           text,
    UNIQUE (section_id, side, slope_kind, station_km, group_seq)
);
COMMENT ON TABLE slope_segment IS '路基边坡分段（.CTR 的 ZTFBP/YTFBP/ZWFBP/YWFBP）★设计输入。教程 §13.10 第 1 类格式：分段终点桩号 + 变化组数 + (坡度,控制坡高,最大坡高,砌护控制)×组数。一行 = 一级边坡。★键是桩号';
COMMENT ON COLUMN slope_segment.slope_kind IS '填方 fill（源文件 T）/ 挖方 cut（源文件 W）。配合 side 还原源关键字：ZTFBP=左填 / YTFBP=右填 / ZWFBP=左挖 / YWFBP=右挖';
COMMENT ON COLUMN slope_segment.station_km IS '★分段终点桩号。⚠ 不挂 station_id 外键：.CTR 的分段桩号不是 .STA 桩号序列的子集';
COMMENT ON COLUMN slope_segment.slope_ratio IS '坡度简写 m 值（教程 §13.10：坡度 1:m 时只输入 m）。正=向上 / 负=向下；**0 = 从中央水平向外**（碎落台或护坡道，此时 max_height_m 表示宽度）；9999=垂直向上 / -9999=垂直向下。⚠ 9999 是**哨兵**，按 NULL 存';
COMMENT ON COLUMN slope_segment.max_height_m IS '最大坡高。★坡度为 0 时此列表示**碎落台/护坡道的宽度**（教程明写），不是高度';
CREATE INDEX IF NOT EXISTS idx_slope_segment_station ON slope_segment(section_id, side, slope_kind, station_km);

-- I2. 边沟 / 排水沟断面分段（.CTR 的 ZBGXS/YBGXS/ZPSGXS/YPSGXS）
-- ---------------------------------------------------------------------------
-- 教程格式：分段终点桩号，本段变化组数，坡度，坡高，砌护控制，…
-- 一行 = 沟的一个边坡/沟底折点。组数 0 表示该段不设沟（本工程排水沟即如此）。
CREATE TABLE IF NOT EXISTS ditch_segment (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id   bigint NOT NULL REFERENCES road_section(id),
    side         varchar(8) NOT NULL,              -- left / right
    ditch_kind   varchar(16) NOT NULL,             -- side_ditch 边沟（BGXS）/ drainage_ditch 排水沟（PSGXS）
    station_km   numeric(12,6) NOT NULL,           -- ★分段终点桩号
    group_seq    smallint NOT NULL,                -- 本段内第几个折点（从 1 起）
    slope_ratio  numeric(8,3),                     -- 坡度简写 m（同 I1 约定）
    height_m     numeric(8,3),                     -- 坡高（坡度为 0 时表示宽度）
    protection   smallint,                         -- 砌护控制：0 不砌护 / 1 砌护
    remark       text,
    UNIQUE (section_id, side, ditch_kind, station_km, group_seq)
);
COMMENT ON TABLE ditch_segment IS '边沟/排水沟断面分段（.CTR 的 ZBGXS/YBGXS 边沟、ZPSGXS/YPSGXS 排水沟）★设计输入。教程 §13.10 第 2/3 类格式：分段终点桩号 + 变化组数 + (坡度,坡高,砌护控制)×组数。一行 = 沟的一个折点。★键是桩号。组数 0 表示该段不设沟';
COMMENT ON COLUMN ditch_segment.ditch_kind IS 'side_ditch 边沟（源关键字 ZBGXS/YBGXS）/ drainage_ditch 排水沟（ZPSGXS/YPSGXS）。本工程边沟 3 折点、排水沟 0 折点（不设）';
CREATE INDEX IF NOT EXISTS idx_ditch_segment_station ON ditch_segment(section_id, side, ditch_kind, station_km);

-- I3. 路基标准断面分段（.CTR 的 ZBZDM/YBZDM）
-- ---------------------------------------------------------------------------
-- 教程格式（一个分段一行，9 个数值）：
--   分段终点桩号，半幅中分带宽，中分带坡度，中分带高度，行车道宽，行车道坡度，
--   硬路肩宽，硬路肩坡度，土路肩宽，土路肩坡度
-- ★ 这张表是 A17 roadbed_width 的**独立校验源**：本工程实测两边各列完全对上
--   （行车道 3.500 / 硬路肩 0.750 / 土路肩 0.750、坡度 2.0/2.0/3.0）。
CREATE TABLE IF NOT EXISTS standard_cross_section (
    id                            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id                    bigint NOT NULL REFERENCES road_section(id),
    side                          varchar(8) NOT NULL,      -- left / right
    station_km                    numeric(12,6) NOT NULL,   -- ★分段终点桩号
    median_half_width_m           numeric(8,3),             -- 半幅中分带宽度（0 = 无中央分隔带）
    median_crossfall_pct          numeric(8,3),             -- 中分带横坡（%）
    median_height_m               numeric(8,3),             -- 中分带高度
    lane_width_m                  numeric(8,3),             -- 行车道宽度
    lane_crossfall_pct            numeric(8,3),             -- 行车道横坡（%）
    hard_shoulder_width_m         numeric(8,3),             -- 硬路肩宽度
    hard_shoulder_crossfall_pct   numeric(8,3),             -- 硬路肩横坡（%）
    earth_shoulder_width_m        numeric(8,3),             -- 土路肩宽度
    earth_shoulder_crossfall_pct  numeric(8,3),             -- 土路肩横坡（%）
    remark                        text,
    UNIQUE (section_id, side, station_km)
);
COMMENT ON TABLE standard_cross_section IS '路基标准断面分段（.CTR 的 ZBZDM/YBZDM）★设计输入。教程 §13.10 第 9 类格式，一个分段一行、桩号后 9 个数值。★这张表是 A17 roadbed_width 的**独立校验源**：本工程两者完全自洽（行车道 3.500、硬路肩 0.750、土路肩 0.750、坡度 2.0/2.0/3.0）';
COMMENT ON COLUMN standard_cross_section.lane_crossfall_pct IS '行车道横坡（%）。本工程 2.000，与 A16 superelev_transition 在 K0+000 的 lane_left_pct/lane_right_pct = -2.00 同量级（正负号约定见 A16 注释）';
CREATE INDEX IF NOT EXISTS idx_standard_cross_section_station ON standard_cross_section(section_id, side, station_km);

-- I4. 路槽分段（.CTR 的 ZLCSD/YLCSD）
-- ---------------------------------------------------------------------------
-- 教程格式：分段终点桩号，中分带路槽深度，行车道路槽深度，硬路肩路槽深度，土路肩路槽深度（米）
-- 用途：土方计算时按此扣除路槽面积。
CREATE TABLE IF NOT EXISTS roadbed_trench (
    id                            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id                    bigint NOT NULL REFERENCES road_section(id),
    side                          varchar(8) NOT NULL,      -- left / right
    station_km                    numeric(12,6) NOT NULL,   -- ★分段终点桩号
    median_trench_depth_m         numeric(8,3),             -- 中分带路槽深度
    lane_trench_depth_m           numeric(8,3),             -- 行车道路槽深度
    hard_shoulder_trench_depth_m  numeric(8,3),             -- 硬路肩路槽深度
    earth_shoulder_trench_depth_m numeric(8,3),             -- 土路肩路槽深度
    remark                        text,
    UNIQUE (section_id, side, station_km)
);
COMMENT ON TABLE roadbed_trench IS '路槽分段（.CTR 的 ZLCSD/YLCSD）★设计输入。教程 §13.10 第 7 类格式：分段终点桩号 + 4 个路槽深度（米）。用途：土方计算时按此扣除路槽面积。★键是桩号';
CREATE INDEX IF NOT EXISTS idx_roadbed_trench_station ON roadbed_trench(section_id, side, station_km);

-- I5. 桥涵构造物控制（.CTR 的 QHSJ/HDSJ/SUIDAO）
-- ---------------------------------------------------------------------------
-- 三种构造物合成一张表：都锚在桩号上，属性高度重合（名称/跨径/角度/控制标高），
-- 分三张表会造成大量重复列。用 structure_kind 区分。
--   QHSJ（桥）：起点桩号，终点桩号，标注桩号，桥名称，跨径，主要结构形式，
--               路线角度（度），控制标高，标高控制类型（0=以上/1=以下），
--               桥梁分幅类型（-1 左半幅 / 1 右半幅 / 0 整幅）
--   HDSJ（涵洞）：涵洞中心桩号，与路线角度，跨径说明，构造物名称，控制标高
--   SUIDAO（隧道）：隧道起点桩号，终点桩号，隧道名称
-- ★ anchor_station_km 是"用于唯一标识的那一个桩号"：涵洞取中心桩号，
--   桥/隧道取起点桩号 —— 这样三种构造物能有同一个唯一键。
-- ⚠ 教程明写 QHSJ/HDSJ 的桩号**可以不按递增排列**，故本表不设"必须递增"约束。
CREATE TABLE IF NOT EXISTS structure_control (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id        bigint NOT NULL REFERENCES road_section(id),
    structure_kind    varchar(16) NOT NULL,        -- bridge 桥（QHSJ）/ culvert 涵洞（HDSJ）/ tunnel 隧道（SUIDAO）
    anchor_station_km numeric(12,6) NOT NULL,      -- ★唯一标识桩号：涵洞=中心桩号；桥/隧道=起点桩号
    name              text NOT NULL,               -- 构造物名称
    start_station_km  numeric(12,6),               -- 起点桩号（桥/隧道；涵洞为 NULL）
    end_station_km    numeric(12,6),               -- 终点桩号（桥/隧道；涵洞为 NULL）
    center_station_km numeric(12,6),               -- 中心桩号（涵洞；桥为"标注桩号"，可能 NULL）
    angle_deg         numeric(8,4),                -- 与路线夹角（度）。桥为"路线角度"，涵洞为"与路线角度"
    span_text         text,                        -- 跨径/跨径说明，原文串（如 30+30、1-1.500×2.000、3x12m）
    structure_form    text,                        -- 主要结构形式（桥用；如"混凝土空心板梁"）
    control_elev_m    numeric(10,4),               -- 控制标高
    elev_control_type smallint,                    -- 标高控制类型：0=控制在此标高**以上** / 1=**以下**（仅桥）
    deck_type         smallint,                    -- 桥梁分幅类型：-1 左半幅 / 1 右半幅 / 0 整幅（仅桥）
    trailing_flag     smallint,                    -- ★QHSJ 尾随整数的**第 3 个**：教程正文未定义，原样存下待考
    remark            text,
    UNIQUE (section_id, structure_kind, anchor_station_km)
);
COMMENT ON TABLE structure_control IS '桥涵构造物控制（.CTR 的 QHSJ 桥 / HDSJ 涵洞 / SUIDAO 隧道）★设计输入。教程 §13.10 第 5/6/15 类格式。用途：纵断面图/总体图标注，且土方计算时扣除大中桥与隧道土方。⚠ 教程明写 QHSJ/HDSJ 的桩号**可不递增排列**，故不设递增约束';
COMMENT ON COLUMN structure_control.anchor_station_km IS '★唯一标识桩号：涵洞取中心桩号，桥/隧道取起点桩号。三种构造物靠它共用同一个唯一键';
COMMENT ON COLUMN structure_control.center_station_km IS '中心桩号。涵洞用它；桥的这一列是源文件的"标注桩号"，教程说明新版已由字符串改为**数字类型**，故本列是 numeric';
COMMENT ON COLUMN structure_control.trailing_flag IS '★QHSJ 行尾 3 个整数里的第 3 个。⚠ 教程 §13.10 正文只列了 2 个尾随整数（标高控制类型、桥梁分幅类型），但**教程自己的示例**（`4100 4280 4190 … 135.80 0 1 0`）和本工程实测文件（`273.000 … 0.0000 0 1 0`）都**有 3 个**。第 3 个的含义教程全文未定义，故**原样存下待考**，不猜、不丢';
COMMENT ON COLUMN structure_control.span_text IS '跨径/跨径说明，**保留原文串**（如 30+30、1-1.500×2.000、3x12m）。教程明确跨径格式含 + 与 ×，不是纯数值，故用 text 而非 numeric';
CREATE INDEX IF NOT EXISTS idx_structure_control_station ON structure_control(section_id, structure_kind, anchor_station_km);

-- I6. 挖方土石成份分段（.CTR 的 TFFD）
-- ---------------------------------------------------------------------------
-- 教程格式：分段桩号，第一类至第六类土（石）所占挖方数量中的百分比。
-- 用途：土石方数量计算时按此把挖方量分成六类。
CREATE TABLE IF NOT EXISTS earthwork_composition (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id  bigint NOT NULL REFERENCES road_section(id),
    station_km  numeric(12,6) NOT NULL,            -- ★分段桩号
    pct_1       numeric(8,3),                      -- 第一类土（石）占比（%）
    pct_2       numeric(8,3),
    pct_3       numeric(8,3),
    pct_4       numeric(8,3),
    pct_5       numeric(8,3),
    pct_6       numeric(8,3),
    remark      text,
    UNIQUE (section_id, station_km)
);
COMMENT ON TABLE earthwork_composition IS '挖方土石成份分段（.CTR 的 TFFD）★设计输入。教程 §13.10 第 4 类格式：分段桩号 + 六类土（石）占挖方量的百分比。用途：土石方数量计算按此分类。★键是桩号';
COMMENT ON COLUMN earthwork_composition.pct_1 IS '第一类土（石）占挖方数量的百分比（%）。六列之和应为 100，但本表**不设 CHECK 约束**——源文件是设计输入，和为 100 是设计约定不是数据完整性条件，硬约束会拒掉合法的中间稿';
CREATE INDEX IF NOT EXISTS idx_earthwork_composition_station ON earthwork_composition(section_id, station_km);

-- I7. 附加用地宽度分段（.CTR 的 ZYDK/YYDK）
-- ---------------------------------------------------------------------------
-- 教程格式：桩号，填方用地宽度，挖方用地宽度（起/分段/终点各一行）。
-- 用途：横断面图绘用地界碑；土方文件记录用地宽度以计算用地表、绘用地图。
-- ⚠ 教程明写：**分段桩号间用地宽度不等时按线性渐变计算** ——
--   即本表是**变化点**，两点之间是渐变，不是分段常量（与 A16/A17 不同！）。
CREATE TABLE IF NOT EXISTS land_use_width (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id        bigint NOT NULL REFERENCES road_section(id),
    side              varchar(8) NOT NULL,         -- left / right
    station_km        numeric(12,6) NOT NULL,      -- ★变化点桩号
    fill_land_width_m numeric(8,3),                -- 填方用地宽度（用地界碑距填方坡脚或排水沟外缘的水平距离）
    cut_land_width_m  numeric(8,3),                -- 挖方用地宽度（用地界碑距挖方坡口或截水沟外边缘的水平距离）
    remark            text,
    UNIQUE (section_id, side, station_km)
);
COMMENT ON TABLE land_use_width IS '附加用地宽度分段（.CTR 的 ZYDK/YYDK）★设计输入。教程 §13.10 第 17 类格式：桩号 + 填方用地宽度 + 挖方用地宽度。⚠ 与 A16/A17 的"分段常量"**不同**：教程明写分段桩号间用地宽度不等时**按线性渐变计算**，故本表是变化点，两点之间需插值';
CREATE INDEX IF NOT EXISTS idx_land_use_width_station ON land_use_width(section_id, side, station_km);

-- I8. 超填 / 清除表土分段（.CTR 的 ZCHT/YCHT/DCHT/QCHBT）
-- ---------------------------------------------------------------------------
--   ZCHT/YCHT（填方路基左右侧超宽填筑）：分段终点桩号，一侧超填水平宽度
--   DCHT（填方路基顶面超填）：分段终点桩号，超填厚度
--   QCHBT（清除表土）：分段终点桩号，左右侧坡脚外增加宽度，清除表土厚度
-- 合成一张：都是"桩号 + 一到两个工程量标量"，用 fill_kind 区分。
-- ⚠ 本工程这四个关键字**全为空**（表建好但 0 行），保留结构以便将来接别的项目。
CREATE TABLE IF NOT EXISTS extra_fill (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id   bigint NOT NULL REFERENCES road_section(id),
    side         varchar(8),                       -- left / right；QCHBT（清除表土）是左右一起，为 NULL
    fill_kind    varchar(16) NOT NULL,             -- side_overfill 超宽填筑 / top_overfill 顶面超填 / topsoil_clear 清除表土
    station_km   numeric(12,6) NOT NULL,           -- ★分段终点桩号
    width_m      numeric(8,3),                     -- 水平宽度（超宽填筑的一侧超填宽度；清除表土的坡脚外增加宽度）
    thickness_m  numeric(8,3),                     -- 厚度（顶面超填厚度；清除表土厚度）
    remark       text,
    UNIQUE (section_id, side, fill_kind, station_km)
);
COMMENT ON TABLE extra_fill IS '超填/清除表土分段（.CTR 的 ZCHT/YCHT 超宽填筑、DCHT 顶面超填、QCHBT 清除表土）★设计输入。教程 §13.10 第 10/11/12 类格式。⚠ 本工程这四个关键字**全为空**，表建好但 0 行';
COMMENT ON COLUMN extra_fill.side IS '侧别。超宽填筑（ZCHT/YCHT）与顶面超填有侧别；清除表土（QCHBT）是左右一起处理，故为 NULL。⚠ 因为可为 NULL，本表唯一键里 side 为 NULL 时 PostgreSQL 视作互不相等 —— 这是**有意**的：QCHBT 一行覆盖左右两侧';
CREATE INDEX IF NOT EXISTS idx_extra_fill_station ON extra_fill(section_id, fill_kind, station_km);

-- I9. 地质概况 / 水准点（.CTR 的 DZGK/SHUIZHUNDIAN）
-- ---------------------------------------------------------------------------
--   DZGK（地质概况）：分段终点桩号，地质概况文字说明（≤128 汉字，可含空格不可换行）
--   SHUIZHUNDIAN（水准点）：位置桩号，名称，高程，说明
-- 合成一张：都是"桩号 + 文字 + 可选高程"。
-- ⚠ 本工程这两个关键字**全为空**。
CREATE TABLE IF NOT EXISTS design_control_text (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id  bigint NOT NULL REFERENCES road_section(id),
    text_kind   varchar(16) NOT NULL,              -- geology 地质概况（DZGK）/ benchmark 水准点（SHUIZHUNDIAN）
    station_km  numeric(12,6) NOT NULL,            -- ★分段终点桩号（地质）/ 位置桩号（水准点）
    name        text,                              -- 名称（水准点用）
    elev_m      numeric(10,4),                     -- 高程（水准点用）
    content     text,                              -- 文字说明（地质概况说明 / 水准点说明）
    remark      text,
    UNIQUE (section_id, text_kind, station_km)
);
COMMENT ON TABLE design_control_text IS '地质概况/水准点（.CTR 的 DZGK 地质概况、SHUIZHUNDIAN 水准点）★设计输入。教程 §13.10 第 13/16 类格式。用途：纵断面绘图标注。⚠ 本工程这两个关键字**全为空**，表建好但 0 行';
COMMENT ON COLUMN design_control_text.content IS '文字说明。教程规定地质概况**不超过 128 个汉字字节数**，汉字之间可空格但**不能换行**（故一个分段说明是一行，本列存该行全文）';
CREATE INDEX IF NOT EXISTS idx_design_control_text_station ON design_control_text(section_id, text_kind, station_km);

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
    station_text     varchar(32),                          -- 桩号
    station_km       numeric(12,6),
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
CREATE INDEX IF NOT EXISTS idx_disease_sec_station ON disease_record(section_id, station_km);
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
    station_text   varchar(32),                            -- 拍摄桩号
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


-- ######################## 兼容视图（契约② v0.2 旧列名）########################
-- 目的：v0.2 时代按旧列名（stake_*）写的**只读查询**在一个版本内不中断。
-- 用法：把查询里的表名换成对应视图名即可，列名保持 v0.2 原样。
-- 范围：仅覆盖本次改名的 4 张表（road_section / monitor_cross_section /
--       disease_record / media_file）；**不含写路径**，写入必须走新列名。
-- 退役：v0.4 发布时随工单 #2 一并删除。

CREATE OR REPLACE VIEW v02_compat_road_section AS
  SELECT id, line_id, design_project_id, section_name,
         start_station_text AS start_stake,
         end_station_text   AS end_stake,
         start_station_km   AS start_km,
         end_station_km     AS end_km,
         length_m, direction, pavement_type, climate_zone, remark
    FROM road_section;
COMMENT ON VIEW v02_compat_road_section IS 'v0.2 兼容视图（只读）：旧列名 start_stake/end_stake/start_km/end_km';

CREATE OR REPLACE VIEW v02_compat_monitor_cross_section AS
  SELECT id, section_id,
         station_text AS stake_text,
         station_km   AS stake_km,
         lane_no, lon, lat, purpose, install_date, status, remark
    FROM monitor_cross_section;
COMMENT ON VIEW v02_compat_monitor_cross_section IS 'v0.2 兼容视图（只读）：旧列名 stake_text/stake_km';

CREATE OR REPLACE VIEW v02_compat_disease_record AS
  SELECT id, task_id, section_id,
         station_text AS stake_text,
         station_km   AS stake_km,
         lane_no, disease_code, severity, length_m, width_m, area_m2,
         image_media_ids, source, status, first_seen, handle_time, remark
    FROM disease_record;
COMMENT ON VIEW v02_compat_disease_record IS 'v0.2 兼容视图（只读）：旧列名 stake_text/stake_km';

CREATE OR REPLACE VIEW v02_compat_media_file AS
  SELECT id, biz_table, biz_id, media_type, file_path, file_name, size_bytes,
         width_px, height_px, duration_s, taken_time,
         station_text AS stake_text,
         md5, remark
    FROM media_file;
COMMENT ON VIEW v02_compat_media_file IS 'v0.2 兼容视图（只读）：旧列名 stake_text';

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

-- ═══════════════════════════════════════════════════════════════════════════════
-- J. 逐桩土方断面（.tf）与逐桩路基设计断面（.lj）
-- ═══════════════════════════════════════════════════════════════════════════════
-- 依据纬地教程 v5.88 §13.9（土方数据文件 *.tf）、§13.6（路基设计中间数据 *.lj）。
--
-- ★★ 本节的建表原则：**照数据文件的样式，好追溯**（用户 2026-09 明确要求）。
--    因此：
--      ① 文件里有的列，这里**全建**，哪怕本工程全是 0（.tf 74 列里有 44 列全 0）；
--      ② 列名用**文件自带的表头**逐条译成英文（.tf 自带 74 个列名，是唯一一个
--         自带完整表头的纬地文件，所以映射**没有猜的成分**）；
--      ③ 与 .DMX/.ZDM 重复的量（地面标高/设计标高）**照样存** —— 可追溯优先，
--         重复的风险由契约测试的**一致性对账**兜住，而不是靠"不存"。
--
-- ★★ 两处**说明书与实测文件对不上**（都用数据判过，不是猜的）：
--    (1) .tf 第 2/3 列 —— 说明书正文写「[桩号][填方面积][挖方面积]」，
--        但**文件自带表头**写「[桩号][挖方面积][填方面积]」。
--        用 .lj 的设计标高 vs 地面标高独立判填挖，再比 332 行谁大：
--        按文件表头 → 326/332 吻合；按说明书 → 6/332。
--        **文件表头是对的**，说明书正文把这两列写反了。
--    (2) .lj 的列数与列序 —— 说明书说「每一行共 20 项数据」，
--        实测 **24 列**；且说明书给的宽度列序与实测值对不上（详见 J2 的注释）。
--
-- ⚠ 这两张表都是**逐桩**表（332 行 = .STA 桩号序列的 332 个），
--   故用 station_id 外键锚定 station_sequence —— 与 I 节（.CTR）不同：
--   .CTR 的分段桩号**不是** .STA 桩号序列的子集，所以那边只能存 station_km。
--   station_km 在这里是**照文件原样存下**的（可追溯），主锚定是 station_id。

-- ── J1. earthwork_section 逐桩土方断面（.tf，74 列）─────────────────────────
-- 教程 §13.9：记录桩号、填挖方断面面积、左右侧坡口坡脚至中桩的距离等。
-- 用途：土石方计算与调配、公路用地图、路线总体图生成的基础数据。
-- 实测：332 行 × 74 列（文件第 75 列是空串 ""，是程序产物，不建列）。
-- 值域实测：挖方面积 0~1014.901、填方面积 0~669.644、中桩填挖 −58.406~+17.305、
--          基缘高 54.437~80.889、坡脚高 41.163~124.758。
-- ⚠ 列名逐条来自文件自带表头（顺序一致，第 N 列 ↔ 第 N 个英文列）。
CREATE TABLE IF NOT EXISTS earthwork_section (
    id                                bigint       generated always as identity primary key,
    section_id                        bigint       not null references road_section(id),
    station_id                        bigint       not null references station_sequence(id),
    station_km                             numeric(10,4)    NULL,   -- 第  1 列  桩     号
    cut_area_m2                            numeric(10,4)    NULL,   -- 第  2 列  挖方面积
    fill_area_m2                           numeric(10,4)    NULL,   -- 第  3 列  填方面积
    center_fill_cut_m                      numeric(10,4)    NULL,   -- 第  4 列  中桩填挖
    subgrade_left_width_m                  numeric(10,4)    NULL,   -- 第  5 列  路基左宽
    subgrade_right_width_m                 numeric(10,4)    NULL,   -- 第  6 列  路基右宽
    subgrade_edge_left_elev_m              numeric(10,4)    NULL,   -- 第  7 列  基缘左高
    subgrade_edge_right_elev_m             numeric(10,4)    NULL,   -- 第  8 列  基缘右高
    left_slope_toe_offset_m                numeric(10,4)    NULL,   -- 第  9 列  左坡脚距
    right_slope_toe_offset_m               numeric(10,4)    NULL,   -- 第 10 列  右坡脚距
    left_slope_toe_elev_m                  numeric(10,4)    NULL,   -- 第 11 列  左坡脚高
    right_slope_toe_elev_m                 numeric(10,4)    NULL,   -- 第 12 列  右坡脚高
    left_ditch_edge_offset_m               numeric(10,4)    NULL,   -- 第 13 列  左沟缘距
    right_ditch_edge_offset_m              numeric(10,4)    NULL,   -- 第 14 列  右沟缘距
    left_berm_width_m                      numeric(10,4)    NULL,   -- 第 15 列  左护坡道宽
    right_berm_width_m                     numeric(10,4)    NULL,   -- 第 16 列  右护坡道宽
    left_ditch_bottom_elev_m               numeric(10,4)    NULL,   -- 第 17 列  左沟底高
    right_ditch_bottom_elev_m              numeric(10,4)    NULL,   -- 第 18 列  右沟底高
    left_ditch_center_offset_m             numeric(10,4)    NULL,   -- 第 19 列  左沟心距
    right_ditch_center_offset_m            numeric(10,4)    NULL,   -- 第 20 列  右沟心距
    left_ditch_depth_m                     numeric(10,4)    NULL,   -- 第 21 列  左沟深度
    right_ditch_depth_m                    numeric(10,4)    NULL,   -- 第 22 列  右沟深度
    left_land_width_m                      numeric(10,4)    NULL,   -- 第 23 列  左用地宽
    right_land_width_m                     numeric(10,4)    NULL,   -- 第 24 列  右用地宽
    topsoil_clear_area_m2                  numeric(10,4)    NULL,   -- 第 25 列  清表面积
    top_overfill_area_m2                   numeric(10,4)    NULL,   -- 第 26 列  顶超面积
    left_overfill_area_m2                  numeric(10,4)    NULL,   -- 第 27 列  左超面积
    right_overfill_area_m2                 numeric(10,4)    NULL,   -- 第 28 列  右超面积
    drainage_ditch_flag                    smallint         NULL,   -- 第 29 列  计排水沟
    left_ditch_fill_area_m2                numeric(10,4)    NULL,   -- 第 30 列  左沟面积填
    left_ditch_cut_area_m2                 numeric(10,4)    NULL,   -- 第 31 列  左沟面积挖
    right_ditch_fill_area_m2               numeric(10,4)    NULL,   -- 第 32 列  右沟面积填
    right_ditch_cut_area_m2                numeric(10,4)    NULL,   -- 第 33 列  右沟面积挖
    trench_fill_area_m2                    numeric(10,4)    NULL,   -- 第 34 列  路槽面积填
    trench_cut_area_m2                     numeric(10,4)    NULL,   -- 第 35 列  路槽面积挖
    topsoil_clear_width_m                  numeric(10,4)    NULL,   -- 第 36 列  清表宽度
    topsoil_clear_thickness_m              numeric(10,4)    NULL,   -- 第 37 列  清表厚度
    cut_class_1_area_m2                    numeric(10,4)    NULL,   -- 第 38 列  挖1类面积
    cut_class_2_area_m2                    numeric(10,4)    NULL,   -- 第 39 列  挖2类面积
    cut_class_3_area_m2                    numeric(10,4)    NULL,   -- 第 40 列  挖3类面积
    cut_class_4_area_m2                    numeric(10,4)    NULL,   -- 第 41 列  挖4类面积
    cut_class_5_area_m2                    numeric(10,4)    NULL,   -- 第 42 列  挖5类面积
    cut_class_6_area_m2                    numeric(10,4)    NULL,   -- 第 43 列  挖6类面积
    left_trench_b_area_m2                  numeric(10,4)    NULL,   -- 第 44 列  左路槽B
    right_trench_b_area_m2                 numeric(10,4)    NULL,   -- 第 45 列  右路槽B
    left_trench_c_area_m2                  numeric(10,4)    NULL,   -- 第 46 列  左路槽C
    right_trench_c_area_m2                 numeric(10,4)    NULL,   -- 第 47 列  右路槽C
    left_bedding_area_m2                   numeric(10,4)    NULL,   -- 第 48 列  左垫层
    right_bedding_area_m2                  numeric(10,4)    NULL,   -- 第 49 列  右垫层
    left_subgrade_bed_area_m2              numeric(10,4)    NULL,   -- 第 50 列  左路床
    right_subgrade_bed_area_m2             numeric(10,4)    NULL,   -- 第 51 列  右路床
    left_earth_shoulder_fill_area_m2       numeric(10,4)    NULL,   -- 第 52 列  左土肩培土
    right_earth_shoulder_fill_area_m2      numeric(10,4)    NULL,   -- 第 53 列  右土肩培土
    left_edge_wrap_fill_area_m2            numeric(10,4)    NULL,   -- 第 54 列  左包边土
    right_edge_wrap_fill_area_m2           numeric(10,4)    NULL,   -- 第 55 列  右包边土
    left_side_ditch_backfill_area_m2       numeric(10,4)    NULL,   -- 第 56 列  左边沟回填
    right_side_ditch_backfill_area_m2      numeric(10,4)    NULL,   -- 第 57 列  右边沟回填
    left_intercept_ditch_fill_area_m2      numeric(10,4)    NULL,   -- 第 58 列  左截沟填
    left_intercept_ditch_cut_area_m2       numeric(10,4)    NULL,   -- 第 59 列  左截沟挖
    right_intercept_ditch_fill_area_m2     numeric(10,4)    NULL,   -- 第 60 列  右截沟填
    right_intercept_ditch_cut_area_m2      numeric(10,4)    NULL,   -- 第 61 列  右截沟挖
    bench_cut_area_m2                      numeric(10,4)    NULL,   -- 第 62 列  挖台阶面积
    fill_class_1_area_m2                   numeric(10,4)    NULL,   -- 第 63 列  填1类面积
    fill_class_2_area_m2                   numeric(10,4)    NULL,   -- 第 64 列  填2类面积
    fill_class_3_area_m2                   numeric(10,4)    NULL,   -- 第 65 列  填3类面积
    fill_class_4_area_m2                   numeric(10,4)    NULL,   -- 第 66 列  填4类面积
    fill_class_5_area_m2                   numeric(10,4)    NULL,   -- 第 67 列  填5类面积
    fill_class_6_area_m2                   numeric(10,4)    NULL,   -- 第 68 列  填6类面积
    waste_class_1_area_m2                  numeric(10,4)    NULL,   -- 第 69 列  弃1类面积
    waste_class_2_area_m2                  numeric(10,4)    NULL,   -- 第 70 列  弃2类面积
    waste_class_3_area_m2                  numeric(10,4)    NULL,   -- 第 71 列  弃3类面积
    waste_class_4_area_m2                  numeric(10,4)    NULL,   -- 第 72 列  弃4类面积
    waste_class_5_area_m2                  numeric(10,4)    NULL,   -- 第 73 列  弃5类面积
    waste_class_6_area_m2                  numeric(10,4)    NULL,   -- 第 74 列  弃6类面积
    remark                            text,
    constraint uq_earthwork_section_station unique (section_id, station_id)
);
comment on table earthwork_section is
  '逐桩土方断面（纬地 .tf 土方数据文件，教程 §13.9）。74 列**逐条对应文件自带表头**。';
comment on column earthwork_section.station_km is
  '桩号 km（照文件原样存；主锚定是 station_id）。';
comment on column earthwork_section.cut_area_m2 is
  '挖方面积 m²。⚠ 文件第 2 列。说明书正文把它与第 3 列写反了 —— 以文件自带表头为准。';
comment on column earthwork_section.fill_area_m2 is
  '填方面积 m²。⚠ 文件第 3 列（同上）。';
comment on column earthwork_section.drainage_ditch_flag is
  '计排水沟：0/1 标志（本工程 332 行全为 1）。';

-- ── J2. roadbed_design_point 逐桩路基设计断面（.lj，24 列）───────────────────
-- 教程 §13.6：由"路基设计计算"自动生成，保存**所有指定桩号（.dmx 里的所有桩号）**
-- 断面的路幅宽度、设计标高、地面标高及超高情况。路基设计表与横断面戴帽子都从它取数。
-- 实测：332 行 × 24 列（说明书说"每一行共 20 项数据" —— **对不上**）。
--
-- ★ 实测分布（前 13 列恒为常数、与桩号无关，说明它们就是**标准断面**）：
--      1 桩号            2 地面标高        3 设计标高
--      4  0.750  左土路肩宽        8  0.000  右中分带宽
--      5  0.750  左硬路肩宽        9  0.000  ★待考（说明书无此项）
--      6  0.000  左中分带宽       10  3.500  右路面宽
--      7  3.500  左路面宽         11  0.000  ★待考（说明书无此项）
--                                 12  0.750  右硬路肩宽
--                                 13  0.750  右土路肩宽
--      14..24  11 个高差（第 14 列实测 −0.0375~+0.4725，可为负）
--
-- ★★ 说明书与实测**三处对不上**（不是猜，是逐列比出来的）：
--    (1) 说明书说 20 项，实测 **24 列**；
--    (2) 说明书给的列序是「左土路肩 左硬路肩 **左路面 左中分带**」，
--        实测第 6 列 = 0.000（中分带）、第 7 列 = 3.500（路面）—— **中分带与路面写反了**；
--    (3) 第 9、11 列在说明书里**没有对应项**，本工程恒为 0.000。
--        按对称读法，右侧应是「中分带 0.000 / 路面 3.500 / 硬路肩 0.750 / 土路肩 0.750」，
--        但实测第 8~13 列是「0.000 0.000 3.500 0.000 0.750 0.750」——
--        比对称读法**多插了两个 0.000**。故第 9、11 列**不猜**，按位置命名并标待考。
--
-- ★ 14..24 那 11 个高差：说明书只说"各路幅宽度位置相对于路面设计标高位置
--   （超高旋转位置）的设计高差"，没说几个、怎么排。用 .CTR 的横坡
--   （土路肩 3% / 硬路肩 2% / 行车道 2% / 中分带 0%）按宽度**累计**算，
--   与 K0+000 的 0.0225 / 0.0375 / 0.1075 完全吻合；
--   但拿同一组常数横坡套全部 332 行只对 204 行（其余 128 行最大差 0.4725）——
--   **差的那 128 行正是超高段**。即：这 11 列编码的是**逐桩真实横坡**，
--   因此它们能反过来校验 .SUP。这一条留给契约测试（尚未做）。
CREATE TABLE IF NOT EXISTS roadbed_design_point (
    id                                bigint       generated always as identity primary key,
    section_id                        bigint       not null references road_section(id),
    station_id                        bigint       not null references station_sequence(id),
    station_km                        numeric(12,6) not null,   -- 照文件原样存；主锚定是 station_id
    ground_elev_m                     numeric(10,4),            -- 第  2 列（与 .DMX 重复，照存）
    design_elev_m                     numeric(10,4),            -- 第  3 列（与 .ZDM 重复，照存）
    left_earth_shoulder_width_m       numeric(10,4),            -- 第  4 列  实测恒 0.750
    left_hard_shoulder_width_m        numeric(10,4),            -- 第  5 列  实测恒 0.750
    left_median_width_m               numeric(10,4),            -- 第  6 列  实测恒 0.000
    left_lane_width_m                 numeric(10,4),            -- 第  7 列  实测恒 3.500
    right_median_width_m              numeric(10,4),            -- 第  8 列  实测恒 0.000
    extra_width_09_m                  numeric(10,4),            -- 第  9 列 ★待考（说明书无此项，恒 0.000）
    right_lane_width_m                numeric(10,4),            -- 第 10 列  实测恒 3.500
    extra_width_11_m                  numeric(10,4),            -- 第 11 列 ★待考（说明书无此项，恒 0.000）
    right_hard_shoulder_width_m       numeric(10,4),            -- 第 12 列  实测恒 0.750
    right_earth_shoulder_width_m      numeric(10,4),            -- 第 13 列  实测恒 0.750
    elev_diff_01_m                    numeric(10,4),            -- 第 14 列  高差 1（可为负）
    elev_diff_02_m                    numeric(10,4),            -- 第 15 列  高差 2
    elev_diff_03_m                    numeric(10,4),            -- 第 16 列  高差 3
    elev_diff_04_m                    numeric(10,4),            -- 第 17 列  高差 4
    elev_diff_05_m                    numeric(10,4),            -- 第 18 列  高差 5
    elev_diff_06_m                    numeric(10,4),            -- 第 19 列  高差 6
    elev_diff_07_m                    numeric(10,4),            -- 第 20 列  高差 7
    elev_diff_08_m                    numeric(10,4),            -- 第 21 列  高差 8
    elev_diff_09_m                    numeric(10,4),            -- 第 22 列  高差 9
    elev_diff_10_m                    numeric(10,4),            -- 第 23 列  高差 10
    elev_diff_11_m                    numeric(10,4),            -- 第 24 列  高差 11
    remark                            text,
    constraint uq_roadbed_design_point_station unique (section_id, station_id)
);
comment on table roadbed_design_point is
  '逐桩路基设计断面（纬地 .lj 路基设计中间数据文件，教程 §13.6）。24 列照文件原样存。'
  '地面标高/设计标高与 .DMX/.ZDM 重复 —— 为可追溯而照存，重复风险由一致性对账兜住。';
comment on column roadbed_design_point.extra_width_09_m is
  '★待考：文件第 9 列，说明书无对应项，本工程 332 行恒为 0.000。'
  '按对称读法右侧宽度应为「中分带/路面/硬路肩/土路肩」4 项，实测却是 6 项（多两个 0.000）。';
comment on column roadbed_design_point.extra_width_11_m is
  '★待考：文件第 11 列，同上一列。';
comment on column roadbed_design_point.elev_diff_01_m is
  '高差 1（文件第 14 列，可为负）。说明书未说个数与排法。'
  '实测可用 .CTR 的横坡按宽度累计复现，但仅 204/332 行吻合 —— '
  '其余 128 行是超高段，故这组列编码的是**逐桩真实横坡**，可反过来校验 .SUP。';
