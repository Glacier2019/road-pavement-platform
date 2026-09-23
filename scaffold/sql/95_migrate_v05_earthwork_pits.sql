-- ═══════════════════════════════════════════════════════════════════════════════
-- 迁移 95：N 节 borrow_pit / spoil_pit（取土坑 / 弃土坑）
--
-- 背景：v0.5 的 10_ddl_v0.5.sql 新开 N 节 2 张表（60 表）。
--       本迁移把**已装好的库**升到同一形状。迁移是**不可变历史**，
--       全新安装以 10_ddl_v0.5.sql 为准，两者必须等价 ——
--       由 tests/contract/test_ddl_migration_parity.py 强制（含约束名与索引名）。
--
-- ★★★ 本节的表**几乎全是出厂默认值**，实测：
--    取土坑写 松土/普通土/硬土/软石/次坚石/坚石 = 20/20/20/20/20/0（六项和 = 100，
--    说明是**比例**不是方量）。但 earthwork_transfer 里 坑=0 的 23 行占比 **恒为 20/60/20**、
--    坑=1 的 4 行 **恒为 33.3/33.3/33.3** —— 两个都与它不符。
--    故这个比例**不是**喂进调配算法的设计参数，只是纬地的出厂默认。
--    ⚠ 我一开始按「它驱动了 坑=1 的组成」去验算，被实测否掉了：
--      按 20/20/20/20/20/0 加权算 GCID 23 的 用土(压实) 得 52811.12，实测是 47874.2367；
--      按「逐类 ÷ 系数」（18466.38/1.23 + /1.16 + /1.09）才得 47874.2 ✓。
--
-- ★★ 真值只有桩号：取土坑 4100.000（与 earthwork_transfer 的 source_kind=1 完全一致），
--    弃土坑 1900（没有任何 earthwork_transfer 行指向它）。
--
-- ★★ 占位符 ≈1e11 / ≈1e10 / ≈1e15 是「无限」，不是容量 → 用 numeric(20,4)
--    （numeric(18,4) 只有 14 位整数，装不下 1e15）。这会是全库最宽的 numeric。
--
-- 幂等：重复执行安全（IF NOT EXISTS）。回滚 = DROP TABLE borrow_pit, spoil_pit;
-- ═══════════════════════════════════════════════════════════════════════════════

begin;

