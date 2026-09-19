-- ============================================================================
-- 迁移：v0.3 GE 域实体校正（按纬地教程 v5.88 §13.3/§13.4/§13.5）
-- ============================================================================
-- 用途：把**已存在的库**从"旧的 A13/A15 形态"改到"10_ddl_v0.5.sql 的目标形态"。
--
-- ★ 本文件**不在** docker-compose 的挂载清单里（那三个是 10_/20_/90_），
--   所以**全新构建不会执行它** —— 全新构建由 10_ddl_v0.5.sql 直接建成目标形态。
--   本文件只用于**已有数据的库**，必须手工执行。
--
-- 执行：
--   docker exec -i rp-pg psql -U rp -d road_pavement -v ON_ERROR_STOP=1 \
--     < scaffold/sql/85_migrate_v03_superelev.sql
--
-- 幂等：全部 IF EXISTS / IF NOT EXISTS，重复执行无副作用。
--
-- 为什么不能"删卷重建"：实库**已有真实数据**（实测 station_sequence 332 行、
--   alignment_element 33、alignment_pi 8、profile_grade_point 12、
--   profile_ground_point 332）。删卷会连设计导入的成果一起丢掉。
--
-- 改动内容（依据教程原文，逐条）：
--   §13.3 .ZDM 每行第 4、5 项是「标高错台位置的桩号及错台的标高差值」→ A13 补两列
--   §13.4 .wid 的 7 列**全是宽度、没有坡度** → A15 的 crossfall_pct 来源注错，删除
--   §13.5 .sup 每行六列横坡「绕桩号左右对称」，行车道横坡才是超高
--         → A15 的 superelev_pct 单列存不下左右，删除；改六列
--         → 新增 A16 superelev_transition 存原始过渡变化点（9999 → NULL）
--   §13.4 .wid 7 列描述**逐桩**路幅宽度（「数据每两行为一组，说明路基一侧某个
--         桩号区间内的路幅宽度变化情况」；每行各有自己的桩号）→ 新增 A17 roadbed_width
--         为什么必须提前落地：section_design_attr.roadway_width_m 是**标量**，
--         而路幅宽度本来就随桩号变（加宽/匝道/交叉口/变速车道），标量装不下
--         分段变化。按「存设计输入、导出派生量」：.WID 是输入（A17），
--         roadway_width_m 是导出的派生标量。
-- ============================================================================

BEGIN;

-- ── ① A13 profile_grade_point：补错台两列（教程 §13.3）──────────────────────
ALTER TABLE profile_grade_point
    ADD COLUMN IF NOT EXISTS offset_station_km numeric(12,6),
    ADD COLUMN IF NOT EXISTS offset_elev_m     numeric(8,4);

COMMENT ON COLUMN profile_grade_point.offset_station_km IS '标高错台位置桩号（教程 §13.3：.ZDM 每行第 4 项）。错台是互通立交匝道上出现的标高突变；一般公路主线此列为 0';
COMMENT ON COLUMN profile_grade_point.offset_elev_m IS '标高错台高差 m（教程 §13.3：.ZDM 每行第 5 项）。向上错开为正、向下为负；一般公路主线此列为 0。本工程（毕设，二级公路主线）12 个变坡点全为 0';

-- ── ② A15 geometry_point：删掉来源/语义有误的两列（教程 §13.4/§13.5）────────
-- 实测该表 0 行，删列不丢数据；即便如此仍用 IF EXISTS，保证可重复执行。
ALTER TABLE geometry_point
    DROP COLUMN IF EXISTS superelev_pct,
    DROP COLUMN IF EXISTS crossfall_pct;

-- ── ③ A15 geometry_point：按教程 §13.5 加六列横坡（左右对称，缺一不可）──────
ALTER TABLE geometry_point
    ADD COLUMN IF NOT EXISTS earth_shoulder_left_pct  numeric(5,2),
    ADD COLUMN IF NOT EXISTS hard_shoulder_left_pct   numeric(5,2),
    ADD COLUMN IF NOT EXISTS lane_left_pct            numeric(5,2),
    ADD COLUMN IF NOT EXISTS lane_right_pct           numeric(5,2),
    ADD COLUMN IF NOT EXISTS hard_shoulder_right_pct  numeric(5,2),
    ADD COLUMN IF NOT EXISTS earth_shoulder_right_pct numeric(5,2);

COMMENT ON COLUMN geometry_point.lane_left_pct  IS '左侧行车道（路面）横坡 %，即左半幅超高。教程 §13.5 定义 .SUP 第 3 项';
COMMENT ON COLUMN geometry_point.lane_right_pct IS '右侧行车道（路面）横坡 %，即右半幅超高。教程 §13.5 定义 .SUP 第 5 项。上坡路段左右同号，超高段左右异号（单向横坡）';
COMMENT ON TABLE  geometry_point IS '逐桩号线形＝κ(s)/G(s)/E(s) 函数库（课题甲/乙共同消费接口）；κ 来自 .PM+.JD，G 来自 .ZDM，E 来自 .SUP（经 A16 过渡变化点插值到逐桩），三者对齐到 station_sequence 同一基准。对应 ASAM OpenDRIVE 的 s = station_absolute_km × 1000（米）';

