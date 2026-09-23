-- ═══════════════════════════════════════════════════════════════════════════════
-- 迁移 94：M 节 earthwork_transfer（土石方调配过程）
--
-- 背景：v0.5 的 10_ddl_v0.5.sql 新开 M 节 1 张表（58 表）。
--       本迁移把**已装好的库**升到同一形状。迁移是**不可变历史**，
--       全新安装以 10_ddl_v0.5.sql 为准，两者必须等价 ——
--       由 tests/contract/test_ddl_migration_parity.py 强制（含约束名与索引名）。
--
-- ★★ 为什么单开 M 节、不并进 L 节：**同一个 .tsf 文件、不同性质的东西**。
--    L1 earthwork_factor 是「**设计输入**」—— 全工程一组系数，用户可在纬地里改。
--    M1 earthwork_transfer 是「**算出来的成果**」—— 27 次调配。
--    一个输入、一个输出，故分节。
--
-- ★★ 锚 section_id，**不锚 design_project_id**（用户定的「乙」）—— 按内容走：
--    桩号 0 ~ 5805.421 km 正好覆盖路段 6 的全长，所以它是路段级数据。
--    仓库里所有带桩号的表都锚 section_id，破例会让人没法跟 earthwork_section 对账。
--    ⚠ 代价：纬地的「分段编号」（本工程全 = 1）要映射成 road_section.id；
--      映射错了会被抓 —— 桩号会超出该路段范围。
--
-- ★★ 桩号**不是 .STA 的子集**（实测，这是本表设计的关键）：
--    68 个桩号里 65 个 ⊂ station_sequence，**3 个不在**：
--        273.000 m（最近 .STA 280.000，差 7 m）
--        333.000 m（最近 .STA 340.000，差 7 m）
--        930.000 m（最近 .STA 940.000，差 10 m）
--    .STA 是 20 m 整数倍，这 3 个不是 —— 它们是**调配算法算出来的分段边界**，
--    不是测量桩号，故合法地落在两个 .STA 桩号之间。
--    → 本表**直接带 station_km**，不锚 station_sequence（与 I 节 .CTR 同一条规矩）。
--
-- ★★ `source_kind`（源列 `坑`）不是「坑的类型」，是「土从哪来」：
--    0 = 路段内调运（取土段是一个**段**）；1 = 从取土坑取土（取土段退化为一个**点**）。
--    实测 23 行为 0、4 行为 1（GCID 23/24/26/27，取土点都是 4100.000 m，
--    与 `取土坑.上路桩号 = '4100.000'` 完全一致）。
--    ⚠ 我一开始按「坑的类型」写注释 —— 是「取土段 S==E」这个实测事实把它推翻的。
--
-- ★★ 六分类的来历：源列名自己就说清楚了 —— `用土1/2/3` + `用石4/5/6`，
--    数字 1–6 连续，正好对上 earthwork_composition.pct_1..6（松土/普通土/硬土/
--    软石/次坚石/坚石）。不是猜的。
--
-- ★★ 精度：桩号 numeric(12,6)（1 mm，与全库 33 处一致）；
--    体积 numeric(14,4)（实测最大 113086.35 m³）；运距 numeric(12,4)（最大 3999.89 m）。
--    ⚠ 运距**不等于两段中心的直线距离**（实测差最多 128 m）—— 纬地算的是沿路加权运距，
--      故**原样存、不重算**：重算会得到一个「看起来对、其实不是纬地那个数」的值。
--
-- 幂等：重复执行安全（IF NOT EXISTS）。回滚 = DROP TABLE earthwork_transfer;
-- ═══════════════════════════════════════════════════════════════════════════════

begin;

