-- ═══════════════════════════════════════════════════════════════════════════════
-- 97  升级 v0.6：桩号脱离路段，重锚到路线（契约变更工单 #3）
-- ═══════════════════════════════════════════════════════════════════════════════
--
-- 背景：桩号是野外客观存在的基准，路段是被设计出来的产物。把不变的挂在可变的
--       下面，就会出现「删一个路段，332 个桩号跟着没了」。
--
-- 层级关系（改前 → 改后）：
--   改前： road_section ──< station_sequence        （路段是桩号的**容器**）
--   改后： road_line    ──< station_sequence        （路线是桩号的**锚**）
--          road_section ──> station_sequence        （路段只是**引用**桩号）
--
-- 工单 #3 的四项改动（Q1–Q3 已获导师批准 2026-09-20）：
--   ① section_id           → DROP NOT NULL                    （放宽）
--   ② 新增 line_id         → REFERENCES road_line(id)          （新增，可空）
--   ③ section_id 的 FK     → ON DELETE SET NULL                （放宽）
--   ④ 补部分唯一索引 UNIQUE (line_id, station_local_km)
--                            WHERE line_id IS NOT NULL        （收紧）
--
-- ★ 为什么要 ④（本工单唯一的「收紧」，也是最容易被漏掉的一条）：
--   既有约束 UNIQUE (section_id, station_local_km) 在 section_id 改成可空之后
--   会**静默失效一部分** —— PostgreSQL（及 SQL 标准）里 NULL 在 UNIQUE 中
--   彼此**不相等**，于是下面三条能同时插进去：
--
--       INSERT INTO station_sequence (section_id, station_local_km) VALUES (NULL, 0.000);
--       INSERT INTO station_sequence (section_id, station_local_km) VALUES (NULL, 0.000);
--       INSERT INTO station_sequence (section_id, station_local_km) VALUES (NULL, 0.000);
--
--   受影响的恰好是本次改动要支持的新场景：「路段还没定」的桩号。
--   ④ 把唯一性的**层级**从路段上移到路线 —— 桩号的唯一性本来就该由路线定义。
--   ★ 既有约束**保留不动**：它仍然正确（同一路段内桩号不重复），两条各管一层。
--
-- 兼容：不破坏。列只增不删；约束只放宽；④ 是**新增索引**，只作用于
--       line_id IS NOT NULL 的行，不影响任何既存数据。
--
-- 回滚：见文件末尾「回滚」段。⚠️ 回滚的**唯一陷阱**是：若此时已产生
--       section_id IS NULL 的行，直接 SET NOT NULL 会失败。必须先处理这些行。
--
-- ⚠ 本文件必须与 10_ddl_v0.6.sql 的 station_sequence 段**逐字一致**（含约束名）——
--   tests/contract/test_ddl_migration_parity.py 会拿临时库比对两者。
-- ═══════════════════════════════════════════════════════════════════════════════

begin;

-- ① 桩号不再被路段「拥有」：section_id 改可空
--    先删旧 FK，才能改可空性（PG 不允许直接对有 FK 的列 DROP NOT NULL 之后
--    再补 ON DELETE 动作 —— 分两步做最清晰）。
ALTER TABLE station_sequence DROP CONSTRAINT IF EXISTS station_sequence_section_id_fkey;
ALTER TABLE station_sequence ALTER COLUMN section_id DROP NOT NULL;

-- ② 新增路线锚定列。可空是**有意的**（工单 Q2）：
--    保留「先导桩号文件、后建路线档案」这个合法顺序。
--    强制 NOT NULL 会逼着导入方先造一条占位路线，那是假数据。
ALTER TABLE station_sequence
    ADD COLUMN IF NOT EXISTS line_id bigint REFERENCES road_line(id);

-- ③ 路段消失时**保留桩号**，只把归属置空（FR-002）。
--    RESTRICT 会让「删路段」退化成「必须先手工清理桩号」，等于问题没解决。
ALTER TABLE station_sequence
    ADD CONSTRAINT station_sequence_section_id_fkey
    FOREIGN KEY (section_id) REFERENCES road_section(id) ON DELETE SET NULL;

-- ④ 唯一性上移到路线层级（Q1）。部分索引：只约束已锚定路线的行。
CREATE UNIQUE INDEX IF NOT EXISTS uq_station_line_local
    ON station_sequence (line_id, station_local_km)
    WHERE line_id IS NOT NULL;

-- 索引：按路线查桩号（FR-003 的查询路径）。
CREATE INDEX IF NOT EXISTS idx_station_line_local
    ON station_sequence (line_id, station_local_km);

-- 回填：既有桩号经由 road_section.line_id 反推路线归属。
-- ★ 这是**纯补数据、不猜**：section_id 为空的旧行本就不存在（改前是 NOT NULL），
--   所以这条 UPDATE 覆盖全部既存行；若有 section_id 非空但 road_section 缺失的行
--   才需要人工介入 —— 那种行在改前会被 FK 挡住，不可能存在。
UPDATE station_sequence s
   SET line_id = r.line_id
  FROM road_section r
 WHERE s.section_id = r.id
   AND s.line_id IS NULL;

commit;

-- ═══════════════════════════════════════════════════════════════════════════════
-- 回滚（人工执行，非自动 —— 因为要**先看数据**再决定怎么收）
-- ═══════════════════════════════════════════════════════════════════════════════
--
-- ⚠️ 陷阱：改前 section_id 是 NOT NULL。若此时库里已有 section_id IS NULL 的行
--    （即「路段被删过、桩号被保留下来」—— 这正是 ③ 的目的），
--    直接 ALTER COLUMN section_id SET NOT NULL 会在生产库上失败并中断。
--
-- 正确顺序：
--
--   -- 第 1 步：先看有多少「无路段」桩号（不修改）
--   SELECT count(*) FROM station_sequence WHERE section_id IS NULL;
--
--   -- 第 2 步：若有，必须**先决定它们的归属**再回滚。二选一：
--   --   (a) 撤销「删路段」这个操作（从备份恢复 road_section 与 section_id）；
--   --   (b) 明确抛弃这些桩号（DELETE）—— 注意这会连带删除
--   --       geometry_point / profile_ground_point / roadbed_design_point /
--   --       earthwork_section 里 station_id 指向它们的行。
--   --   两种都要**显式选择**，不能默认。
--
--   -- 第 3 步：确认 count = 0 之后，才执行下面这段
--   begin;
--   DROP INDEX IF EXISTS uq_station_line_local;
--   DROP INDEX IF EXISTS idx_station_line_local;
--   ALTER TABLE station_sequence DROP CONSTRAINT IF EXISTS station_sequence_section_id_fkey;
--   ALTER TABLE station_sequence ALTER COLUMN section_id SET NOT NULL;
--   ALTER TABLE station_sequence
--       ADD CONSTRAINT station_sequence_section_id_fkey
--       FOREIGN KEY (section_id) REFERENCES road_section(id);
--   ALTER TABLE station_sequence DROP COLUMN IF EXISTS line_id;
--   commit;
--
--   注：既有 UNIQUE (section_id, station_local_km) 全程未动，无需恢复。
-- ═══════════════════════════════════════════════════════════════════════════════