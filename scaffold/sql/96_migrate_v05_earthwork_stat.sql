-- ═══════════════════════════════════════════════════════════════════════════════
-- 96  升级 v0.5：逐桩土方统计 2 张（O 节）
-- ═══════════════════════════════════════════════════════════════════════════════
--
-- 背景：纬地 HintTF 的 .tsf 里除 L 节「土石系数」、M 节「过程」、N 节「取土坑/弃土坑」，
--       还有两张**逐桩**（每 20 m 一段，334 段）统计表：
--         · 统计扩展        334 行 × 73 列
--         · 土方调配扩展记录  334 行 × 46 列
--
-- 实测（本工程「毕设」）：
--   ★ 两张表桩号区间**完全相同**，但**列完全不重叠** —— 两个维度：
--       统计扩展 = 本桩段「调出去 / 借进来」   ← 调配**流向**
--       调配扩展 = 本桩段「填 / 利用 / 缺」     ← 填方**来源**
--   ★ 总量精确闭合（松方 m³）：调+借 == 缺 == 568907.6536，且 填 == 利+缺（334 行 0 例外）
--   ⚠ 但 调+借 == 缺+利 逐行只成立 281/334 —— 分段口径不同，
--     故**不是** earthwork_transfer 的投影，而是**独立的一层归集**。
--
-- 用户决定：**收**，且「如实标注」—— **73 / 46 列一列不少**，
--   全 0 的列（石方、第 4/5/6 类、弃/调出/调入各组）**照样建列**。
--   少收列 = 静默丢数据；「本工程没用上」和「这一列不存在」是两件事。
--
-- 兼容：纯新增，无破坏性变更。回滚 = DROP TABLE earthwork_haul_stat, earthwork_fill_stat;
-- ⚠ 本文件必须与 10_ddl_v0.5.sql 的 O 节**逐字一致**（含约束名）——
--   tests/contract/test_ddl_migration_parity.py 会拿临时库比对两者。
-- ═══════════════════════════════════════════════════════════════════════════════

begin;