CREATE TABLE IF NOT EXISTS earthwork_transfer (
    id                            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id                    bigint NOT NULL REFERENCES road_section(id),
    transfer_no                   integer NOT NULL,
    section_seq                   smallint,
    cut_start_km                      numeric(12,6) NOT NULL,
    cut_end_km                        numeric(12,6) NOT NULL,
    fill_start_km                     numeric(12,6) NOT NULL,
    fill_end_km                       numeric(12,6) NOT NULL,
    used_soil_m3                      numeric(14,4),
    used_rock_m3                      numeric(14,4),
    used_soil_compacted_m3            numeric(14,4),
    used_rock_compacted_m3            numeric(14,4),
    used_class_1_m3                   numeric(14,4),
    used_class_2_m3                   numeric(14,4),
    used_class_3_m3                   numeric(14,4),
    used_class_4_m3                   numeric(14,4),
    used_class_5_m3                   numeric(14,4),
    used_class_6_m3                   numeric(14,4),
    used_class_1_compacted_m3         numeric(14,4),
    used_class_2_compacted_m3         numeric(14,4),
    used_class_3_compacted_m3         numeric(14,4),
    used_class_4_compacted_m3         numeric(14,4),
    used_class_5_compacted_m3         numeric(14,4),
    used_class_6_compacted_m3         numeric(14,4),
    haul_soil_m                       numeric(12,4),
    haul_rock_m                       numeric(12,4),
    haul_class_1_m                    numeric(12,4),
    haul_class_2_m                    numeric(12,4),
    haul_class_3_m                    numeric(12,4),
    haul_class_4_m                    numeric(12,4),
    haul_class_5_m                    numeric(12,4),
    haul_class_6_m                    numeric(12,4),
    source_kind                   smallint,
    remark                        text,
    CONSTRAINT uq_earthwork_transfer_no UNIQUE (section_id, transfer_no)
);

COMMENT ON TABLE earthwork_transfer IS '土石方调配过程（纬地 HintTF 的 .tsf「过程」表）★设计**成果**（不是输入）。一行 = 一次调配：哪段土运到哪段、多少方、运多远。★锚 section_id（按内容走：桩号 0~5805.421 正好覆盖一个路段全长），**不锚 station_sequence** —— 实测 68 个桩号里 3 个不在 .STA 里（273/333/930 m），是调配算法算出的分段边界。⚠ 本表是 `design_control`、`earthwork_factor` 之后第三个「段名 ≠ 单张物理表名」的例外（.tsf 是 1 文件 ↔ 多表）';
COMMENT ON COLUMN earthwork_transfer.transfer_no IS '纬地的调配序号（源列 GCID，实测 1–27，每行一个、无重复）。';
COMMENT ON COLUMN earthwork_transfer.section_seq IS '纬地的**分段编号**（源列 分段编号，本工程全为 1）。⚠ 是纬地的序号，不是本库的 road_section.id —— 由落库器映射，映射错了会被桩号越界抓住。';
COMMENT ON COLUMN earthwork_transfer.cut_start_km IS '取土段起点桩号 km（源列 取土段S）。⚠ source_kind=1 时它与 cut_end_km **相等** —— 取土坑退化成一个点。';
COMMENT ON COLUMN earthwork_transfer.source_kind IS '土从哪来（源列 坑）：**0=路段内调运**（挖方段→填方段，取土段是一个段）、**1=从取土坑取土**（取土段退化成一个点）。实测 23 行为 0、4 行为 1（GCID 23/24/26/27，取土点都是 4100.000 m = 取土坑.上路桩号）。★ 不是「坑的类型」—— 一开始我这么以为，是「取土段 S==E」这个实测事实把它推翻的。';
COMMENT ON COLUMN earthwork_transfer.used_soil_m3 IS '用土量 m³（源列 用土，**松方**）。实测 107.93 ~ 113086.35。恒等式（实测验证）：用土1+2+3 == 用土（相对偏差 ~1e-9，是双精度舍入）。';
COMMENT ON COLUMN earthwork_transfer.used_soil_compacted_m3 IS '用土（压实）m³（源列 用土(压实)）。= 用土 ÷ 加权系数（系数见 earthwork_factor）。';
COMMENT ON COLUMN earthwork_transfer.used_class_1_m3 IS '松土的用量 m³（源列 用土1）。★ 源列名「用土1/2/3 + 用石4/5/6」数字 1–6 连续，正好对上 earthwork_composition.pct_1..6 的六分类。';
COMMENT ON COLUMN earthwork_transfer.haul_soil_m IS '土方运距 m（源列 土方运距）。实测 17.67 ~ 3999.89。⚠ **不等于两段中心的直线距离**（实测差最多 128 m）—— 纬地算的是沿路加权运距，故**原样存、不重算**：重算会得到一个「看起来对、其实不是纬地那个数」的值。';

commit;
