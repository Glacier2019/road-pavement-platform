-- ============================================================================
-- 25_crs_two_level_guard.sql ｜ 两级坐标基准一致性：**写入侧**守卫
-- ============================================================================
--
-- 背景（一句话）
-- ---------------------------------------------------------------------------
-- `design_project` 和 `road_section` 各自可以配一套坐标基准。一个路段
-- 同时属于一个项目时，**两级都有值就必须相等** —— 这是 Q1b 的决定
-- （data-model.md E2 第 2 态、research.md R6、宪法 VI 第①条）。
--
-- ★ 为什么必须在**库**里做，而不是在某个模块里做
-- ---------------------------------------------------------------------------
-- ① `psql`、`COPY`、任何一个后续模块、任何一个运维脚本都能写这两张表。
--    只在 M2 写路径拦，等于只拦住了**门**，没拦住**墙**。
-- ② 两条真实后果，都不是理论上的：
--      · 一条矛盾的记录可以**永久躺在库里**，而没有任何一层会报错；
--      · ★★ 更危险的是**导出器**会照着它写出一份错的 `crs.yaml` ——
--        文件是派生产物（005 T015/T026），它会**忠实地**把错的抄出去，
--        而下游那四个消费者一句警告都不会有。
-- ③ 读取侧那道 503 是**兜底**，不是栏。quickstart 第 2 步写着：
--    「这一步必须在写入侧拦……不能在读路径拦 —— 读路径拦会让一条路段的
--      坏数据把**整个列表接口**搞挂。」
--
-- ★ 为什么这份文件被**两条路径共用**（而且只有一份）
-- ---------------------------------------------------------------------------
-- 全新装：本文件挂在 docker-entrypoint-initdb.d/25_*.sql，随 10/20/90 一起跑。
-- 已装的库：手动跑本文件（幂等，可重复执行）。
-- ★★ 不把它拆成「DDL 里一份 + 迁移里一份」：两份定义必然各自漂，
--    而漂掉的那一半**不会报错**，只会少拦一种情况。
-- ★★★ 注意：**迁移从不自动执行**（compose 只挂 10/20/90）。
--    所以「只写迁移」对全新装等于**没有守卫** —— 这正是本文件必须进
--    initdb.d 的原因。
--
-- 覆盖的四种组合（data-model.md E2）
-- ---------------------------------------------------------------------------
--   ① 两级都有且**相等**      → 通过，来源仍算 project
--   ② 两级都有且**不等**      → ★ 报错，且 MUST 说出两级各是什么
--   ③ 项目有、路段没填        → 通过（取项目）
--   ④ 项目没填、路段有        → 通过（取路段）
--   ⑤ 两级都没填              → 通过（「有类型的没有」，由读路径给 503）
-- ⇒ 只有第 ② 种会被拦。本文件**只管拦**，不做自动填充、不做取其一。
--
-- ★ 触发器的**对称性**（这一点容易漏）
-- ---------------------------------------------------------------------------
-- 只在 `road_section` 上挂触发器是**不够的**：`UPDATE design_project
-- SET crs_epsg=...` 同样能造出一对矛盾的值，而且它改的是**全项目所有
-- 路段**的依据。所以两张表上都要有：写项目时扫它名下所有已配基准的路段，
-- 写路段时查它所属项目。
--
-- 幂等：可重复执行。重复执行不会叠加触发器（DROP IF EXISTS 在前）。
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 0) 装上之前先查一遍：库里**已经**矛盾的话，不许假装装上就好了
-- ---------------------------------------------------------------------------
-- ★ 一个「装的时候库本来就是坏的」的守卫，表现是**永远绿** ——
--   因为矛盾的行早就存在了，触发器不会回头去管它。
--   所以这里必须**当场拒绝**，让人先修数据。
DO $$
DECLARE
    n_bad integer;
    d text;
BEGIN
    SELECT count(*), string_agg(detail, ' ｜ ')
      INTO n_bad, d
      FROM (
        SELECT format('项目「%s」(id=%s)=EPSG:%s vs 路段「%s」(id=%s)=EPSG:%s',
                      p.project_name, p.id, p.crs_epsg,
                      s.section_name, s.id, s.crs_epsg) AS detail
          FROM road_section s
          JOIN design_project p ON p.id = s.design_project_id
         WHERE s.crs_epsg IS NOT NULL
           AND p.crs_epsg IS NOT NULL
           AND s.crs_epsg <> p.crs_epsg
      ) t;

    IF n_bad > 0 THEN
        RAISE EXCEPTION
            E'装上两级一致性守卫之前，库里已经有 % 处矛盾，先修数据：\n  %\n'
            '（守卫只拦**将来的**写入，不会回头修已经存在的行）', n_bad, d
            USING ERRCODE = '23514';
    END IF;
    RAISE NOTICE '两级一致性预检通过：库里 0 处矛盾。';