CREATE TABLE IF NOT EXISTS earthwork_haul_stat (
    id                        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        section_id                bigint NOT NULL REFERENCES road_section(id),   -- 锚：由 分段编号 映射而来
    start_station_km          numeric(12,6), -- 源列 起始桩号
    end_station_km            numeric(12,6), -- 源列 终止桩号
    haul_soil_loose_m3        numeric(14,4), -- 源列 调土量松；松方
    haul_rock_loose_m3        numeric(14,4), -- 源列 调石量松；松方；本工程全 0
    haul_loose_1_m3           numeric(14,4), -- 源列 调松1；松方
    haul_loose_2_m3           numeric(14,4), -- 源列 调松2；松方
    haul_loose_3_m3           numeric(14,4), -- 源列 调松3；松方
    haul_loose_4_m3           numeric(14,4), -- 源列 调松4；松方
    haul_loose_5_m3           numeric(14,4), -- 源列 调松5；松方
    haul_loose_6_m3           numeric(14,4), -- 源列 调松6；松方
    haul_1_m3                 numeric(14,4), -- 源列 调1；压实方
    haul_2_m3                 numeric(14,4), -- 源列 调2；压实方
    haul_3_m3                 numeric(14,4), -- 源列 调3；压实方
    haul_4_m3                 numeric(14,4), -- 源列 调4；压实方
    haul_5_m3                 numeric(14,4), -- 源列 调5；压实方
    haul_6_m3                 numeric(14,4), -- 源列 调6；压实方
    borrow_soil_loose_m3      numeric(14,4), -- 源列 借土量松；松方
    borrow_rock_loose_m3      numeric(14,4), -- 源列 借石量松；松方；本工程全 0
    borrow_loose_1_m3         numeric(14,4), -- 源列 借松1；松方
    borrow_loose_2_m3         numeric(14,4), -- 源列 借松2；松方
    borrow_loose_3_m3         numeric(14,4), -- 源列 借松3；松方
    borrow_loose_4_m3         numeric(14,4), -- 源列 借松4；松方
    borrow_loose_5_m3         numeric(14,4), -- 源列 借松5；松方
    borrow_loose_6_m3         numeric(14,4), -- 源列 借松6；松方
    borrow_1_m3               numeric(14,4), -- 源列 借1；压实方
    borrow_2_m3               numeric(14,4), -- 源列 借2；压实方
    borrow_3_m3               numeric(14,4), -- 源列 借3；压实方
    borrow_4_m3               numeric(14,4), -- 源列 借4；压实方
    borrow_5_m3               numeric(14,4), -- 源列 借5；压实方
    borrow_6_m3               numeric(14,4), -- 源列 借6；压实方
    spoil_soil_loose_m3       numeric(14,4), -- 源列 弃土量松；松方
    spoil_rock_loose_m3       numeric(14,4), -- 源列 弃石量松；松方；本工程全 0
    spoil_loose_1_m3          numeric(14,4), -- 源列 弃松1；松方
    spoil_loose_2_m3          numeric(14,4), -- 源列 弃松2；松方
    spoil_loose_3_m3          numeric(14,4), -- 源列 弃松3；松方
    spoil_loose_4_m3          numeric(14,4), -- 源列 弃松4；松方
    spoil_loose_5_m3          numeric(14,4), -- 源列 弃松5；松方
    spoil_loose_6_m3          numeric(14,4), -- 源列 弃松6；松方
    spoil_1_m3                numeric(14,4), -- 源列 弃1；压实方
    spoil_2_m3                numeric(14,4), -- 源列 弃2；压实方
    spoil_3_m3                numeric(14,4), -- 源列 弃3；压实方
    spoil_4_m3                numeric(14,4), -- 源列 弃4；压实方
    spoil_5_m3                numeric(14,4), -- 源列 弃5；压实方
    spoil_6_m3                numeric(14,4), -- 源列 弃6；压实方
    haul_out_soil_loose_m3    numeric(14,4), -- 源列 调出土量松；松方
    haul_out_rock_loose_m3    numeric(14,4), -- 源列 调出石量松；松方；本工程全 0
    haul_out_loose_1_m3       numeric(14,4), -- 源列 调出松1；松方
    haul_out_loose_2_m3       numeric(14,4), -- 源列 调出松2；松方
    haul_out_loose_3_m3       numeric(14,4), -- 源列 调出松3；松方
    haul_out_loose_4_m3       numeric(14,4), -- 源列 调出松4；松方
    haul_out_loose_5_m3       numeric(14,4), -- 源列 调出松5；松方
    haul_out_loose_6_m3       numeric(14,4), -- 源列 调出松6；松方
    haul_out_1_m3             numeric(14,4), -- 源列 调出1；压实方
    haul_out_2_m3             numeric(14,4), -- 源列 调出2；压实方
    haul_out_3_m3             numeric(14,4), -- 源列 调出3；压实方
    haul_out_4_m3             numeric(14,4), -- 源列 调出4；压实方
    haul_out_5_m3             numeric(14,4), -- 源列 调出5；压实方
    haul_out_6_m3             numeric(14,4), -- 源列 调出6；压实方
    haul_in_soil_loose_m3     numeric(14,4), -- 源列 调入土量松；松方
    haul_in_rock_loose_m3     numeric(14,4), -- 源列 调入石量松；松方；本工程全 0
    haul_in_loose_1_m3        numeric(14,4), -- 源列 调入松1；松方
    haul_in_loose_2_m3        numeric(14,4), -- 源列 调入松2；松方
    haul_in_loose_3_m3        numeric(14,4), -- 源列 调入松3；松方
    haul_in_loose_4_m3        numeric(14,4), -- 源列 调入松4；松方
    haul_in_loose_5_m3        numeric(14,4), -- 源列 调入松5；松方
    haul_in_loose_6_m3        numeric(14,4), -- 源列 调入松6；松方
    haul_in_1_m3              numeric(14,4), -- 源列 调入1；压实方
    haul_in_2_m3              numeric(14,4), -- 源列 调入2；压实方
    haul_in_3_m3              numeric(14,4), -- 源列 调入3；压实方
    haul_in_4_m3              numeric(14,4), -- 源列 调入4；压实方
    haul_in_5_m3              numeric(14,4), -- 源列 调入5；压实方
    haul_in_6_m3              numeric(14,4), -- 源列 调入6；压实方
    section_seq               smallint,      -- 源列 分段编号；纬地序号，非本库 id
    remark                    text,
    CONSTRAINT uq_earthwork_haul_stat_station UNIQUE (section_id, start_station_km)
);

