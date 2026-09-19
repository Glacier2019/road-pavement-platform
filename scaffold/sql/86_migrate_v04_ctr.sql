-- ============================================================================
-- 迁移脚本：v0.3 → v0.4（新增 I 节「设计控制参数（.CTR）」9 张表）
-- ============================================================================
-- 用途：把**已存在的库**补上 v0.4 新增的 9 张表。
--   为什么需要它：compose 只把 sql/*.sql **逐个显式挂载**进
--   /docker-entrypoint-initdb.d/，而那个目录**只在数据目录为空时执行**。
--   所以已建好的库不会自动吃到新 DDL —— 必须手工跑这个脚本。
--   全新构建**不需要**它（10_ddl_v0.4.sql 直接建成目标形态）。
--
-- 幂等：全部 CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS，
--       重复执行不报错、不改动已有数据。
--
-- 兼容：**纯新增**，不触碰 v0.3 的任何一张表、任何一列。
--       回滚 = DROP TABLE slope_segment, ditch_segment, standard_cross_section,
--              roadbed_trench, structure_control, earthwork_composition,
--              land_use_width, extra_fill, design_control_text;
--
-- 依据：纬地教程 v5.88 §13.10《设计参数控制数据文件（*.ctr）》18 类格式 / 36 个关键字。
--       本工程 .CTR 实测 36 个关键字：19 个有数据、17 个为空。
--       下面 9 张覆盖**有数据的全部**；17 个空关键字只在
--       contracts/design-import/README.md 里登记，不建表。
--
-- 执行：docker exec -i rp-pg psql -U rp -d road_pavement -v ON_ERROR_STOP=1 \
--         < scaffold/sql/86_migrate_v04_ctr.sql
-- ============================================================================

BEGIN;

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

COMMIT;

-- ----------------------------------------------------------------------------
-- 跑完后自检（应输出 9）：
--   SELECT count(*) FROM information_schema.tables
--    WHERE table_schema='public'
--      AND table_name IN ('slope_segment','ditch_segment','standard_cross_section',
--                         'roadbed_trench','structure_control','earthwork_composition',
--                         'land_use_width','extra_fill','design_control_text');
-- ----------------------------------------------------------------------------
