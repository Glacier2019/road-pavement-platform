-- ============================================================================
-- 91_migrate_v05_side_vocab.sql
--   把 cross_section_ground_point.side 的词表，从我自己发明的 'L'/'R'
--   改成**全库其余 7 张 side 表一直在用的 'left'/'right'**。
--
-- 【背景：这个缺陷是怎么来的】
--   `.HDM`（横断面地面线）**源文件里没有任何"侧"的标记**。它靠位置关系表达：
--     第 1 行 桩号
--     第 2 行 左侧那一串测点（<点数>\t<平距>\t<高差>\t<平距>\t<高差>…）
--     第 3 行 右侧那一串测点
--   我写解析器时，两侧是**位置推出来的**，随手给了 'L'/'R' —— 因为脑子里想的是
--   `cross_section_ground_point` 的 check 约束该长什么样，而不是"这个库里
--   别的 side 列长什么样"。结果就是：同一个库里两套侧词表。
--
-- 【实测（迁移前）】
--   roadbed_width                 → left,right      （varchar(8)，无 check）
--   cross_section_ground_point    → L,R             （char(1)，有 check）← 就它不一样
--   其余 6 张带 side 的表          → left,right      （varchar(8)，无 check）
--   全库带 side 列的表共 **8** 张。
--
-- 【⚠ 陷阱：源文件里的 [LEFT]/[RIGHT] 不是库里的值】
--   `.WID`（路幅宽度）源文件确实写 `[LEFT]` / `[RIGHT]`，
--   `10_ddl_v0.5.sql` 里 roadbed_width 那行注释「实测为 [LEFT]/[RIGHT]」说的是
--   **源文件**，不是库里的值 —— 那句话是**对的**，不要"顺手改掉"。
--   解析器负责把源文件的写法归一化成库里的词表。
--   本迁移只动 `cross_section_ground_point`，**不碰**其余 7 张表。
--
-- 【为什么不是"改注释就行"】
--   这不是措辞问题，是**数据值**问题：跨表查"左侧有哪些测点"时，
--   `where side = 'left'` 会在本表上静默返回 0 行。静默 0 行是本项目最怕的
--   失败形态（见项目约定「空转不是通过」）。
--
-- 【回滚】
--   ALTER TABLE cross_section_ground_point DROP CONSTRAINT IF EXISTS ck_cross_section_ground_point_side;
--   ALTER TABLE cross_section_ground_point ADD CONSTRAINT cross_section_ground_point_side_check CHECK (side IN ('L','R'));
--   UPDATE cross_section_ground_point SET side = CASE side WHEN 'left' THEN 'L' WHEN 'right' THEN 'R' ELSE side END;
--   ALTER TABLE cross_section_ground_point ALTER COLUMN side TYPE char(1);
--   （回滚后表就又不一致了 —— 回滚是为了应急，不是推荐状态。）
--
-- 【幂等】
--   列类型转换、UPDATE、加约束都写成可重复执行。重复跑不会二次改名，
--   也不会因为约束已存在而报错。
-- ============================================================================

BEGIN;

-- ① ⚠ **必须先删掉旧的 check 约束**，否则第 ② 步的 UPDATE 会被它拒绝。
--    建表时那条 check 是**内联**写的（`side char(1) ... check (side in ('L','R'))`），
--    PostgreSQL 给它自动起了个名字 `cross_section_ground_point_side_check`。
--    这个坑第一次跑迁移时踩到了：ALTER COLUMN TYPE 成功、UPDATE 报
--    「violates check constraint」，因为 BEGIN 包着，整体回滚、数据没脏。
--    这里按**列**去找（而不是硬编码那一个名字），这样无论约束叫什么都能删掉。
DO $$
DECLARE
    c record;
BEGIN
    FOR c IN SELECT conname FROM pg_constraint
              WHERE conrelid = 'cross_section_ground_point'::regclass
                AND contype  = 'c'
                AND pg_get_constraintdef(oid) ILIKE '%side%'
    LOOP
        EXECUTE format('ALTER TABLE cross_section_ground_point DROP CONSTRAINT %I', c.conname);
        RAISE NOTICE '删掉旧约束：%', c.conname;
    END LOOP;
END $$;

-- ② 列类型对齐其余 7 张表：char(1) → varchar(8)
--    （不换类型的话，'left' 根本存不进去。）
ALTER TABLE cross_section_ground_point
    ALTER COLUMN side TYPE varchar(8);

