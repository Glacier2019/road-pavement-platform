# Implementation Plan: 桩号基准解耦与逐桩线形生成

**Branch**: `002-station-baseline-geometry` | **Date**: 2026-09-27 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/002-station-baseline-geometry/spec.md`

## Summary

本特性做**两件性质不同的事**，必须分开对待 —— 这是规划的第一个判断。

| | 事项 | 性质 | 现状 |
|---|---|---|---|
| **甲** | 桩号基准解耦（桩号脱离路段） | **架构变更**（改外键方向） | 需走工单，导师把关 |
| **乙** | `geometry_point` 逐桩线形 | **派生缓存生成器** | 无外部阻塞，可直接做 |

★ **规划结论：乙先于甲，且甲不阻塞乙。** 理由是实测的：

- 乙的输入（`alignment_element` 33 行、`station_sequence` 332 行、`profile_grade_point` 12 行、
  `profile_ground_point`、`superelev_transition`）**全部已入库**
- 甲要改 `station_sequence.section_id` 外键方向，属**契约②变更**，
  按仓库纪律须走工单由导师把关（冻结设计纪律：「学生不参与设计决策」）
- 若把甲排在乙前面，乙会被一次**审批**卡住 —— 而乙本来今天就能做

**因此本 plan 把甲列为「需工单前置」的独立阶段，乙立刻可执行。**

---

## 一、先把「已有什么」查清（本次实测，2026-09-27）

规划的第一动作不是设计，是**核对既有实现** —— 本次已因此避免两次重复劳动。

### 1.1 已入库的表（`count(*)` 实测）

| 表 | 行数 | 角色 |
|---|---|---|
| `alignment_element` | **33** | A12 平面线形要素 ★真源 |
| `alignment_pi` | **8** | A11 交点（★派生表，`geom.py` 可反算） |
| `station_sequence` | **332** | A10 桩号序列（采样） |
| `profile_grade_point` | **12** | A13 纵断面变坡点 |
| `profile_ground_point` | — | A14 地面线（`.DMX` 逐桩） |
| `cross_section_ground_point` | **2215** | 横断面地面线 |
| **`geometry_point`** | **0** | **A15 本特性目标** |
| `station_equation` | **0** | 断链（空＝正常，本工程无断链） |

### 1.2 已有且**不得重写**的实现

| 文件 | 作用 |
|---|---|
| `adapters/weidi/pm.py`（195 行） | `.PM` → 33 要素单元；含 3 条不变量验证 |
| `adapters/weidi/jd.py`（175 行） | `.JD` → 交点（验算用） |
| `adapters/weidi/sta.py`（114 行） | `.STA` → 桩号 |
| `adapters/weidi/zdm.py`（380 行） | `.ZDM` → 变坡点 |
| `adapters/weidi/sup.py`（292 行） | `.SUP` → 超高过渡变化点 |
| `adapters/weidi/dmx.py`（155 行） | `.DMX` → 地面线 |
| `adapters/geom.py` | 由单元链反算交点（偏差 3×10⁻⁸ m） |

### 1.3 缺口（**只有这三处**）

| # | 缺口 | 对应 FR |
|---|---|---|
| ① | **由要素逐桩求值 → 生成 `geometry_point`** | FR-012~018 |
| ② | 由要素**重采样**，密度可配置 | FR-014 |
| ③ | 按桩号区间查询（索引已建，接口未落地） | FR-018 |

★ **①是核心，②③是它的自然副产品** —— 求值器一旦存在，换密度只是换个步长。

---

## 二、Technical Context

**Language/Version**: Python 3.13（容器内），项目既有约定，不引入新版本

**Primary Dependencies**: FastAPI（M2/M6 既有）、psycopg3（**仅 M3 持有**）、pyyaml

**Storage**: PostgreSQL 16。**本期不新增任何表、不新增列** ——
`geometry_point` 及其全部列**早已存在于 DDL v0.5**，本特性只是**往空表里写**。

**Testing**: 项目自研契约测试框架（`scaffold/tests/contract/*.py`，可离线跑）＋ `run_contract_tests.sh`

**Target Platform**: Linux 容器（docker compose 骨架栈）

**Project Type**: 多模块服务化平台（M1–M10），本特性落在 **M2 / M3 / M6** 三个既有模块内

**Performance Goals**: 332 行逐桩求值在**单次事务内**完成（实测规模极小，无性能压力）；
求值需对缓和曲线做**数值积分**，但 33 个单元 × 332 桩号量级下应为亚秒级

**Constraints**:
- M2 不得 import M3（既有硬线）
- M3 不得读文件系统（既有硬线）
- **写库只能经 M2 的 `WriteGuardError` 守卫路径**
- 新增 HTTP 端点**必须同时登记契约 yaml**，否则 `m6-路由` 测试报「实现缺契约」

**Scale/Scope**: 33 要素单元 → 332 桩号求值；1 张空表填满；1 个新端点；0 张新表

### 2.1 `geometry_point` 的完整规格（**已存在于 DDL，本表照抄自 DDL 而非新设计**）

DDL 第 552–585 行已把这张表定义得很完整，**包括本特性的核心判断**：

> ★**派生缓存**：全部由 A12（κ/坐标/方位角）＋ A13/A14（高程）逐桩算出。
> 之所以"存"而不是"每次算"：缓和曲线要数值积分，逐桩重算代价高 —— 这是合理的缓存，
> 不是冗余。但有两条要求：① 必须**可重建**；② 必须与 A12 **同批次生成**。

**列与来源（照 DDL 注释）：**

| 列 | 来源 | 说明 |
|---|---|---|
| `station_id` | `station_sequence(id)` **UNIQUE** | ★锚在桩号基准上 |
| `x_coord` / `y_coord` | `.PM` | 平面坐标 |
| `azimuth_deg` | `.PM` | 方位角 |
| `curvature_1pm` | `.PM`＋`.JD` 推导 | ★曲率 κ 1/m |
| `design_elev_m` | `.ZDM` 竖曲线 | 设计高程 |
| `ground_elev_m` | `.DMX` | 地面高程 |
| `grade_pct` | `.ZDM` 推导 | ★纵坡 G % |
| `h_radius_m` | `.JD` | 平曲线半径 |
| `v_radius_m` | `.ZDM` | 竖曲线半径 |
| 6 列横坡 | `.SUP` 经 `superelev_transition` **插值** | 左右对称，缺一不可 |

> ⚠ 与 A12 的关键差别（DDL 第 557–558 行）：**本表的 `curvature_1pm` 是逐桩单值，正确**；
> A12 原先那个逐单元单值对缓和曲线是错的，**已删**。

**`station_id` 有 UNIQUE 约束** ⇒ 332 行与 `station_sequence` 是**一一对应**，
这为「行数严格相等」提供了**数据库层保证**，不是靠测试兜底。

---

## 三、Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 本特性如何满足 | 判定 |
|---|---|---|
| **I 契约先行** | 新增端点必须同时登记 `contracts/openapi/m6-gateway.*.yaml`；求值器的输入输出作为契约冻结 | ✅ |
| **II 三条数据硬线** | 求值器在 **M2 内**（它有要素与桩号的解析能力）；写库经 M2 `WriteGuardError`；M3 只出 DAO；M6 只出口。**M3 不读盘、M2 不 import M3** | ✅ |
| **III 证据优先级** | 全部行数/特征**以实库为准**（本 plan 第一节即实库实测）；DDL 注释与实库冲突时修注释 | ✅ |
| **IV 迁移不可变** | **零 schema 变更**。`geometry_point` 早已在 v0.5 中定义，本特性只写数据 | ✅ |
| **V 反空转** | 三条硬断言各配元测试：①「可重建」须能**证伪**（故意篡改一行，重算必须报不一致）②「同批次」须能**证伪**（单独改 A12 不同步，必须被检出）③ 缺源字段须为 NULL，**不得静默填 0**（构造一条缺失数据，必须可见） | ✅ |

**Gate 结论：通过。** 无需要豁免的条款。

### 3.1 关于「甲（桩号解耦）」的宪法处理

甲的 FR-001 要改 `station_sequence.section_id` 为可空 + 新增路线锚定 —— 这是 **schema 变更**。
按原则 IV，**不在本特性内夹带**。处理方式：

1. 本特性**先做乙**（零 schema 变更，Gate 直接通过）
2. 甲**另立工单**，由导师裁决后再排期
3. 在乙中**不做任何假设甲已完成的代码** —— 求值器只读 `station_sequence`，
   不关心它挂在哪（`section_id` 是 6 还是别的），**天然与甲解耦**

★ 第 3 条是关键：**乙的正确性不依赖甲是否实施。**

---

## 四、Project Structure

### Documentation (this feature)

```text
specs/002-station-baseline-geometry/
├── plan.md              # 本文件
├── spec.md              # 规格
├── research.md          # Phase 0 输出
├── data-model.md        # Phase 1 输出
├── quickstart.md        # Phase 1 输出
├── contracts/           # Phase 1 输出
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

`(?)` 标记＝**待 Phase 0 研究确认**的落点；`＋` 标记＝本特性新增。

```text
scaffold/
├── modules/
│   ├── M2-ingest/
│   │   ├── adapters/weidi/
│   │   │   ├── pm.py                     （只读：既有 33 要素解析器，不改）
│   │   │   ├── zdm.py                    （只读：既有变坡点解析器，不改）
│   │   │   ├── sup.py                    （只读：既有超高过渡，不改）
│   │   │   └── dmx.py                    （只读：既有无地面线，不改）
│   │   ├── geometry_solver.py            ← ＋ 新建：要素 → 逐桩求值（本特性核心）
│   │   │                                    · 直线段 κ=0
│   │   │                                    · 缓和曲线段 κ=s/A²（数值积分）
│   │   │                                    · 圆曲线段 κ=1/R
│   │   ├── design_import.py              ← 改：接入 geometry_point 落库（多表同事务）
│   │   └── app.py                        ← ＋ GET /v1/design/geometry (?)
│   ├── M3-rpdao/rpdao/
│   │   └── pool.py                       ← ＋ 查询：按桩号区间取 geometry_point
│   ├── M6-api/app.py                     ← ＋ GET /v1/geom/points (?)
│   └── M9-console/                       （本特性不改页面，M9 为只读透传）
├── contracts/openapi/
│   └── m6-gateway.v0.4.yaml              ← ＋ 新端点契约登记（现版本 v0.3）
└── tests/contract/
    ├── test_geometry_solver.py           ← ＋ 新建：求值正确性 + 3 条元测试
    ├── test_design_import.py             （既有：会强制契约同步）
    └── test_api_routes.py                （既有：会强制契约同步）
```

**Structure Decision**: 沿用既有 M1–M10 划分，**不新增模块**。
求值器放 **M2** 而非 M3，因为：它是**由源文件语义推导**的量（要素类型码、A 值、半径）；
M3 是 DAO，**不许承载解析语义**，且不许读盘。
M2 已持有全部输入解析器，求值器与其同处一地，跨模块接口为零 —— 少一次对接成本。

---

## 五、Phase 0 研究与 Phase 1 设计（产出）

### research.md —— 解决四个 `(?)`

1. **缓和曲线数值积分法**：κ(s) 在缓和段线性变化，但 x/y/E 需积分。
   选哪种（Simpson / Gauss-Legendre / 解析式）？**判据：与既有 `.PM` 端点坐标对比**，
   偏差须 < 1e-6 m。这给出**可证伪的选型标准**，不靠偏好。
2. **`.SUP` 超高插值**：`.SUP` 给的是过渡变化点，需插值到逐桩。
   用线性还是曲线（回旋线）过渡？**判据：`.SUP` 有无过渡段长度与形式字段。**
3. **端点形态**：`/v1/design/geometry` 与 `/v1/geom/points` 是否都需要？
   还是只留 M6 一个（M2 供内部调用）？**判据：M9 是否需要，以及既有 M2/M6 分工惯例。**
4. ~~**批次（batch）的表达**~~ —— ✅ **已查清，不再是待研究项**：
   仓库**已有** `data_import_batch` 表 + `batch_no`，且 `design_import.py:934-935`
   已在**同事务内**登记批次（`tx.insert("data_import_batch", [batch], ...)`，
   与各表落库同一个 `tx`）。
   **所以「同批次生成」现有机制即可表达，无需加列。**
   遗留一个**真实限制**（`design_import.py:949-951` 已自陈）：
   `data_import_batch` 是**采集批次表**，几何等级/缺口只能塞进 `remark`，
   **不可查询**。若「同批次」的校验需要**按批次反查** `geometry_point`，
   这可能需要加列 —— 但那属 schema 变更，按原则 IV **报工单，不擅自扩表**。
   Phase 0 需判定：**现有 remark 是否够用**。

### data-model.md

- `geometry_point` 的 19 列定义、来源、可为空规则（**照 DDL，不重新设计**）
- 「可重建」的不变量：`recompute(A12, A13, A14) ≡ stored`
- 「同批次」的不变量：批次号一致
- 缺源字段的 NULL 语义（**不得填 0**）

### contracts/

- 新端点的响应契约（按 Phase 0 问题 3 的结论定）
- 求值器的输入输出契约

### quickstart.md

可复跑的验收脚本，含三条**可证伪**的硬断言：

```text
① 行数：geometry_point = 332 = station_sequence 行数
② 可重建：重算全表，逐行比对，偏差 0（并演示篡改一行后必须报错）
③ 交叉：单元长度合计 5805.421 m ≡ .STA 末桩号；
        第 1 交点曲线总长 674.493 m ≡ .JD
```

---

## 六、明确不做的事（防范围蔓延）

| 不做 | 理由 |
|---|---|
| 不改 `station_sequence` 外键方向 | 属甲，需工单；**乙不依赖它** |
| 不写 `.3DR` 适配器 | 实测 `source_absent`；但 `geometry_point` **是派生的**，不需要 `.3DR` |
| 不新增表 / 不改 DDL | 原则 IV；`geometry_point` 早已定义完整 |
| 不重写任何既有解析器 | 已实测可用且更严谨（见 1.2） |
| 不填 `design_file.coverage_*` | 属覆盖区间校验（US2/FR-005~008），可独立排期 |

★ 最后一条需说明：**US2（覆盖区间校验）与本 plan 的核心（乙）是两件事**，
可分开实施。本 plan 先做乙，因为乙有明确产出（填满一张空表）；
US2 是**防御性校验**，价值随导入次数增长 —— 建议紧随其后，但不阻塞乙。

---

## 七、可执行的最小下一步

1. Phase 0 研究（三个 `(?)`），**重点是问题 1 的积分法选型** —— 它是本特性唯一的技术风险点
2. 写 `geometry_solver.py`，**先只做直线段与圆曲线段**（κ 恒定，无需积分）
   —— 33 个单元里 17 个是这两类，可先出 17/33 的覆盖并验证管线
3. 加缓和曲线段（数值积分），补齐 33/33
4. 落库 + 三条硬断言

★ 第 2 步是**刻意的增量**：先用无积分部分打通「要素→逐桩→落库→校验」全链路，
再引入数值积分这个唯一的技术风险点。**不要一开始就写积分。**