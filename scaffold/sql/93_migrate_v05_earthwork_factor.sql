-- ═══════════════════════════════════════════════════════════════════════════════
-- v0.5 迁移 ⑨③：土石方压实系数（.tsf 的「土石系数」）落表
--
-- 背景 —— 一个新源文件，而且是**另一个产品**的：
--   本仓此前的设计输入全部来自 **HintCAD 道路系统**：`.PRJ/.STA/.JD/.PM/.DMX/.ZDM/
--   .SUP/.WID/.CTR/.tf/.lj/.HDM`，除了 `.HDM/.BDM/.HDMSJ` 少数二进制，**都是文本**。
--   `.tsf` 不同 —— 它属于 **HintTF 土石方调配系统**，是 **Microsoft Access / Jet 4
--   数据库**（魔数 `\x00\x01\x00\x00Standard Jet DB`），2,695,168 B，20 张表。
--
--   ⚠ 我此前把它归进 `_BLOCKED_SUFFIX`，理由是「需 ODBC/Jet 引擎」。**那条理由是错的**：
--     实测 `uv run --with access-parser` 纯 Python 就解出 20 张表，表名和列名**全是中文**。
--     → 不是"结构上读不了"，是"适配器还没写"。已从 blocked 移到 pending。
--     ⚠ 这与 `.dq` 是**同一个缺陷形状、方向相反**：.dq 被误判成"文本"，.tsf 被误判成
--       "读不了"。**两次都是没去读就下了结论。**
--
-- 为什么先只落一张表（土石系数 1 行 6 列）：
--   `.tsf` 实测 20 张表，但**真有数据的只有 4 张**（另有一张 334 行全 0 的中间表）：
--     土石系数        1 × 6      ← 本次
--     过程           27 × 31     「哪段土运到哪段、运多远」
--     统计扩展      334 × 73     逐桩调配成果
--     土石计算      334 × 137    逐桩土石计算（含六类含量/系数/数量/压实）
--   一次只做一张，是为了把整条链路（契约⑤ schema → DDL → 适配器 → 落库 → 测试）
--   **先走通一遍**，再照抄。土石系数是其中最小的一张。
--
-- ★★ 语义不是猜的 —— 实测反推 + 算术验证（这一步必须做，否则就是"看着有、其实含义错"）：
--     `过程`表 GCID=1：`用土 819.1131000000418` → `用土(压实) 707.1642932578042`
--     比值 = 1.1583
--     本表系数 土方1/2/3 = 1.23 / 1.16 / 1.09 对应 松土 / 普通土 / 硬土
--     按 `earthwork_composition`（.CTR 的 TFFD）实测的 20% / 60% / 20% 加权：
--         0.2/1.23 + 0.6/1.16 + 0.2/1.09 = 0.1626 + 0.5172 + 0.1835 = 0.8633
--         819.1131 × 0.8633 = 707.13    ✓ 与实测 707.1643 对得上（差 0.03，舍入）
--     → 用法是 **压实方 = 松方 ÷ 系数**（系数是"松方 ÷ 压实方"的倍数，**不是乘数**）。
--     ★ 六分类与 `earthwork_composition.pct_1..6` **同一套**。
--
-- ★★ 为什么不锚 station_sequence —— 第三种情况：
--     · 逐桩表（.tf/.lj/.HDM）：锚 station_id，因为行数 = .STA 桩号序列
--     · I 节（.CTR）：**有桩号但不同源** —— 分段桩号不是 .STA 的子集，故直接存 station_km
--     · 本表：**根本没有桩号** —— 全工程一组系数。故锚 design_project，UNIQUE 于它。
--
-- ★★ L1 是 `design_control`（1 段 ↔ 9 表）之后**第二个「段名 ≠ 单张物理表名」的例外**：
--     `.tsf` 是 **1 文件 ↔ 多表**。契约⑤ 的 `segments.earthwork_factor` 就是这一段的落点。
--
-- 兼容：纯新增，无破坏性变更。回滚 = DROP TABLE earthwork_factor;
-- ═══════════════════════════════════════════════════════════════════════════════

begin;

CREATE TABLE IF NOT EXISTS earthwork_factor (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    design_project_id  bigint NOT NULL REFERENCES design_project(id),
    factor_soil_1      numeric(8,4),      -- 松土的压实系数（.tsf 列「土方1」）
    factor_soil_2      numeric(8,4),      -- 普通土（.tsf 列「土方2」）
    factor_soil_3      numeric(8,4),      -- 硬土（.tsf 列「土方3」）
    factor_rock_1      numeric(8,4),      -- 软石（.tsf 列「石方1」）
    factor_rock_2      numeric(8,4),      -- 次坚石（.tsf 列「石方2」）
    factor_rock_3      numeric(8,4),      -- 坚石（.tsf 列「石方3」）
    remark             text,
    CONSTRAINT uq_earthwork_factor_project UNIQUE (design_project_id)
);

-- ⚠ 这里必须写**具名**约束（不是 inline UNIQUE）——
--   否则 PG 自动命名与 DDL 里的名字不一致，`tests/contract/test_ddl_migration_parity.py`
--   的「约束快照（含名字）」会红。这一条是被那条测试教出来的（见迁移 ⑨② 的同款教训）。

comment on table earthwork_factor is
  '土石方压实系数（纬地 HintTF 的 .tsf「土石系数」表）★设计输入。'
  '全工程一组、无桩号，故锚 design_project 而非 station_sequence。'
  '用途：松方 ↔ 压实方换算，**压实方 = 松方 ÷ 系数**。'
  '★六分类与 earthwork_composition 同一套（松土/普通土/硬土/软石/次坚石/坚石）。'
  '⚠ 本工程实测：土方 1.23/1.16/1.09、石方 0.92/0.92/0.92。';
comment on column earthwork_factor.factor_soil_1 is
  '松土的压实系数（.tsf 列「土方1」）。用法：压实方 = 松方 ÷ 系数。本工程 1.23';
comment on column earthwork_factor.factor_soil_2 is
  '普通土的压实系数（.tsf 列「土方2」）。本工程 1.16';
comment on column earthwork_factor.factor_soil_3 is
  '硬土的压实系数（.tsf 列「土方3」）。本工程 1.09';
comment on column earthwork_factor.factor_rock_1 is
  '软石的压实系数（.tsf 列「石方1」）。本工程 0.92';
comment on column earthwork_factor.factor_rock_2 is
  '次坚石的压实系数（.tsf 列「石方2」）。本工程 0.92';
comment on column earthwork_factor.factor_rock_3 is
  '坚石的压实系数（.tsf 列「石方3」）。本工程 0.92';
comment on column earthwork_factor.remark is
  '备注。⚠ 本表**不设 CHECK 约束**要求系数 > 0 或 < 1 —— '
  '源文件是设计输入，系数用户可改（纬地官方：「技术指标…用户可以根据需求自行修改调整相关参数」），'
  '硬约束会拒掉合法的中间稿。';

commit;
