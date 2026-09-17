# M3 对象域数据访问层（DAO）· 契约③

> 本文件是**契约③（DAO 契约）的真源**。手写摘要见 `contracts/`，机器可读目录见 `catalog.py`。
> 实现：`modules/M3-rpdao/`　验收：`tests/contract/test_dao_contract.py`

---

## 一、这一层解决什么问题

报告 6.1 的接口契约里写着一条铁律：**「应用不直连存储」**。

但骨架期没有 M3，M6（`modules/M6-api/app.py`）只能自己 `import psycopg`、自己持有
`ConnectionPool`、自己内联 SQL —— **架构上写着不许，代码上却只能这么写**。
这是骨架期最典型的一处"文档与实现的静默背离"。

M3 落地后，这件事从"约定"变成**结构上做不到**：

| | 骨架期（无 M3） | 现在（有 M3） |
|---|---|---|
| M6 怎么取数 | 自己建连接池、拼 SQL、执行 | `dao.lo.passages(...)` |
| 拿得到 cursor 吗 | 拿得到 | **拿不到**（`Dao.cursor()` 不对外） |
| 改表结构要动谁 | 动出口服务 | 只动 DAO 内部 |
| 越权查别的域 | 拼个表名就行 | 抛 `ContractViolation` |
| 谁来保证 | 靠自觉 | `test_dao_contract.py` 用 AST 断言 M6 里没有 psycopg |

**边界（刻意划清，不是遗漏）**：M3 是**数据出口**层（契约③原文即"7 域 repository
**数据出口**"）。M2（`modules/M2-ingest`）作为"接入/落库"这个动作本身，仍旧直接写库 ——
它是写入路径，不在本层职责内。写入收口是后续议题（届时 DAO 需扩 `insert_*` 方法）。

---

## 二、对外接口（唯一入口）

```python
from rpdao import Dao

dao = Dao(os.environ["PG_DSN"], app_name="rp-api")
dao.open()                       # 生命周期由调用方管（M6 挂在 FastAPI lifespan 上）

dao.ping()                       # 健康探针，不抛异常
dao.pool_stats()                 # 连接池水位

# 7 个域仓储（大小写皆可）
dao.lo.passages(station="K4640+000", limit=200)   # LO 交通荷载
dao.lo.passage(12345)                           # 单条＋轴明细
dao.lo.daily_summary(date(2026, 9, 15))         # 小时桶＋汇总
dao.ge.sections()                              # GE 道路几何：全部路段
dao.ge.completeness(6)                          #   为什么只有 L2
dao.domain("DE").get("alarm_rule", 1)           # 显式取域
dao.close()
```

### 通用仓储方法（每个域都有）

| 方法 | 说明 |
|---|---|
| `tables` | 本域**已建**的物理表（元组） |
| `logical_entities()` | 本域逻辑视图有、物理表未建的实体（附原因） |
| `list_objects(table, limit=100)` | 按表列对象，`ORDER BY id` |
| `get(table, obj_id)` | 取单条；不存在抛 `NotFound` |
| `count(table)` | 计数 |

### 域特有方法

| 域 | 方法 | 说明 |
|---|---|---|
| LO | `passages(from_ts, to_ts, station, overload_only, limit)` | 过车记录，时间倒序 |
| LO | `passage(record_id)` | 单条＋轴明细（对象-链接展开） |
| LO | `axles(record_id)` | 只要轴明细 |
| LO | `hourly_buckets(day, station)` | 小时聚合桶 |
| LO | `daily_summary(day, station)` | 桶＋汇总（**含超载率与 ESAL 合计口径**） |
| GE | `sections()` | 全部路段（含所属路线/项目/分段属性 + 三类几何计数） |
| GE | `section(section_id)` | 单条路段；不存在抛 `NotFound` |
| GE | `stations(section_id, from_km, to_km, integer_only, limit)` | 桩号序列，可按区间/整桩筛 |
| GE | `alignment(section_id)` | 平面线形：交点链 + 线元链（**一次取回**） |
| GE | `completeness(section_id)` | 几何完整度报告（等级 + 依据 + 缺口） |

> 指标口径（求和、超载占比）刻意放在 DAO 而不是出口服务：它属于**指标语义**，
> 换口径不应改 M6。

### GE 为什么不能照搬 `list_objects`

GE 的数据不是"一堆并列对象"，而是**以 `road_section` 为根的一棵树**：
`station_sequence` / `alignment_pi` / `alignment_element` 全部锚在路段上。
平铺查「所有交点」在只有一个路段时看着没问题，**路段一多就静默串台** ——
把 A 路的交点混进 B 路的线形，而两条路的桩号都从 0 开始，混了也看不出来。
所以 GE 的方法一律**按路段取子树**，不提供无 section 的平铺查。

两个**实测出来的**坑，写在这里以免下次重踩：

1. **锚定列不统一**。多数表锚在 `section_id`；`profile_ground_point` 与
   `geometry_point` 锚在 **`station_id`**（它们是逐桩数据，桩号才是它们的父）。
   所以锚定列声明在 `GeRepository.SEGMENT_ANCHOR` 里、SQL 由 `count_sql()` 生成，
   **不要手写 SQL** —— 手写就会与声明脱钩，而契约测试正是拿声明去核对 DDL 的。
