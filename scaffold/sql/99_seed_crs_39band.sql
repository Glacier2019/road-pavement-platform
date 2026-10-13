-- ═══════════════════════════════════════════════════════════════════════════
-- 99  种子 v0.6：把「毕设」项目的坐标基准写进库（契约变更工单 005）
-- ═══════════════════════════════════════════════════════════════════════════
--
-- 背景
-- ---------------------------------------------------------------------------
-- 005 之前，坐标基准只存在于一份文件里：modules/M9-console/config/crs.yaml。
-- 005 按 Q3 裁决把**库**定为权威，文件降为派生产物。于是必须把已经确认过的
-- 那个基准（39 带 / CM 117°E / EPSG 4548 / assumed=false）搬进库里 ——
-- 否则接口在改造后立刻无处取值，五个 /v1/geometry/* 全部 503。
--
-- ★ 为什么是**种子**，不是 sql/NN_migrate_*.sql
-- ---------------------------------------------------------------------------
-- 这条判断有一个实测依据，不是风格偏好：
--
--   `design_project` 这张表**在 sql/90_seed_skeleton.sql 里根本没出现**
--   （`grep -c design_project sql/90_seed_skeleton.sql` = 0）。
--   库里那一行是**导入**来的（source_file='052201341刘其立道路毕设总项目.PRJ'）。
--
--   ⇒ **全新安装的库里没有这一行。** 它是一条不可复现的项目数据。
--
--   所以「把 39 带写进 design_project 第 2 行」这件事：
--     · 它**不是**任何人升级到 v0.6 都要做的动作（别人没有这个项目）；
--     · 它**不该**进迁移历史（迁移是"所有库都会走一遍"的结构演进 —— 宪法 IV）；
--     · 它是**本项目的业务数据准备**，与 sql/98_seed_sim_device.sql 同类。
--
-- ○ 商业化的正确回答（US3 的题目）：客户的项目挂哪个基准，由客户经**写入路径**
--   自己填（M2 数据接入 / 写权守卫登记过的 design_project）。本文件只是把课程
--   项目手上已经确认过的那个值先放进库里，好让 MVP 验得出来。
--
-- ★ 本文件与 sql/90_seed_skeleton.sql 一样**不会自动执行** ——
--   docker-compose.skeleton.yml 只挂载了 10 / 20 / 90 三个文件。
--   98_seed_sim_device.sql 也是手工跑的，这里沿用同一个先例。
--   手工跑法见 specs/005-project-crs-binding/quickstart.md。
--
-- 幂等
-- ---------------------------------------------------------------------------
-- `WHERE crs_epsg IS NULL` 有两个作用，缺一不可：
--   ① 重复执行不产生变化（第二次跑更新 0 行）；
--   ② ★ **不覆盖已经定下来的值** —— 如果客户或别的路径已经写了一个基准，
--      本文件必须让路。一个会覆盖客户数据的"种子"是缺陷，不是便利。
--
-- 定位方式
-- ---------------------------------------------------------------------------
-- 按**业务键** `project_name = '毕设'` 定位，**不按 `id = 2`**。
-- ★ id 是导入时分配的代理键，在别人的库里不一定是 2。按 id 写死的脚本
--   在活库上能跑、在别人的机器上静默更新 0 行 —— 而"更新 0 行"看起来
--   和"本来就不需要更新"一模一样。本仓库已三次栽在"筛错了被读成不存在"上。
-- ═══════════════════════════════════════════════════════════════════════════

UPDATE design_project
   SET crs_epsg                = 4548,
       crs_zone_number         = 39,
       crs_central_meridian_deg = 117,
       crs_zone_width_deg      = 3.00,
       crs_assumed             = false,
       -- ★ FR-015：推断依据 MUST 与基准同存。这里存的就是 crs.yaml 里那 8 条，
       --   **一条不多一条不少** —— 增量会让 T027/T034 的「文件与库一致」判据
       --   变成一句空话（判据要比对一个集合，不是比对一个前缀）。
       -- ★ 确认之后不删：它是这个值的历史，也是复核的起点。
       crs_evidence            = $ev$1. Y 值无带号前缀：实测 534559.2..537679.4 m。若带前缀应约 39534559..39537679
2. Y 距 500 km 假东偏 +34.6..+37.7 km，即距中央子午线 34.6..37.7 km
3. 39 带（117°E）落点为 25.08°N / 117.34°E —— 闽中山区，吻合
4. 40 带（120°E）落点为 25.08–25.21°N / 120.34–120.37°E —— 台湾海峡，约 150 km 离岸，排除
5. 投影长度变形 14.75..17.51 mm/km，满足《公路工程数字勘察通用规范》25 mm/km 上限
6. 地形特征：8 段 255..500 m 半径圆曲线 + 29.75 m 高差 —— 山岭重丘区，与闽中吻合
7. 2026-10-10 平台侧复核：用户确认沿用 39 带（复核 ≠ 设计方确认，故当时仍为 assumed）
8. 2026-10-11 用户确认：带号为真实值，不再是推断。据此 assumed 置 false$ev$
 WHERE project_name = '毕设'
   AND crs_epsg IS NULL;

-- 报出本次动了什么。★ 静默的种子会让人分不清「跑了没生效」与「本来就该这样」。
DO $$
DECLARE
    n_bound  int;
    n_total  int;
BEGIN
    SELECT count(*) INTO n_bound FROM design_project WHERE crs_epsg IS NOT NULL;
    SELECT count(*) INTO n_total FROM design_project;
    RAISE NOTICE '99_seed_crs_39band：design_project 共 % 行，其中已挂坐标基准 % 行', n_total, n_bound;
    IF n_bound = 0 THEN
        RAISE NOTICE '  ⚠ 0 行有基准 —— 若你期待「毕设」这一行被写入，请核对 project_name 是否逐字相符';
    END IF;
END $$;

-- 核对结果（与上面那条的 NOTICE 一起看）
SELECT id, project_name, crs_epsg, crs_zone_number, crs_central_meridian_deg,
       crs_zone_width_deg, crs_assumed
  FROM design_project
 ORDER BY id;

-- ═══════════════════════════════════════════════════════════════════════════
-- ② 路段级：「两级都填、且值相等」的真实样本（T022）
-- ═══════════════════════════════════════════════════════════════════════════
--
-- ★ T022 原文要求把这段写进 `sql/98_migrate_v06_crs_binding.sql`。
--   2026-10-13 按「通用商业化优先」（宪法 VI）改放这里，两条理由：
--
--     ① **本文件自己已经论证过同一件事**，只是当时说的是项目级。
--        98 的后记① 逐字写着「不写 update，不给任何现有行填基准……
--        基准由 T019 作为数据装载单独做」。把路段级塞回 98，
--        等于同一份迁移里前后自相矛盾。
--
--     ② **更重的一条**：路段 6 那组值是本部署的**演示样本**，
--        不是「任何库升级到 v0.6 都该有的东西」。放进迁移，
--        每一个商业部署都会凭空多出一条自己没配过的路段级基准 ——
--        而它看起来完全正常。这正是 004 的教训：看着正常的错值比缺失更危险。
--
-- ★ 样本的作用是什么
--   data-model.md 的 E2 第 1 态：**同一份基准、两级都填、值相等**。
--   这一条要证明的是「路段级填了相同的值，也不改变权威来源是**项目级**」——
--   即路段级是**冗余校验**，不是覆盖。判据 SC-012 / T021 要断言
--   `crs_source` 仍是 `"project"`，而不是 `"section"`。
--   ★ 没有这一行真实数据，那条判据只能在假仓储上成立。
--
-- 幂等：同 ①，`WHERE crs_epsg IS NULL` 让第二次跑更新 0 行，
--       并且**不覆盖**任何已经存在的路段级基准。
--
-- 定位：按业务键 `section_name = '毕设'`，不按 `id = 6`（理由同上面「定位方式」）。
-- ★ 另加一个**行数守卫**：本段只打算匹配 1 行。匹配到 0 行或 2 行以上时
--   大声报出来 —— 静默地更新了 0 行，与「本来就该这样」长得一模一样。
-- ═══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    n_hit int;
BEGIN
    SELECT count(*) INTO n_hit
      FROM road_section
     WHERE section_name = '毕设'
       AND design_project_id IS NOT NULL;

    IF n_hit <> 1 THEN
        RAISE WARNING '99_seed_crs_39band ②：期望恰好 1 条「毕设」路段，实际 % 条 —— '
                      '已跳过路段级样本，不做任何写入', n_hit;
    ELSE
        UPDATE road_section
           SET crs_epsg                = 4548,
               crs_zone_number         = 39,
               crs_central_meridian_deg = 117,
               crs_zone_width_deg      = 3.00,
               crs_assumed             = false,
               crs_evidence            = 'T022 样本：与所属项目同值（39 / 117 / 4548），'
                                         '用于验证 data-model E2 第 1 态 —— '
                                         '路段级填相同的值不改变权威来源，crs_source 仍为 project'
         WHERE section_name = '毕设'
           AND design_project_id IS NOT NULL
           AND crs_epsg IS NULL;      -- ★ 幂等，且绝不覆盖已定下来的值
    END IF;
END $$;

-- 核对：两级的五元组必须**逐位相等**，且 origin 一栏能看出各自来自哪一级。
SELECT 'project' AS origin,
       dp.project_name AS name,
       dp.crs_epsg, dp.crs_zone_number, dp.crs_central_meridian_deg,
       dp.crs_zone_width_deg, dp.crs_assumed,
       dp.crs_evidence IS NOT NULL AS has_evidence
  FROM design_project dp
 WHERE dp.project_name = '毕设'
UNION ALL
SELECT 'section',
       rs.section_name,
       rs.crs_epsg, rs.crs_zone_number, rs.crs_central_meridian_deg,
       rs.crs_zone_width_deg, rs.crs_assumed,
       rs.crs_evidence IS NOT NULL
  FROM road_section rs
 WHERE rs.section_name = '毕设'
   AND rs.design_project_id IS NOT NULL;

