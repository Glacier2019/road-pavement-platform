-- ============================================================================
-- 迁移 90：批次备注里那句等级，补上时点 —— 「几何等级 L3」→「导入当时几何等级 L3」
-- ============================================================================
-- 目标库：road_pavement（v0.5，56 表）
-- 幂等：是（WHERE 要求 remark **以「几何等级 」开头**，改完就不再匹配）
-- 依赖：无（纯数据订正，不动表结构、不动任何列）
--
-- ── 背景：一个真实发生过的误读 ──────────────────────────────────────────
--
-- 毕设路段（road_section.id = 6）有两条批次：
--     id=8    WEIDI-BS-2026-09-17   备注「几何等级 L3｜交点来源 derived｜
--                                      缺口 not_supported×1／source_absent×1｜告警 1」
--     id=155  WEIDI-BS-2026-09-HDM  备注「几何等级 L4｜交点来源 derived｜
--                                      缺口 source_absent×1｜告警 14 条：…」
--
-- **两条都是对的**：
--   · id=8 是 .HDM 适配器**还没写**的时候导的 —— 当时确实只有 L3，
--     缺口确实叫 not_supported（"有源、可解析、只是没写适配器"）。
--   · id=155 是写完 .HDM 之后导的 —— 横断面那一段补上了，等级到 L4，
--     缺口只剩一个 source_absent（.3DR 源文件本工程没生成）。
--
-- 问题不在数字，在**措辞**：id=8 那句原文只写「几何等级 L3」，
-- **读起来像当前状态** —— 而它早就不是了。查库的人看到同一路段两条批次、
-- 两个等级，没有任何线索判断哪个是现在的。
--
-- 这正是本会话反复出现的**同一形状**：一个看起来权威的过期断言。
-- 其它几例：hdm.py 注释里承诺了代码没实现的约束；ER 图上写着 55 而文件名写 42；
-- 提交信息宣称钉住了一条其实没进提交的断言。
--
-- ── 为什么是改措辞，而不是加一列 geometry_level ────────────────────────
--
-- 因为等级是**派生量**，不该存：
--   · 契约⑤ `contracts/design-import/README.md` 明写
--     「geometry_level 几何完整度等级（**推导得出，不允许外部传入**）」；
--   · `adapters/base.py` 的 `derive_level()` 从"哪些段在"直接算出来；
--   · `核查.sql` 里也写着「所以查不到一列叫 geometry_level —— 它由 M6 网关现算」。
-- 加一列会把派生量落库，还会多出一个"什么时候刷新它"的问题。
--
-- 要拿**当前**等级有两条现成的路（都已验证可用）：
--   · M3 `GeRepository` 现算（`rpdao/repo.py` 的 completeness.geometry_level）；
--   · 或由 `design_file.parse_status = 'ok'` 反推段集合，再喂给 `derive_level()`。
--     实测本库反推得 {alignment_element, cross_section, design_control,
--     earthwork_section, profile_grade_point, profile_ground_point,
--     roadbed_design_point, roadbed_width, station_sequence, superelev_transition}
--     → **L4**，与 id=155 那条一致。
-- 两条路都以「当前库里有什么」为准，不受本备注的时点影响 —— 这才是"当前等级"的
-- 正确来源。备注保留的是**历史事实**（那一批导进来时是什么等级），两者用途不同。
--
-- ── ⚠ 陷阱 ──────────────────────────────────────────────────────────────
--
-- ① **只改前缀，不改中间的**。用 `replace(remark, '几何等级 ', '导入当时几何等级 ')`
--    会把正文里可能出现的「几何等级」也一起换掉 —— 所以这里用
--    `'导入当时' || remark` 直接加在**开头**，只影响第一处。
-- ② **必须带 WHERE**：不带就会把已经改过的行再改一遍（「导入当时导入当时几何等级」）。
--    WHERE 的条件是"以「几何等级 」开头"，天然幂等。
-- ③ 别顺手把 id=8 的 L3 改成 L4 —— 那才是**真的篡改历史**。
--    这一批导进来时就是 L3，这个事实不会因为后来补了适配器而改变。
--
-- ── 回滚 ────────────────────────────────────────────────────────────────
--   UPDATE data_import_batch
--      SET remark = substr(remark, length('导入当时') + 1)
--    WHERE remark LIKE '导入当时几何等级 %';
-- ============================================================================

BEGIN;

UPDATE data_import_batch
   SET remark = '导入当时' || remark
 WHERE remark LIKE '几何等级 %';

-- 验证：改完后应当**没有**任何 remark 以「几何等级 」开头（全都被加了前缀），
-- 且改过的行数 = 原本以「几何等级 」开头的行数。
DO $$
DECLARE
    n_left  integer;
    n_fixed integer;
BEGIN
    SELECT count(*) INTO n_left  FROM data_import_batch WHERE remark LIKE '几何等级 %';
    SELECT count(*) INTO n_fixed FROM data_import_batch WHERE remark LIKE '导入当时几何等级 %';
    RAISE NOTICE '迁移 90：仍有旧措辞 % 行（应为 0）；已带时点 % 行', n_left, n_fixed;
    IF n_left <> 0 THEN
        RAISE EXCEPTION '迁移 90 未完成：仍有 % 行 remark 以「几何等级 」开头', n_left;
    END IF;
END $$;

COMMIT;

-- ============================================================================
-- ⚠ 本文件不会被 docker-compose 自动执行
--    compose 只在**数据目录为空**时跑 /docker-entrypoint-initdb.d/ 里的脚本，
--    且 sql/*.sql 是**逐个显式挂载**的（见 scaffold/docker-compose.skeleton.yml）。
--    对已存在的库，必须手工执行：
--      docker exec -i rp-pg psql -U rp -d road_pavement -f - < scaffold/sql/90_migrate_v05_batch_remark.sql
-- ============================================================================