2. **可选筛选项一律显式 cast**（同 `LoRepository.PASSAGES_SQL` 那条）：参数传 NULL 时
   PostgreSQL 无法从 `(%(x)s IS NULL OR ...)` 推断类型，抛 `AmbiguousParameter`，
   接口恒定 500。语法编译查不出、只有真发一次请求才暴露。

### 几何等级是**现算的派生量**，不是存下来的字段

`completeness()` 返回的等级由"哪些段有数据"当场推出，并把依据（每张表的行数）
一并返回，让结论**可被核对**。规则 `LEVEL_RULES` 与 M2 的
`adapters/base._LEVEL_RULES` 必须一致 —— M3 不许 import M2（模块之间只认契约），
所以规则只能各写一遍，**靠契约测试把两遍钉在一起**（`test_dao_contract.py` 第 6 组）。
改一边忘了另一边，测试就红。

缺口分两类报，因为含义不同：`missing_not_built`（schema 没到，如 v0.4 的横断面）
与 `missing_no_data`（表建好了但解析器没做）。混为一谈会让"为什么只有 L2"
得到**错误答案**。

---

## 三、异常契约

DAO 抛**语义化异常**，绝不把 psycopg 的类型泄漏给上层（泄漏了上层就得 import psycopg，
"不直连"就破了）。M6 负责翻译成 HTTP：

| 异常 | 含义 | M6 映射 |
|---|---|---|
| `NotFound` | 对象不存在 | 404 |
| `UnknownDomain` / `UnknownTable` | 未登记的域/表（越白名单即越界） | 404 |
| `ContractViolation` | 越域取数、非法标识符 | 400 |
| `StorageUnavailable` | 连接池取不到连接/超时 | 503 |
| `DaoError`（其它） | 兜底 | 500 |

---

## 四、对象域目录（7 域 ↔ 物理表）

依据报告第五章原文口径：**GE 5 / SU 4 / RE 2 / LO 5 / WE 1 / TE 3 / DE 5 = 25 表**逻辑视图。

| 域 | 中文名 | 已建物理表 | 逻辑视图合计 | 落库 |
|---|---|---|---|---|
| GE | 道路几何 | 14 | 14 | 关系库 |
| SU | 路面表面 | 4 | 4 | 关系库＋对象存储 |
| RE | 结构响应 | 2 | 3 | 时序库（数据）＋关系库（测点元数据） |
| LO | 交通荷载 | 3 | 5 | 关系库（按月分区）＋时序 |
| WE | 环境气象 | 0 | 1 | 时序库 |
| TE | 试验检测 | 3 | 3 | 关系库＋对象存储 |
| DE | 决策输出 | 5 | 5 | 关系库 |

**31 张域内物理表 ＋ 11 张跨域支撑表（字典 5 ＋ 治理/闭环 6）＝ 42**，
与 DDL v0.3 的 42 张基表**逐张对齐**（`test_dao_contract.py` 第 1、2 项断言；
三处真源一致性另有 `test_ddl_dict_catalog.py` 逐表比对）。

### 两张口径不要混

- **7 域逻辑视图 35 表**：各域**实体合计**＝已建物理表 ＋ 逻辑占位（`logical` 字段）。
  v0.3 前为 25 表（域内物理 21 ＋ 逻辑 4）；GE 域新增 10 张物理表 → **35**。
  ⚠ `geometry_point` 原为逻辑占位，**已随 v0.3 落地为物理表**，故不再列于 `logical`。
- **物理 DDL 42 表**（v0.3）：已在 PostgreSQL 实测建的**基表**，含字典与治理支撑表。

差异是真实存在的，故 `Domain` 用具名字段 `tables` / `logical` 分开承载，
**不把"逻辑视图里有"当成"物理表已建"**。查 `logical` 里的实体会得到 `UnknownTable`。

---

## 五、两条硬规矩

1. **不允许在 DAO 之外出现 psycopg。** 由 `test_dao_contract.py` 第 4 项用 AST 断言
   `modules/M6-api/app.py` 里没有 psycopg import / ConnectionPool / `.execute(` / `cursor`。
   *（注：断言必须走 AST 而非正则 —— 正则会把文档字符串里"不再 import psycopg"
   这句话误判成违规，本测试自测时真的踩到过。）*
2. **表名不得由上层拼进来。** 表名无法走参数绑定，故 `quote_ident()` 只放行
   `[a-z_][a-z0-9_]*`，且仓储再校验一次白名单；越域直接抛 `ContractViolation`。

---

## 六、已知欠账（下一步）

1. **写入路径未收口**：M2 仍直接写库（见第一节边界说明）。
2. **RE / WE 无落点**：两域的数据在时序库，而骨架栈没有 IoTDB；本层对它们的
   `tables` 为空、`logical` 有值，是**如实反映**而非遗漏。接 RE/WE 前必须先定时序库选型。
3. **只读连接**：当前 DAO 未强制只读，理论上能执行写 SQL。建议后续用
   `default_transaction_read_only` 把出口层锁成只读，让"出口"名副其实。