-- ── ④ 新增 A16 superelev_transition：超高过渡的原始设计输入 ─────────────────
CREATE TABLE IF NOT EXISTS superelev_transition (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id               bigint NOT NULL REFERENCES road_section(id),
    transition_seq           smallint NOT NULL,
    station_km               numeric(12,6) NOT NULL,
    earth_shoulder_left_pct  numeric(5,2),
    hard_shoulder_left_pct   numeric(5,2),
    lane_left_pct            numeric(5,2),
    lane_right_pct           numeric(5,2),
    hard_shoulder_right_pct  numeric(5,2),
    earth_shoulder_right_pct numeric(5,2),
    remark                   text,
    UNIQUE (section_id, transition_seq)
);
COMMENT ON TABLE superelev_transition IS '超高过渡（.SUP）★设计输入，一行一个过渡变化点。六列横坡绕桩号左右对称；NULL 表示源文件写了 9999「可以忽略此数据」，即该列在此位置不参与约束、过渡照常继续（不是缺值、也不是沿用上值）。教程 §13.5';
CREATE INDEX IF NOT EXISTS idx_superelev_trans_station ON superelev_transition(section_id, station_km);

-- ── ⑤ 两表的**键都改为桩号** ─────────────────────────────────────────────
-- 起因：逐桩数据表应当按桩号寻址（GE 域其余表皆如此，A9 station_sequence 的注释
--       即写明"其余逐桩数据表以 station_id FK 锚定本表"）。原来 A16 用 transition_seq、
--       A17 用 (side, interval_seq) 并存 start/end 区间 —— 等于在 GE 域**另立一套
--       区间寻址**，且组内两行本来就可不同（列 4：有附加车道时上一行 1/2、下一行 0），
--       折叠成区间必然丢一个值。
--
-- ⑤-1 A16 superelev_transition：换约束，**保数据**（实库有 76 行真实数据）
--      先确认新键成立：实测 count(*)=76 = count(distinct (section_id, station_km))。
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint
               WHERE conname = 'superelev_transition_section_id_transition_seq_key') THEN
        ALTER TABLE superelev_transition
            DROP CONSTRAINT superelev_transition_section_id_transition_seq_key;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'superelev_transition_section_id_station_km_key') THEN
        ALTER TABLE superelev_transition
            ADD CONSTRAINT superelev_transition_section_id_station_km_key
            UNIQUE (section_id, station_km);
    END IF;
END $$;

-- ⑤-2 A17 roadbed_width：**重建**（去掉 start/end，改成一行一个桩号）
--      为什么重建是可接受的：该表是本版当天新建的，实库里只有导入器刚写的 2 行，
--      内容**完全可由 .WID 源文件重导**（不承载任何不可再生的信息）。
--      重建后需重新导入一次 .WID —— 这是有意的，不是丢失数据。
DROP TABLE IF EXISTS roadbed_width;

CREATE TABLE IF NOT EXISTS roadbed_width (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id               bigint NOT NULL REFERENCES road_section(id),
    side                     varchar(8) NOT NULL,
    seq_no                   smallint NOT NULL,
    group_seq                smallint NOT NULL,
    station_km               numeric(12,6) NOT NULL,
    median_width_m           numeric(6,3),
    half_carriageway_width_m numeric(6,3),
    extra_lane_flag          smallint,
    hard_shoulder_width_m    numeric(6,3),
    earth_shoulder_width_m   numeric(6,3),
    extra_lane_file          text,
    remark                   text,
    UNIQUE (section_id, side, station_km)
);
COMMENT ON TABLE roadbed_width IS '路幅宽度（.WID）★设计输入，一行 = 一侧的一个桩号。教程 §13.4 的 7 列原样保存。★与 A16 superelev_transition 同形：**键是桩号**，值自本桩号起保持到同侧下一个桩号（分段常量，不是渐变）。区间起终点由相邻两行推得，不落库。★本表**不存**路基总宽：总宽 = 中央分隔带 + 2×(半侧路面 + 硬路肩 + 土路肩)，是跨左右两行的派生量，GENERATED 列也表达不了（生成列不能跨行），故不落库';
COMMENT ON COLUMN roadbed_width.side IS '路基侧别：left 左侧 / right 右侧。教程 §13.4 用一行"Z"/"Y"引出其后数据；实测 6.00 版用 [LEFT]/[RIGHT] 段标题行（教程全文无此写法）';
COMMENT ON COLUMN roadbed_width.station_km IS '★桩号（本行自己的桩号）。⚠ 与 A16 一样**不挂 station_id 外键**：设计变化点的桩号不是 .STA 桩号序列的子集（实测 .SUP 76 点只有 34 点在序列里）';
COMMENT ON COLUMN roadbed_width.group_seq IS '该侧第几个桩号区间（教程 §13.4「数据每两行为一组」的组号）。同一 group_seq 的两行 = 这个区间的起、终点';
COMMENT ON COLUMN roadbed_width.half_carriageway_width_m IS '半侧路面宽度＝行车道＋内侧路缘带（教程 §13.4 第 3 列）。本工程 3.500 m，即路面 2×3.5＝7 m，与 section_design_attr.roadway_width_m=10.00（含硬路肩/土路肩）自洽';
COMMENT ON COLUMN roadbed_width.extra_lane_flag IS '有无附加车道标识（教程 §13.4 第 4 列）：0 无附加车道 / 1 或 2 有；为 2 时其下一行的 0 表示主线外侧路缘带宽度。★这一列**有意**允许同组两行不同，故本表不折叠成区间';
CREATE INDEX IF NOT EXISTS idx_roadbed_width_station ON roadbed_width(section_id, side, station_km);

COMMIT;
