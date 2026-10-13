-- ═══════════════════════════════════════════════════════════════════════════════
-- 98  升级 v0.6：坐标系绑定挂到项目与路段（契约变更工单 005）
-- ═══════════════════════════════════════════════════════════════════════════════
--
-- 背景：平台从 004 起就能算桩号↔经纬度，但**坐标基准只活在一个手写的
--       crs.yaml 里**（`modules/M9-console/config/crs.yaml`），库里的
--       `road_section` / `design_project` 对它一无所知。后果有两个：
--
--         ① 多项目就有多基准。第二个项目（不同带号/不同中央子午线）一进来，
--            文件里那一套就不对了 —— 而库里没有任何地方能说出「这一段用哪套」。
--         ② 「这个基准是确认过的，还是猜的」这个判断只存在于文件里。数据一旦
--            离开这台机器，可信度就丢了。
--
--       所以本迁移把基准**挂到它本来就属于的层级上**：项目级定基准，
--       路段级是可选覆盖。
--
-- ★ 权威字段为什么是 crs_epsg，而不是「带号 + 中央子午线」
--   （2026-10-12 按宪法原则 VI「通用商业化优先」定权）
--
--   中国高斯-克吕格用「带号 + 中央子午线」。但这不是通用记法：
--     · UTM          有另一套带号编号
--     · Lambert 2154 没有「带号」，中央子午线也不是分带的产物
--     · 阿尔伯斯 5070 同上
--     · 地理坐标 4326 两者都没有
--   把带号做成必填字段，等于把数据模型钉死在中国高斯-克吕格上 ——
--   而这是产品要卖到国外时第一个要拆掉的东西。
--
--   所以：`crs_epsg` 是**唯一权威字段**；`crs_zone_number` /
--   `crs_central_meridian_deg` / `crs_zone_width_deg` 是**可空的人类可读注释**。
--   有则须与 SRID 自洽，**没有则不得报错**。
--
-- ★★ 外键不是装饰 —— 它把一条业务规则变成了结构
--
--   FR-005 要求「EPSG 查不到就当场拒绝」。写在应用代码里是**约定**；
--   写成指向 `spatial_ref_sys` 的外键就是**结构**：
--
--       insert into design_project (project_name, crs_epsg) values ('x', 999999);
--       ERROR:  insert or update on table "design_project" violates foreign key
--               constraint "design_project_crs_epsg_fkey"
--       DETAIL: Key (srid)=(999999) is not present in table "spatial_ref_sys".
--
--   实测于 2026-10-12（探针表 + 事务回滚，未动真库）。
--   宪法 II 的原话是「关键在「结构上做不到」，不是「我们约定不这么做」」。
--
--   ★ 前置条件已验：`10_ddl_v0.6.sql` L220 自己就 `CREATE EXTENSION postgis`，
--     所以「只跑 DDL」的库里有 `spatial_ref_sys`，外键建得起来。
--     `spatial_ref_sys.srid` 上有唯一索引 `spatial_ref_sys_pkey`，属主是 `rp`
--     （就是跑迁移的这个角色），所以不需要额外授权。
--
-- 幂等性（契约② 补漏 `m1-装库一致` 的不变量）
-- -------------------------------------------
--   对**已经是最新 DDL** 的库，本迁移必须是**空操作**。用最新 DDL 建出来的库里
--   六列已在、两个外键已在，于是：
--     · `ADD COLUMN IF NOT EXISTS` 全部跳过
--     · 外键用 `if not exists (select ... from pg_constraint)` 守卫后跳过
--   两条路径得到的约束**同名**（`<表>_<列>_fkey`，PostgreSQL 的自动命名规则），
--   所以逐项比对无差异。
--
--   ★ 这也是为什么外键**不写在内联 `references` 里**：内联会由 PostgreSQL
--     自动命名，而显式 `ADD CONSTRAINT` 能钉死名字。两条路径要同名，就得
--     至少有一边是显式的 —— 这里两边都显式对齐到同一个自动命名规则。
--
-- 回滚
-- ----
--   ALTER TABLE design_project DROP COLUMN IF EXISTS crs_assumed, ... ;（六列）
--   ALTER TABLE road_section   DROP COLUMN IF EXISTS crs_assumed, ... ;（六列）
--   ★ 回滚会丢基准。它没有备份来源 —— 值只能从 crs.yaml 或人工重新录入。
--     所以本迁移**允许为空、不允许猜**：六列全可空，宁缺勿造。
-- ═══════════════════════════════════════════════════════════════════════════════

-- ── ① 项目级：六列 ────────────────────────────────────────────────────────────

ALTER TABLE design_project
    ADD COLUMN IF NOT EXISTS crs_epsg                 integer,
    ADD COLUMN IF NOT EXISTS crs_zone_number          smallint,
    ADD COLUMN IF NOT EXISTS crs_central_meridian_deg numeric(9,6),
    ADD COLUMN IF NOT EXISTS crs_zone_width_deg       numeric(4,2),
    ADD COLUMN IF NOT EXISTS crs_assumed              boolean,
    ADD COLUMN IF NOT EXISTS crs_evidence             text;

-- ── ② 路段级：同名同类型六列 ──────────────────────────────────────────────────
--    ★ 路段级与项目级是**两份**，不是一份。项目级定基准，路段级是可选覆盖。
--      两级都有且 crs_epsg 不等时由写入侧拒绝（data-model.md I3 / research.md R6）。

