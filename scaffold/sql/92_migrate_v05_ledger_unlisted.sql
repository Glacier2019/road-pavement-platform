-- ═══════════════════════════════════════════════════════════════════════════════
-- v0.5 迁移 ⑨②：design_file 台账补上「磁盘上有、台账看不见」的文件
--
-- 背景 —— **一句话能问倒整套库**：
--   `design_file` 的表名是「设计文件台账」，但它的数据来源**只有一个**：`.PRJ` 的
--   〔文件名〕段。也就是说它回答的是「.PRJ 里写了哪些文件」，**不是**「这个工程
--   有哪些文件」。这两句话实测不等价 —— 磁盘上 20 个文件，`.PRJ`〔文件名〕段只列了
--   18 个（其中 12 个路径为空），而库里 16 行。
--
--   于是「这个工程有哪些文件？哪些我们还没处理？」这句话**答不出来**。
--   我此前只能靠 `ls` 回答 —— 而 `ls` 不是数据库，进不了接口、进不了页面、
--   进不了完整性检查。这是本次一路反复出现的同一个毛病：**静默**。
--
-- 实测（052201341 刘其立毕设，20 个文件 / 9,511 B 的 .PRJ / 458 行）：
--   缺口是**两类**，不是一类 ——
--     ① **声明了但没给号**：`.hda`(涵洞数据 10,557 B)、`.cys`(涵洞系统参数 1,796 B)。
--        它们就排在 `.PRJ`〔文件名〕段的**最后两行**，**有名、有路径**，唯独没有号：
--            `涵洞数据文件(*.hda) = .\毕设.hda`
--            `涵洞系统参数文件(*.cys) = .\毕设.cys`
--        段里其余 28 条都有号（101–125 / 500 / 501 / 28674）。
--     ② **磁盘上有、`.PRJ` 里连提都没提**：`.dtm`(数模本体 33,460 B)、
--        `.tsf`(土石方调配 2,695,168 B)、`.prj`(**总项目文件自己** 9,511 B)。
--        第 ② 类里 `.dtm` 的缺席是**设计如此**，不是漏：纬地的做法是把**数模组**
--        登记进项目管理器（`.gtm` 在〔文件名〕段里，115 号），组里具体哪几个数模
--        由 `.gtm` 自己记（实测它存的是各数模的边界框 + `.DTM` 路径 + 大小）。
--
-- 为什么改的是约束、不是加一张表：
--   「另建一张 design_file_unlisted」能绕开 NOT NULL，但代价是**查"这个工程所有文件"
--   必须 UNION** —— 而 UNION 总有人会忘，忘了就又回到"看不见"。一张表、一句
--   `select * from design_file` 答完，才是这个台账该有的样子。
--
-- 改了两处：
--   ① `file_kind_code` 去掉 NOT NULL。**NULL = 纬地自己没给码**，不是"我们没填"。
--      硬塞一个号就是造假（101–125/500/501/28674 是纬地的号）。
--   ② 冲突键 `(design_project_id, file_kind_code)` 扩成**加 file_name**。
--      两件事要同时满足，只有这个形状能同时满足：
--        · 没码的行**可以有任意多行** —— PG 的 UNIQUE 默认把 NULL 当互不相等，
--          故 (proj, NULL, 'a.PRJ') 与 (proj, NULL, 'b.DTM') 不冲突；
--        · 重导必须**幂等** —— 有码的靠号命中，没码的靠**文件名**命中。
--      ⚠ 我第一版做成了"部分唯一索引 (proj, code) WHERE code IS NOT NULL"，
--        被契约测试当场抓住，两个独立的理由都说明它错：
--          1. `ON CONFLICT` 用不了部分索引（PG 报 InvalidColumnReference）；
--          2. 本仓那条「源码 on_conflict ↔ 实库 pg_constraint」的对账检查
--             **只看约束、不看索引**，换索引它会变红（它是对的）。
--      ⚠ 也试过 `NULLS NOT DISTINCT`（PG15+）：那样只允许**一行** NULL，
--        而本工程就有 3 个没码的文件，全插不进去。
--
-- ⚠ 本迁移在**已经建好的库**上执行；在**全新安装**上它是 no-op（DDL 已含新形态）。
--   两者必须等价 —— `tests/contract/test_ddl_migration_parity.py` 就在钉这件事。
-- ⚠ 结构改了，**数据不在这里补**：新增的行由 `design_import` 在下次导入时写入
--   （导入幂等，重跑即可）。这里故意不 INSERT —— 在迁移里塞工程数据，
--   会让"结构迁移"和"数据装载"混在一起，以后分不清哪行是哪来的。
-- ═══════════════════════════════════════════════════════════════════════════════

begin;

-- ① 允许为空（幂等：已经是可空时，drop not null 是 no-op）
alter table design_file alter column file_kind_code drop not null;

-- ② 冲突键 (proj, code) → (proj, code, file_name)
--    旧约束名是内联 UNIQUE 自动生成的，实测确认过就是下面这个。
--    ⚠ 必须先 drop 再 add：同一个表上留着旧的 (proj, code) 唯一约束，
--      会把"两个没码的行"堵死（NULL 互不相等看似没事，但语义上是多余的枷锁）。
-- 旧约束（原内联 UNIQUE 自动生成的）—— 全新装的 DDL 上已经没有它了，是 no-op
alter table design_file
  drop constraint if exists design_file_design_project_id_file_kind_code_key;

-- ⚠ 这里**不能**写 `drop index if exists uq_design_file_project_kind`。
--   本迁移第一版建过一个同名**部分唯一索引**，我一度加了这句来清它 ——
--   但约束支撑的索引**不允许**用 DROP INDEX 删，PG 直接报错并提示
--   "You can drop constraint … instead"（parity 测试当场抓住）。
--   而第一版那个索引已在实库里手动清掉，这个场景不复存在，故不留这句。
--   → 下面用 `drop constraint if exists`，对"约束"和"没有"两种状态都对。

-- ⚠⚠ 必须先 drop 再 add，**不能只 add**：迁移要能在两种库上跑通 ——
--   ① 旧库：约束叫 design_file_…_file_kind_code_key（上面已 drop），add 新建 ✓
--   ② 全新装的库：DDL 里已经有同名的 uq_design_file_project_kind（显式命名过），
--      只 add 会 "constraint already exists" 直接失败 —— parity 测试当场抓住过。
--   先 drop 后 add 在两种库上都是幂等的，且结果**逐字节相同**。
alter table design_file
  drop constraint if exists uq_design_file_project_kind;

-- ⚠⚠ **NULLS NOT DISTINCT** 是必须的，不是修饰（PG15+）：
--   默认 NULLS DISTINCT 下，没码的行（file_kind_code IS NULL）**永不冲突** ——
--   实测重导一次就多插一行（16→17→18），`ON CONFLICT` 对 NULL 键形同虚设。
--   冲突键里有 file_name，所以 NULL 只是变"可比"，不同文件名仍是不同的键。
alter table design_file
  add constraint uq_design_file_project_kind
  unique nulls not distinct (design_project_id, file_kind_code, file_name);

comment on column design_file.file_kind_code is
  '.PRJ〔文件名〕键号（101–125/500/501/28674）。**NULL = 纬地自己没给码**，'
  '不是"我们没填"——实测有两类文件没有号：① .PRJ 里有名有路径却没给号（.hda/.cys）；'
  '② 磁盘上有、.PRJ 里没提（.dtm/.tsf/.prj 自己）。硬塞一个号就是造假。'
  '具体是哪一种，写在 remark 里。';

commit;
