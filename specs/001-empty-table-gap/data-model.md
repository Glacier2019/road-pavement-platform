# Phase 1 Data Model: 空表缺口盘点与接入优先级

**本特性不改任何数据库表结构**（宪法原则 IV：零迁移）。
下面描述的是**计算出来的**数据结构 —— 它们存在于响应里，不落库。

> 为什么不落库："缺口"是**实库状态的一个视图**。存下来就会与实库漂移，
> 而漂移的正是"哪张表有数据"这个每天都在变的事实。

---

## 实体 1：空因枚举 `EmptyReason`

**封闭枚举，扩枚举须走契约变更。**

| 取值 | 中文 | 判定条件（按此顺序短路求值） | 建议动作 |
|---|---|---|---|
| `has_data` | 有数据 | `row_count > 0` | 无需动作 |
| `source_ready_not_imported` | 源已具备·未导入 | `row_count = 0` ∧ 表有对应段 ∧ `implemented` ∧ `source_present` | **跑一次导入** |
| `source_absent` | 源缺失·待接入 | `row_count = 0` ∧ 表有对应段 ∧ `implemented` ∧ ¬`source_present` | 向外部索取源文件 |
| `module_not_built` | 模块未实现 | `row_count = 0` ∧ 表有对应段 ∧ ¬`implemented` | 写适配器 |
| `upstream_pending` | 上游未产出 | `row_count = 0` ∧ **表不对应任何段**（它是下游计算产出） | 等上游模块 |
| `unknown` | 无法判定 | 以上都不匹配 | **人工排查（不得静默）** |

### 不变量

- **INV-1 短路顺序**：必须**先**判 `source_present` 再判 `implemented`。
  理由（已实测踩过）：`weidi/__init__.py` 的注释里记着
  「原来写成『在 IMPLEMENTED 里才报 source_absent，否则报 not_supported』，
  结果 .3DR（本工程没生成）被报成 not_supported，**把真正的原因盖掉了**」。
  诊断必须说**真正**的那一个原因。
- **INV-2 `has_data` 优先**：只要 `row_count > 0` 就报 `has_data`，
  不因"源缺失"或"模块未实现"而改口。表里有数据就是有数据。
- **INV-3 `upstream_pending` 只给"无对应段的表"**：
  这类表（如 `alarm_record`、`diagnosis_result`）是**其它模块算出来的**，
  不是从外部源导入的。把它们判成"源缺失"会误导人去外部找数据。
- **INV-4 `unknown` 不得被吞**：SC-001 要求 100% 有明确分类；
  出现 `unknown` 必须在页面上一眼可见，**不得**默认折叠或计入 `has_data`。

### 元测试要求（宪法原则 V）

对 INV-1 / INV-2 / INV-3 各配一条：构造一个触发**错误**顺序的输入，
断言它会得到**错误**分类（证明这条判定真的在起作用，不是摆设）。

---

## 实体 2：缺口条目 `TableGap`

| 字段 | 类型 | 来源（唯一真源） | 说明 |
|---|---|---|---|
| `table` | str | M3 `ALL_TABLES` | 逻辑表名 |
| `domain` | str \| null | M3 catalog | 7 域之一；跨域支撑表为 `null` |
| `row_count` | int | M3 `count(*)` | 实库行数 |
| `has_data` | bool | `row_count > 0` | |
| `owner` | str \| null | **M3 `TABLE_OWNER`** | 归属模块（FR-003：不另抄一份） |
| `phase` | str \| null | M9 模块登记表 `phase` | P1–P4 |
| `empty_reason` | `EmptyReason` | 拼装计算 | 见上 |
| `segments` | list[str] | M2 capabilities ∧ R1 映射 | 对应哪些段（可空） |
| `suffixes` | list[str] | M2 `SEGMENT_FILES` | 涉及哪些后缀（可空） |
| `source_present` | bool \| null | M2 capabilities | 无对应段时为 `null`（**不是 false**） |
| `action` | str | 由 `empty_reason` 决定 | 建议动作 |
| `priority_keys` | list | 三键（见 research R3） | 排序依据，**必须暴露**以便复核 |

### 不变量

- **INV-5 `source_present` 三态**：`true` / `false` / `null`。
  `null` 表示**该表不对应任何段**（下游产出表）—— 与"源缺失"完全不同。
  把 `null` 压成 `false`，就是 EXT-3 要防的误导。
- **INV-6 `owner` 缺失要如实报**：`TABLE_OWNER` 里没有的表报 `null` ＋ 原因，
  **不得**猜一个模块名填上（FR-010）。
- **INV-7 每张逻辑表恰好一条记录**：62 张表 ⇒ 62 条，不重不漏。
  这条由「`table_census` 的键集 == `ALL_TABLES`」保证（已有测试钉住）。

---

## 实体 3：段能力 `SegmentCapability`（M2 侧）

| 字段 | 类型 | 来源 |
|---|---|---|
| `segment` | str | `CAPABILITIES` |
| `implemented` | bool | `segment in IMPLEMENTED` |
| `suffix` | str | `SEGMENT_FILES[segment][0]` |
| `kind` | str | `SEGMENT_FILES[segment][1]` |
| `source_present` | bool | **现算**：导入目录里有该后缀的文件 |
| `source_file` | str \| null | 命中的文件名（多个时取排序后第一个并**记下重复**） |

### 不变量

- **INV-8 `CAPABILITIES` 与 `IMPLEMENTED` 必须都能报**：两者**故意不同**
  （前者"声称支持"、后者"真做了"）。只报一个就丢失了"声明支持但没解析器"这个真实状态。
- **INV-9 多文件必须记下**：同一后缀有多个文件时，只取一个（确定性），
  但**必须在响应里说明用了哪个、其余几个没进这份 IR** ——
  这是 `weidi/__init__.py` 既有的 `dupes` 做法，本特性沿用，不新造。

---

## 状态转换

本特性**无状态**：每次请求现算，不保存。

因此「表从空变有数据」这个转换**不需要任何代码**：
数据一进库，下一次盘点自动反映（SC-004）。
这也是"不落库"这个决定的具体收益 —— 没有同步问题。

## 关键约束回顾

| 约束 | 来自 | 影响到哪个字段 |
|---|---|---|
| 读只经 M3 | 宪法 II | `row_count` / `owner` 只能来自 M3 |
| 跨模块只认契约 | 宪法 II | `segment` 相关信息只能经 M2 的 HTTP 接口 |
| 实库 > 文档 | 宪法 III | `has_data` 只认 `count(*)`，不认任何文档声明 |
| 反空转 | 宪法 V | `unknown` 不得被吞；每条判定配元测试 |