CREATE TABLE IF NOT EXISTS borrow_pit (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id            bigint NOT NULL REFERENCES road_section(id),
    pit_no                integer NOT NULL,          -- 源表无此列，落库器按 1 起编
    access_station_km     numeric(12,6),             -- 源列 上路桩号 ★真值
    econ_front_km         numeric(12,6),             -- 源列 前经济分界点桩号
    econ_back_km          numeric(12,6),             -- 源列 后经济分界点桩号
    access_road_length_m  numeric(10,4),             -- 源列 支线长度 ⚠出厂默认 100.0
    pct_1                 numeric(8,3),              -- 源列 松土   ⚠出厂默认 20，**不喂算法**
    pct_2                 numeric(8,3),              -- 源列 普通土 ⚠出厂默认 20
    pct_3                 numeric(8,3),              -- 源列 硬土   ⚠出厂默认 20
    pct_4                 numeric(8,3),              -- 源列 软石   ⚠出厂默认 20
    pct_5                 numeric(8,3),              -- 源列 次坚石 ⚠出厂默认 20
    pct_6                 numeric(8,3),              -- 源列 坚石   ⚠出厂默认 0（六项和 = 100）
    soil_total_m3         numeric(20,4),             -- 源列 土方总量 ⚠⚠「无限」占位符 ≈1e11
    rock_total_m3         numeric(20,4),             -- 源列 石方总量 ⚠⚠「无限」占位符 ≈1e10
    remark                text,
    CONSTRAINT uq_borrow_pit_no UNIQUE (section_id, pit_no)
);
COMMENT ON TABLE borrow_pit IS '取土坑（纬地 HintTF 的 .tsf「取土坑」表）。★★ **本表几乎全是出厂默认值，真值只有 access_station_km**：实测 earthwork_transfer 里 坑=0 的 23 行占比恒为 20/60/20、坑=1 的 4 行恒为 33.3/33.3/33.3，而本表写的 20/20/20/20/20/0 与两者都不符 —— 故 pct_1..6 **不是**喂进调配算法的设计参数，只是出厂默认。真值 access_station_km=4100.000 与 earthwork_transfer 的 source_kind=1 取土点完全一致，source_kind=1 的语义靠它落地。⚠ soil_total_m3/rock_total_m3 是「无限」占位符，不是容量。';
COMMENT ON COLUMN borrow_pit.access_station_km IS '★**真值**：上路桩号 km（源列 上路桩号，实测 4100.000）。与 earthwork_transfer 里 source_kind=1 那 4 行的取土点完全一致 —— source_kind=1（从取土坑取土）的语义就靠它落地。';
COMMENT ON COLUMN borrow_pit.access_road_length_m IS '支线（便道）长度 m（源列 支线长度）。⚠ **出厂默认 100.0** —— 取土坑与弃土坑同值，用户没改过。';
COMMENT ON COLUMN borrow_pit.pct_1 IS '松土占比 %（源列 松土）。⚠⚠ **出厂默认 20，不是喂进算法的参数** —— 实测 坑=1 的 4 行实际用的是 33.3/33.3/33.3（只分三类土、不分石），与本值不符。命名对齐 earthwork_composition.pct_1..6。';
COMMENT ON COLUMN borrow_pit.pct_6 IS '坚石占比 %（源列 坚石）。出厂默认 0 —— 六项之和恰为 100，这也是「它是比例不是方量」的证据。';
COMMENT ON COLUMN borrow_pit.soil_total_m3 IS '土方总量 m³（源列 土方总量）。⚠⚠ **「无限」占位符** ≈1e11（实测 99999892528.71431），**不是容量** —— 故本列 numeric(20,4)，是全库最宽的 numeric。';
COMMENT ON COLUMN borrow_pit.rock_total_m3 IS '石方总量 m³（源列 石方总量）。⚠⚠ **「无限」占位符** ≈1e10（实测 9999999999.0），**不是容量**。';

CREATE TABLE IF NOT EXISTS spoil_pit (
    id                    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    section_id            bigint NOT NULL REFERENCES road_section(id),
    pit_no                integer NOT NULL,          -- 源表无此列，落库器按 1 起编
    access_station_km     numeric(12,6),             -- 源列 上路桩号（实测 1900，没人用）
    econ_front_km         numeric(12,6),             -- 源列 前经济分界点桩号
    econ_back_km          numeric(12,6),             -- 源列 后经济分界点桩号
    access_road_length_m  numeric(10,4),             -- 源列 支线长度 ⚠出厂默认 100.0
    capacity_m3           numeric(20,4),             -- 源列 总容量 ⚠⚠「无限」占位符 ≈1e15
    remark                text,
    CONSTRAINT uq_spoil_pit_no UNIQUE (section_id, pit_no)
);
COMMENT ON TABLE spoil_pit IS '弃土坑（纬地 HintTF 的 .tsf「弃土坑」表）。★ 本工程 1 行且**完全没被用上**：上路桩号实测 1900，而 earthwork_transfer 里没有任何一行指向它（4 条 source_kind=1 全指向取土坑的 4100.000）。与取土坑同样几乎全是出厂默认值/占位符。仍然收它：不收的话，将来出现指向弃土坑的调配行就没有落点；但必须把「这是默认值/占位符」如实标出来，不能让它看起来像设计参数。';
COMMENT ON COLUMN spoil_pit.capacity_m3 IS '总容量 m³（源列 总容量）。⚠⚠ **「无限」占位符** ≈1e15（实测 999999999999999.0），**不是容量** —— 故本列 numeric(20,4)：numeric(18,4) 只有 14 位整数，装不下。';

commit;