CREATE TABLE IF NOT EXISTS earthwork_fill_stat (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        section_id                bigint NOT NULL REFERENCES road_section(id),   -- 锚：由 分段编号 映射而来
    start_station_km         numeric(12,6), -- 源列 起始桩号
    end_station_km           numeric(12,6), -- 源列 终止桩号
    fill_total_loose_m3      numeric(14,4), -- 源列 填方总量松；松方
    fill_soil_loose_m3       numeric(14,4), -- 源列 填土量松；松方
    fill_rock_loose_m3       numeric(14,4), -- 源列 填石量松；松方；本工程全 0
    fill_loose_1_m3          numeric(14,4), -- 源列 填松1；松方
    fill_loose_2_m3          numeric(14,4), -- 源列 填松2；松方
    fill_loose_3_m3          numeric(14,4), -- 源列 填松3；松方
    fill_loose_4_m3          numeric(14,4), -- 源列 填松4；松方
    fill_loose_5_m3          numeric(14,4), -- 源列 填松5；松方
    fill_loose_6_m3          numeric(14,4), -- 源列 填松6；松方
    fill_1_m3                numeric(14,4), -- 源列 填1；压实方
    fill_2_m3                numeric(14,4), -- 源列 填2；压实方
    fill_3_m3                numeric(14,4), -- 源列 填3；压实方
    fill_4_m3                numeric(14,4), -- 源列 填4；压实方
    fill_5_m3                numeric(14,4), -- 源列 填5；压实方
    fill_6_m3                numeric(14,4), -- 源列 填6；压实方
    utilize_soil_loose_m3    numeric(14,4), -- 源列 利土量松；松方
    utilize_rock_loose_m3    numeric(14,4), -- 源列 利石量松；松方；本工程全 0
    utilize_loose_1_m3       numeric(14,4), -- 源列 利松1；松方
    utilize_loose_2_m3       numeric(14,4), -- 源列 利松2；松方
    utilize_loose_3_m3       numeric(14,4), -- 源列 利松3；松方
    utilize_loose_4_m3       numeric(14,4), -- 源列 利松4；松方
    utilize_loose_5_m3       numeric(14,4), -- 源列 利松5；松方
    utilize_loose_6_m3       numeric(14,4), -- 源列 利松6；松方
    utilize_1_m3             numeric(14,4), -- 源列 利1；压实方
    utilize_2_m3             numeric(14,4), -- 源列 利2；压实方
    utilize_3_m3             numeric(14,4), -- 源列 利3；压实方
    utilize_4_m3             numeric(14,4), -- 源列 利4；压实方
    utilize_5_m3             numeric(14,4), -- 源列 利5；压实方
    utilize_6_m3             numeric(14,4), -- 源列 利6；压实方
    deficit_soil_loose_m3    numeric(14,4), -- 源列 缺土量松；松方
    deficit_rock_loose_m3    numeric(14,4), -- 源列 缺石量松；松方；本工程全 0
    deficit_loose_1_m3       numeric(14,4), -- 源列 缺松1；松方
    deficit_loose_2_m3       numeric(14,4), -- 源列 缺松2；松方
    deficit_loose_3_m3       numeric(14,4), -- 源列 缺松3；松方
    deficit_loose_4_m3       numeric(14,4), -- 源列 缺松4；松方
    deficit_loose_5_m3       numeric(14,4), -- 源列 缺松5；松方
    deficit_loose_6_m3       numeric(14,4), -- 源列 缺松6；松方
    deficit_1_m3             numeric(14,4), -- 源列 缺1；压实方
    deficit_2_m3             numeric(14,4), -- 源列 缺2；压实方
    deficit_3_m3             numeric(14,4), -- 源列 缺3；压实方
    deficit_4_m3             numeric(14,4), -- 源列 缺4；压实方
    deficit_5_m3             numeric(14,4), -- 源列 缺5；压实方
    deficit_6_m3             numeric(14,4), -- 源列 缺6；压实方
    section_seq              smallint,      -- 源列 分段编号；纬地序号，非本库 id
    remark                   text,
    CONSTRAINT uq_earthwork_fill_stat_station UNIQUE (section_id, start_station_km)
);