-- ③ 值改名。只动 'L'/'R'，别的值原样留着（真有第三种值的话，
--    下面第 ⑤ 步的校验会把它抓出来，而不是被这条 UPDATE 悄悄吞掉）。
UPDATE cross_section_ground_point
   SET side = CASE side WHEN 'L' THEN 'left' WHEN 'R' THEN 'right' ELSE side END
 WHERE side IN ('L', 'R');

-- ④ 补上约束，把词表**钉在库里**（原来只有本表有 check，现在仍是本表有；
--    其余 7 张表要不要补是另一个决定，不在本迁移范围内）。
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                    WHERE conname = 'ck_cross_section_ground_point_side'
                      AND conrelid = 'cross_section_ground_point'::regclass) THEN
        ALTER TABLE cross_section_ground_point
            ADD CONSTRAINT ck_cross_section_ground_point_side
            CHECK (side IN ('left', 'right'));
    END IF;
END $$;

-- ⑤ 校验：改完之后本表必须**只有** left/right，且没有漏改的 L/R。
DO $$
DECLARE
    n_bad   bigint;
    n_left  bigint;
    n_right bigint;
    n_old   bigint;
    n_all   bigint;
    vals    text;
BEGIN
    SELECT count(*) INTO n_bad
      FROM cross_section_ground_point
     WHERE side NOT IN ('left', 'right');
    IF n_bad > 0 THEN
        SELECT string_agg(DISTINCT side, ',') INTO vals
          FROM cross_section_ground_point WHERE side NOT IN ('left', 'right');
        RAISE EXCEPTION '仍有 % 行 side 不在 (left,right) 内，实际值：%', n_bad, vals;
    END IF;

    SELECT count(*) FILTER (WHERE side = 'left'),
           count(*) FILTER (WHERE side = 'right')
      INTO n_left, n_right
      FROM cross_section_ground_point;

    SELECT count(*) INTO n_all FROM cross_section_ground_point;

    -- ★★ 空表要放行 —— 这一条是**补的**，原因见文末后记：
    --   原来这里无条件要求两侧都非空，于是这条 migration 在**全新装的库**上
    --   必然报错（DDL 只建表、不插数据）。而全新装恰恰是本迁移该是空操作的情形。
    --   守住的仍然是原来那个用意（"改名没生效"），只是问得更准：
    --   **本来有 L/R 要改**，改完就必须两侧都有数据；本来就没数据，不该拦。
    IF n_all = 0 THEN
        RAISE NOTICE 'cross_section_ground_point 无数据（全新装）—— 无需改名，放行 ✓';
    ELSIF n_left = 0 OR n_right = 0 THEN
        RAISE EXCEPTION '两侧之一为 0 行（left=% right=%）—— 本表有 % 行，'
                        '改名却没有生效', n_left, n_right, n_all;
    ELSE
        RAISE NOTICE 'cross_section_ground_point.side：left=% right=% ✓', n_left, n_right;
    END IF;
END $$;

COMMIT;


-- ══════════════════════════════════════════════════════════════════════════
-- 后记（2026-09 补）：本迁移原先在**全新装的库**上必然失败
-- ══════════════════════════════════════════════════════════════════════════
--
-- 怎么发现的
--   补上了 `tests/contract/test_ddl_migration_parity.py` —— 它建一个一次性库，
--   只跑 `10_ddl_v0.5.sql`，再依次施加 85..91 的全部 migration，比对前后 schema。
--   第一次跑就报：**6/7 条 migration 跑通，91 报错**（就是上面那个 RAISE）。
--
-- 为什么
--   原来的校验无条件要求 left/right 两侧都非空。而全新装的库里
--   `cross_section_ground_point` **一行数据都没有**（DDL 只建表不插数据），
--   于是必然命中 `n_left = 0 OR n_right = 0` → RAISE EXCEPTION。
--
--   作者的用意是对的（"改名没生效"要能查出来），只是问法太粗：
--   它把"本来就没数据"和"有数据但没改成功"当成了同一种情况。
--
-- 改了什么
--   先看全表行数 `n_all`：
--     · n_all = 0  → 全新装，无数据可改，放行（原来会崩）
--     · n_all > 0  → 两侧仍必须都非空，否则报错（守卫原样保留，且把行数写进消息）
--   只放宽了"空表"这一种情形，**对真实升级路径的行为一字未变**。
--
-- 幂等性
--   本文件本来就是幂等的（`DO $$` 里先按列名删旧约束、再改类型、再按值改名），
--   所以这个改动对**已经升级过的库**重跑一次是安全的：表里有数据 → 走 ELSIF 分支，
--   两侧都非空 → 不报错。