END $$;

-- ---------------------------------------------------------------------------
-- 1) 判据本身：一对 (项目, 路段) 该不该拦
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION crs_pair_must_agree(
    p_project_id   bigint,
    p_project_name text,
    p_project_epsg integer,
    p_section_id   bigint,
    p_section_name text,
    p_section_epsg integer
) RETURNS void AS $$
BEGIN
    -- ③④⑤：缺一边、或两边都缺 —— 都不是冲突
    IF p_project_epsg IS NULL OR p_section_epsg IS NULL THEN
        RETURN;
    END IF;
    -- ①：相等，放行
    IF p_project_epsg = p_section_epsg THEN
        RETURN;
    END IF;

    -- ②：★ 报错，而且必须**说得出两级各是什么**（SC-009）
    --    只报「基准冲突」是不够的：接手的人得知道往哪边改、改什么。
    --    ★ 用「项目」「路段」这两个词，而不是「上行/下行」「父/子」——
    --      这是库里的真名，也是 data-model 里的真名。
    -- ★★ 占位符是**裸 `%`**，不是 `%s`。
    --    PostgreSQL 的 RAISE 不是 Python 的 %-格式串：`%` 就是占位符，
    --    后面跟的 `s` 会被当成**字面字母**打出来（`%s` → `毕设s`、`2s`）。
    --    ★ 这个错误特别阴：输出**看起来完全正常**，只有一个多余的 s，
    --      不逐字读就会当成对的发出去。
    --    （对比上面那个 DO 块用的是 `format()`，那里 `%s` 才是对的。）
    RAISE EXCEPTION
        E'坐标基准两级不一致：\n'
        '    项目「%」（id=%）是 EPSG:%\n'
        '    路段「%」（id=%）是 EPSG:%\n'
        '  两级都有值时必须相等。改法：把两级改成同一个，或者清掉其中一级。',
        p_project_name, p_project_id, p_project_epsg,
        p_section_name, p_section_id, p_section_epsg
        USING ERRCODE  = '23514',
              HINT     = 'data-model E2 第 2 态：两级都有且不等 ⇒ 报错，不是取其一。',
              COLUMN   = 'crs_epsg',
              TABLE    = 'road_section',
              CONSTRAINT = 'crs_two_level_must_agree';
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 2) 写「项目」时：扫它名下**所有已配基准的路段**
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_crs_guard_on_project() RETURNS trigger AS $$
DECLARE
    s record;
BEGIN
    FOR s IN SELECT id, section_name, crs_epsg
               FROM road_section
              WHERE design_project_id = NEW.id
                AND crs_epsg IS NOT NULL
    LOOP
        PERFORM crs_pair_must_agree(NEW.id, NEW.project_name, NEW.crs_epsg,
                                    s.id, s.section_name, s.crs_epsg);
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 3) 写「路段」时：查它所属项目
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION trg_crs_guard_on_section() RETURNS trigger AS $$
DECLARE
    p record;
BEGIN
    -- ④⑤：没有所属项目 —— 路段级基准单独生效，没有可比的一级
    IF NEW.design_project_id IS NULL THEN
        RETURN NEW;
    END IF;

    SELECT id, project_name, crs_epsg INTO p
      FROM design_project WHERE id = NEW.design_project_id;

    -- 项目不存在：这属于外键该管的事，不在本守卫的职责里
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;

    PERFORM crs_pair_must_agree(p.id, p.project_name, p.crs_epsg,
                                NEW.id, NEW.section_name, NEW.crs_epsg);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- 4) 挂上（BEFORE，这样报错时那一行还没写进去）
-- ---------------------------------------------------------------------------
DROP TRIGGER IF EXISTS crs_two_level_guard_project ON design_project;
CREATE TRIGGER crs_two_level_guard_project
    BEFORE INSERT OR UPDATE ON design_project
    FOR EACH ROW EXECUTE FUNCTION trg_crs_guard_on_project();

DROP TRIGGER IF EXISTS crs_two_level_guard_section ON road_section;
CREATE TRIGGER crs_two_level_guard_section
    BEFORE INSERT OR UPDATE ON road_section
    FOR EACH ROW EXECUTE FUNCTION trg_crs_guard_on_section();

COMMENT ON FUNCTION crs_pair_must_agree(bigint, text, integer, bigint, text, integer) IS
    'E2 第 2 态：两级都有且 crs_epsg 不等 ⇒ 报错，且说得出两级各是什么（SC-009）。'
    '其余四态一律放行 —— 本函数只拦，不填、不选。';
