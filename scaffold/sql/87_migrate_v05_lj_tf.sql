-- ============================================================================
-- 迁移 86 → 87：v0.4 → v0.5（新开 J 节：逐桩土方断面 .tf + 逐桩路基设计断面 .lj）
-- ----------------------------------------------------------------------------
-- 用法：docker exec -i rp-pg psql -U rp -d road_pavement -v ON_ERROR_STOP=1 \
--         < scaffold/sql/87_migrate_v05_lj_tf.sql
-- 幂等：J 节全部用 `create table if not exists`，重复执行无 ERROR。
-- 兼容：纯新增，无破坏性变更。回滚 = DROP TABLE earthwork_section, roadbed_design_point;
-- ----------------------------------------------------------------------------
-- ⚠ 新建的 sql/*.sql 不会被 compose 自动执行（/docker-entrypoint-initdb.d/ 只在
--   数据目录为空时跑一次），所以必须显式跑本脚本，或把本文件挂进 compose 的 volumes。
-- ============================================================================

BEGIN;

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

COMMIT;

-- 自检：应返回 2 行
-- select relname from pg_class where relname in ('earthwork_section','roadbed_design_point');