ALTER TABLE road_section
    ADD COLUMN IF NOT EXISTS crs_epsg                 integer,
    ADD COLUMN IF NOT EXISTS crs_zone_number          smallint,
    ADD COLUMN IF NOT EXISTS crs_central_meridian_deg numeric(9,6),
    ADD COLUMN IF NOT EXISTS crs_zone_width_deg       numeric(4,2),
    ADD COLUMN IF NOT EXISTS crs_assumed              boolean,
    ADD COLUMN IF NOT EXISTS crs_evidence             text;

-- ── ③ 两个外键：显式命名，与全新装路径同名 ────────────────────────────────────

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'design_project_crs_epsg_fkey'
                     AND conrelid = 'public.design_project'::regclass) THEN
        ALTER TABLE design_project
            ADD CONSTRAINT design_project_crs_epsg_fkey
            FOREIGN KEY (crs_epsg) REFERENCES spatial_ref_sys(srid);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conname = 'road_section_crs_epsg_fkey'
                     AND conrelid = 'public.road_section'::regclass) THEN
        ALTER TABLE road_section
            ADD CONSTRAINT road_section_crs_epsg_fkey
            FOREIGN KEY (crs_epsg) REFERENCES spatial_ref_sys(srid);
    END IF;
END $$;

-- ── ④ 注释：与 DDL 逐字相同 ──────────────────────────────────────────────────
--    ★ 快照只比 列/约束/索引 三项，注释不在比对范围。但**照写** ——
--      注释是给人看的，两条路径得到的表应当读起来一样。

COMMENT ON COLUMN design_project.crs_epsg IS '★权威坐标系：EPSG/SRID。外键指向 spatial_ref_sys，查不到即存不进去';
COMMENT ON COLUMN design_project.crs_zone_number IS '可空注释：带号。只在分带投影族里有意义，没有不得报错';
COMMENT ON COLUMN design_project.crs_central_meridian_deg IS '可空注释：中央子午线。手写换算路径需要它';
COMMENT ON COLUMN design_project.crs_zone_width_deg IS '可空注释：带宽。只在分带投影族里有意义';
COMMENT ON COLUMN design_project.crs_assumed IS '值可不可信：true＝猜的，false＝已确认';
COMMENT ON COLUMN design_project.crs_evidence IS '推断依据。MUST 与基准同存，否则依据会与值分开漂（FR-015）';

COMMENT ON COLUMN road_section.crs_epsg IS '★权威坐标系：EPSG/SRID。外键指向 spatial_ref_sys；与所属项目不等时写入侧报错';
COMMENT ON COLUMN road_section.crs_zone_number IS '可空注释：带号。只在分带投影族里有意义，没有不得报错';
COMMENT ON COLUMN road_section.crs_central_meridian_deg IS '可空注释：中央子午线。手写换算路径需要它';
COMMENT ON COLUMN road_section.crs_zone_width_deg IS '可空注释：带宽。只在分带投影族里有意义';
COMMENT ON COLUMN road_section.crs_assumed IS '值可不可信：true＝猜的，false＝已确认';
COMMENT ON COLUMN road_section.crs_evidence IS '推断依据。MUST 与基准同存（FR-015）';

-- ═══════════════════════════════════════════════════════════════════════════════
-- 后记：本迁移**刻意不做**的三件事
--
--   ① 不写 `update`，不给任何现有行填基准。
--      现有两行路段、一行项目都不带基准。**猜一个填上去，就是造假** ——
--      而且是一个「看起来正常」的错值，比缺失更危险（004 的教训）。
--      基准由 T019 作为**数据装载**单独做，依据写进 crs_evidence。
--
--   ② 不加 `not null`。
--      加了就等于宣布「没有基准的数据不合法」，而「还没标定」是合法状态。
--      宪法接入约束写的是「未标定的阈值不得用于生产判定」—— 它说的是
--      **不得用于判定**，不是**不得存在**。
--
--   ③ 不在这里做「两级一致」检查。
--      那是行间约束。落点是写入侧 + 契约测试整库断言
--      （research.md R6：不给读路径加 compare-and-throw 作为唯一手段）。
--      ★★ 实现地：`sql/25_crs_two_level_guard.sql`（两张表都挂的 BEFORE 触发器）。
--      ⚠️ 005 实施期间**实测发现这句话当时是空的**：写进路段一个不同的基准
--         返回 `UPDATE 1`（成功了）。触发器、check 约束、M2 写路径三处都没有它，
--         只有读取侧 503 在兜底 —— 而「推到写入侧」这句把责任推出去了、
--         没有任何任务接住。⇒ 补上 T047，并**刻意放在 25 号文件 + initdb.d**，
--         不放在本迁移里：迁移从不自动执行，放这里对全新装等于没有守卫。
--
--   ④ 不把「带号 ↔ SRID」的对应关系写成 check 或常量。
--      实测 `SRID = 带号 + 4509` 对 25–45 带成立（21/21 无反例），但那条规律
--      **只对 3 度带族成立**，写进库就是把中国高斯-克吕格钉进 schema。
--      外键指向 `spatial_ref_sys` 已经够 —— 它认的是**全世界的** SRID。
-- ═══════════════════════════════════════════════════════════════════════════════
