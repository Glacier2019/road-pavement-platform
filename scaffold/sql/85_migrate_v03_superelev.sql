-- ============================================================================
-- 迁移：v0.3 GE 域超高相关实体校正（按纬地教程 v5.88 §13.3/§13.4/§13.5）
-- ============================================================================
-- 用途：把**已存在的库**从"旧的 A13/A15 形态"改到"10_ddl_v0.3.sql 的目标形态"。
--
-- ★ 本文件**不在** docker-compose 的挂载清单里（那三个是 10_/20_/90_），
--   所以**全新构建不会执行它** —— 全新构建由 10_ddl_v0.3.sql 直接建成目标形态。
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

COMMIT;