COMMENT ON TABLE earthwork_haul_stat IS '逐桩土方调运统计（纬地 HintTF 的 .tsf「统计扩展」表，334 行 × 73 列）。★ 本桩段「调出去多少 / 借进来多少」—— 调配**流向**。★ 总量与 earthwork_transfer 精确闭合（调+借 = 缺 = 568907.6536 松方 m³），但逐行口径不同（281/334），是**独立的一层归集**，不是 transfer 的投影。⚠ 本工程只用到土的 1/2/3 类，4/5/6（石方）及「弃/调出/调入」三组全 0 —— 列照样建，**不用 ≠ 不存在**。';
COMMENT ON COLUMN earthwork_haul_stat.start_station_km IS '起始桩号 km（源列 起始桩号，源为 m）。';
COMMENT ON COLUMN earthwork_haul_stat.end_station_km IS '终止桩号 km（源列 终止桩号）。';
COMMENT ON COLUMN earthwork_haul_stat.section_seq IS '源列 分段编号：纬地序号，非本库 road_section.id；落库时映射。';
COMMENT ON COLUMN earthwork_haul_stat.haul_soil_loose_m3 IS '源列 调土量松：本桩段**调出去**的土方（松方）。';
COMMENT ON COLUMN earthwork_haul_stat.borrow_soil_loose_m3 IS '源列 借土量松：本桩段**借进来**的土方（松方）。';
COMMENT ON COLUMN earthwork_haul_stat.spoil_soil_loose_m3 IS '源列 弃土量松：⚠ 本工程全 0。';
COMMENT ON COLUMN earthwork_haul_stat.haul_out_soil_loose_m3 IS '源列 调出土量松：⚠ 本工程全 0（与「调土量松」不是同一列，勿混）。';
COMMENT ON COLUMN earthwork_haul_stat.haul_in_soil_loose_m3 IS '源列 调入土量松：⚠ 本工程全 0（与「借土量松」不是同一列，勿混）。';

COMMENT ON TABLE earthwork_fill_stat IS '逐桩填方来源统计（纬地 HintTF 的 .tsf「土方调配扩展记录」表，334 行 × 46 列）。★ 本桩段「填多少 / 其中利用多少 / 缺多少」—— 填方**来源**。★ 恒等式 填 == 利+缺 **334 行 0 例外**。⚠ 本工程只用到土的 1/2/3 类，4/5/6（石方）全 0。';
COMMENT ON COLUMN earthwork_fill_stat.start_station_km IS '起始桩号 km（源列 起始桩号）。';
COMMENT ON COLUMN earthwork_fill_stat.end_station_km IS '终止桩号 km（源列 终止桩号）。';
COMMENT ON COLUMN earthwork_fill_stat.fill_total_loose_m3 IS '源列 填方总量松（松方）。';
COMMENT ON COLUMN earthwork_fill_stat.fill_soil_loose_m3 IS '源列 填土量松（松方）。';
COMMENT ON COLUMN earthwork_fill_stat.utilize_soil_loose_m3 IS '源列 利土量松：填方中**利用**的部分（松方）。';
COMMENT ON COLUMN earthwork_fill_stat.deficit_soil_loose_m3 IS '源列 缺土量松：填方中**缺**的部分（松方）—— 合计 = 568907.6536，与 earthwork_haul_stat 的「调+借」相等。';
COMMENT ON COLUMN earthwork_fill_stat.section_seq IS '源列 分段编号：纬地序号，非本库 road_section.id；落库时映射。';

commit;
