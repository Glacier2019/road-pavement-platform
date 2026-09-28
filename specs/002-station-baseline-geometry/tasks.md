# Tasks: 桩号基准解耦与逐桩线形生成

**Input**: Design documents from `/specs/002-station-baseline-geometry/`

**Prerequisites**: plan.md ✅、spec.md ✅、research.md ✅

**Tests**: 本特性**必须带测试**。理由：spec 的 FR-010 明文要求「一条元测试证明坏数据会被拒绝」，
SC 又要求「可证伪的硬断言」；且宪法原则 V 规定「一个永远不会失败的检查，比没有检查更糟」。

---

## ★ 执行前必读：本清单的范围已按 research.md 收窄

research.md 的实测结论改变了本特性的工作性质，**执行前务必先读它**：

| 原以为 | 实测 |
|---|---|
| 要新写线形求值器 | `adapters/geom.py` **已有** `curvature_at`/`azimuth_at`/`point_at`/`locate` |
| 要研究积分法选型 | **已解决**，实测最大坐标偏差 **3.171e-08 m**（真实 33 单元） |
| 要先做无积分单元再补积分 | **不必要** —— 积分已实现且验证过 |

**因此本特性没有需要新写的数学。** 核心工作是**接线**：把既有求值器接到落库链路。

### 三条不得违反的既有约定（来自代码注释，违反会静默出错）

1. `radius_start_m = None` 表示**无穷大半径**（κ=0），**不是缺值**
2. 曲率符号来自 `turn_flag`（+1 左转 / −1 右转）；`radius_*_m` 恒为正。
   代码原文：「漏掉转向，右转的曲线会朝反方向弯 —— **实测终点差 98 m，而且不报错**」
3. `locate()` 对落在单元链外的桩号返回 `None`，**不外推**。遇 `None` 必须报错，不得填默认值

---

## ★ MVP 已完成（2026-09-20）

**Phase 1 + Phase 2 + Phase 3 = T001–T018 全部完成并验证。**

| 项 | 结果 |
|---|---|
| `geometry_point` 行数 | **0 → 332**（≡ `station_sequence`，严格 1:1） |
| 验收① 可重建 | 重算 vs 已存，逐列差全部落在该列 `numeric` 的末位内 |
| 验收② 同批次 | 与 `alignment_element` 同一 `write_txn`、同 `batch_no` |
| 契约测试 | 14/14 全绿（`design-导入` 637 条断言） |
| 新增测试 | `tests/contract/test_geometry_solver.py` 8 组 |

**两点值得记住的实测结论：**

1. **竖曲线处设计高程 ≠ 变坡点高程。** 实测差 `0.1667 m`，恰好等于外距
   `E = |ω|·L/8` —— 竖曲线本来就要把切线交点「切掉」。所以 VPI 高程
   **不能**拿来当设计高程的期望值。

2. **坐标差的量级由「链式累积」决定，不是一个统一的半格。**
   `x/y/azimuth` 沿 33 个单元积分，每个单元的起点坐标各被舍入一次，
   误差累积到 3.3～6.8 个半格；而 `curvature_1pm` 是**单元内**量、
   `design_elev_m` 是**局部**量，都不累积，就是 1 个半格。
   容差按列写（`COL_TOL`），不写一个魔数。

---

## Phase 1: Setup（共享前置）

**Purpose**: 确认既有组件可用，避免重复实现

- [x] T001 通读 `scaffold/modules/M2-ingest/adapters/geom.py`，确认四个函数签名与 `research.md` 记载一致；把三条不得违反的约定写进新模块的 docstring
- [x] T002 [P] 复核既有测试 `scaffold/tests/contract/test_design_import.py`（第 599–631 行 geom 部分）覆盖了哪些行为，列出**未被覆盖**的行为作为 T012/T015 的补充依据
- [x] T003 [P] 在 `scaffold/modules/M2-ingest/design_import.py` 定位落库链路（`load()` 第 756 行起、`station_id` 中转逻辑第 817–930 行），确认 `geometry_point` 应插入的位置与 `on_conflict` 键

**Checkpoint**: 确认无需新写数学，接线点已定位

---

## Phase 2: Foundational（阻塞所有用户故事）

**Purpose**: 求值组装器 —— 所有故事共用的核心

**⚠️ CRITICAL**: 本阶段完成前，任何用户故事都不能开始

- [x] T004 在 `scaffold/modules/M2-ingest/geometry_solver.py` 新建模块：输入 `alignment_element` 列表 + 桩号，输出 `geometry_point` 行。**不得重新实现几何数学**，一律调用 `adapters/geom.py` 的 `locate`/`curvature_at`/`azimuth_at`/`point_at`
- [x] T005 在 `scaffold/modules/M2-ingest/geometry_solver.py` 实现 `solve_all(elements, stations)`：对每个桩号调 `locate`，返回 `<el>` 时算出 κ/方位角/坐标；**返回 `None` 时必须抛错并指名桩号，不得跳过或填默认值**（对应约定 3）
- [x] T006 [P] 在 `scaffold/modules/M2-ingest/geometry_solver.py` 实现高程求解：`design_elev_m` 来自 `.ZDM` 竖曲线，`ground_elev_m` 来自 `.DMX`（既有 `profile_ground_point` 表），按桩号对齐
- [x] T007 [P] 在 `scaffold/modules/M2-ingest/geometry_solver.py` 实现六列横坡插值：源为既存表 `superelev_transition`（本工程 76 个变化点），按 `station_km` 插值到逐桩
- [x] T008 在 `scaffold/modules/M2-ingest/geometry_solver.py` 处理 `superelev_transition` 的 **NULL 语义**：DDL 注释规定「NULL 表示源文件写了 9999『可以忽略此数据』，即该列在此位置不参与约束、**过渡照常继续**（不是缺值、也不是沿用上值）」。插值时 MUST 跳过该点继续寻找下一个有效点
- [x] T009 在 `scaffold/modules/M2-ingest/geometry_solver.py` 处理桩号**精度匹配**：`superelev_transition.station_km` 与 `station_sequence.station_local_km` 均为 `numeric(12,6)`，但只有 34/76 精确命中。插值 MUST NOT 依赖精确相等，须用区间查找

**Checkpoint**: `solve_all` 可在内存中由 33 要素 + 332 桩号产出 332 行，且经 T013 验证与源文件自带的 `end_x`/`end_y` 一致

---

## Phase 3: User Story 4 - 逐桩线形作为可重建的派生缓存 (Priority: P2)

> **说明**：本特性先做 **US4**（乙），再做 US1–US3（甲/校验）。
> 理由见 plan.md 第五节：甲要改 `station_sequence` 外键方向，属**契约②变更**，须走工单由导师把关；
> **乙的正确性不依赖甲** —— 求值器只读 `station_sequence`，不关心它挂在哪个路段下。

**Goal**: 填满 `geometry_point`（当前 0 行），332 行逐桩线形，与 `station_sequence` 一一对应

**Independent Test**: 重算全表并逐行比对；再故意篡改一行，重算必须报出不一致

### Tests for User Story 4 ⚠️（先写，确认失败）

- [x] T010 [P] [US4] 契约测试：`scaffold/tests/contract/test_geometry_solver.py` 新建，断言 `geometry_point` 行数 == `station_sequence` 行数 == 332
- [x] T011 [P] [US4] **元测试（可证伪）**：篡改 `geometry_point` 任一行的 `curvature_1pm`，重算校验**必须报不一致**。若此测试恒通过，说明校验形同虚设 —— 对应宪法原则 V
- [x] T012 [P] [US4] 单元测试：用 `tests/fixtures/design_import/weidi_pm_excerpt_4units.pm` 断言 κ(s) 在直线段 == 0、圆曲线段 == 1/R、缓和段 == s/A²；并断言 `curvature_at` 对 `turn_flag=-1` 返回负值
- [x] T013 [P] [US4] **交叉验证测试**：用真实 `.PM` 断言 `point_at(el, el.end_station)` 与源文件自带 `end_x`/`end_y` 偏差 < 1e-6 m（research.md 实测 3.171e-08 m）；并断言单元长度合计 == 5805.421 m

### Implementation for User Story 4

- [x] T014 [US4] 在 `scaffold/modules/M2-ingest/design_import.py` 的 `load()` 中接入 `geometry_point` 落库：**与 A12 `alignment_element` 同一事务**（DDL 第 556 行要求「必须与 A12 同批次生成，不允许只改 A12 不改本表」）
- [x] T015 [US4] `geometry_point.station_id` 带 UNIQUE 约束（实测 `geometry_point_station_id_key`），`on_conflict=("station_id",)` —— 与 DDL 一致
- [x] T016 [US4] 接线 `station_id` 中转：复用既有 `station_id_by_m`（第 817–930 行）。桩号对不上时 MUST 报错并指名桩号，**不得静默跳过**
- [x] T017 [US4] 批次登记：确认 `data_import_batch`（第 934–935 行，已在同事务内）覆盖本次生成，「同批次」由现有机制表达，**不新增列**
- [x] T018 [US4] 补 `design_file` 血缘（FR-017 的 ⚠️ 部分）：使 `geometry_point` 的每一行可追溯到源文件

**Checkpoint**: `geometry_point` 由 0 行 → 332 行；`SC-004` 与 `SC-009` 通过

---

## Phase 4: User Story 1 - 桩号脱离路段独立存在 (Priority: P1)

**⚠️ 本阶段被工单阻塞** —— FR-001 改动 `station_sequence.section_id` 为可空 + 新增路线锚定，
**触碰现有表，属契约②变更**。按宪法原则 IV 与仓库冻结设计纪律（「学生不参与设计决策」），
**须先由导师裁决工单，再执行本阶段**。

**Goal**: 桩号不再挂在易变的路段下；路段增删不影响已导入桩号

**Independent Test**: 删除一个路段，桩号应完好无损（当前会级联失败）

- [ ] T019 [US1] **（工单前置：导师批准后方可执行）** 起草并提交契约变更工单：`station_sequence.section_id` 改可空、新增路线锚定列、影响面与回滚路径
- [ ] T020 [US1] 新增 migration：`station_sequence.section_id` 改可空 + 路线锚定（**迁移不可变**，新增文件，不改历史迁移）
- [ ] T021 [US1] 修改 FK 方向于 `scaffold/sql/10_ddl_v0.5.sql`（或新 migration）：路段删除 MUST NOT 级联删除桩号（FR-002）
- [ ] T022 [US1] 在 `scaffold/modules/M3-rpdao/rpdao/repo.py` 实现按路线查询桩号集合（FR-003）
- [ ] T023 [P] [US1] 在 `scaffold/tests/contract/test_station_baseline.py` 测试：断言删除路段后桩号仍在（FR-002）；断言两条相距数千公里的路线互不混淆（FR-003）

---

## Phase 5: User Story 2 - 文件覆盖区间被记录，越界即报错 (Priority: P1)

**Goal**: `design_file.coverage_from_station_km` / `coverage_to_station_km` 由全 NULL 变为实测值；越界即失败且原子

**Independent Test**: 构造一个声明区间与实际不符的文件，导入必须失败且**零行入库**

- [ ] T024 [P] [US2] 在 `scaffold/modules/M2-ingest/design_import.py` 记录每个文件的实际桩号覆盖区间，写入 `design_file.coverage_from_station_km` / `coverage_to_station_km`（FR-005）
- [ ] T025 [US2] 在 `scaffold/modules/M2-ingest/design_import.py` 实现声明区间 vs 实际区间校验，不符时报出：文件、声明区间、实际区间、差值（米）（FR-006）
- [ ] T026 [US2] 在 `scaffold/modules/M2-ingest/design_import.py` 保证原子性：失败时该文件无任何部分数据入库（FR-007）
- [ ] T027 [US2] 在 `scaffold/modules/M2-ingest/design_import.py` 断言无补值/外推/默认填充（FR-008）
- [ ] T028 [P] [US2] **元测试**：构造越界文件，断言导入失败**且** `count(*)` 未变 —— 对应 FR-007 的可证伪检查

---

## Phase 6: User Story 3 - 桩号文本与数值可互相还原 (Priority: P1)

**Goal**: 桩号文本 ↔ 数值双向还原，不一致即失败并指出行号

**Independent Test**: `tests/fixtures/design_import/weidi_sta_precision_edge.STA` 全部往返一致

- [ ] T029 [P] [US3] 在 `scaffold/modules/M2-ingest/adapters/weidi/sta.py` 实现文本 ↔ 数值双向解析（FR-009）；不一致时报错并**指出具体行**
- [ ] T030 [US3] 在 `scaffold/modules/M2-ingest/adapters/weidi/sta.py` 校验覆盖**全部**记录，不得只抽样（FR-010）
- [ ] T031 [P] [US3] 在 `scaffold/modules/M2-ingest/adapters/weidi/sta.py` 保留桩号类型（整桩/加桩/端点），**加桩 MUST NOT 被规整**（FR-011）。实测：332 = 整桩 290 + 加桩 40 + 端点 2，且 40 个加桩全部紧跟一个整桩
- [ ] T032 [P] [US3] 在 `scaffold/tests/contract/test_station_text.py` 写**元测试**：构造坏数据（文本与数值不符），断言被拒绝 —— 对应 FR-010 明文要求

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T033 在 `scaffold/modules/M3-rpdao/rpdao/pool.py` 实现按桩号区间查询（FR-018）：出 DAO 方法，利用既有索引 `idx_alignment_elem_station`
- [ ] T034 在 `scaffold/modules/M6-api/app.py` 新增 HTTP 端点供 TruckSim / FEM 消费（FR-018）；**必须同时登记 `scaffold/contracts/openapi/m6-gateway.*.yaml`**，否则 `m6-路由` 测试报「实现缺契约」
- [ ] T035 [P] 更新 `scaffold/contracts/design-import/README.md`：`geometry_point` 从「`source_absent` 缺口」改为已实现，并说明它是派生缓存而非独立源
- [ ] T036 [P] 复核 `design_file` 中 `横断面绘图.3DR` 的 `absent` 登记仍然正确（`.3DR` 确实未生成，本次工作**不改变**这一事实 —— `geometry_point` 是派生的，不需要 `.3DR`）
- [ ] T037 运行 `scaffold/run_contract_tests.sh`，确认 14/14 通过（含本特性新增的测试）

---

## Dependencies & Execution Order

```text
Phase 1 Setup（T001–T003）
        ↓
Phase 2 Foundational（T004–T009）★ 阻塞所有故事
        ↓
Phase 3 US4（T010–T018）← 本特性先做这个，无外部阻塞
        ↓
Phase 4 US1（T019–T023）← 被工单阻塞，导师裁决后执行
Phase 5 US2（T024–T028）← 独立，可并行
Phase 6 US3（T029–T032）← 独立，可并行
        ↓
Phase 7 Polish（T033–T037）
```

**关键依赖**：

- Phase 2 **阻塞**所有故事 —— `solve_all` 是共用核心
- **US4 不依赖 US1** —— 这是 plan.md 的核心判断。求值器只读 `station_sequence`，不关心它挂在哪
- US2 / US3 与 US4 **完全独立** —— 可并行，涉及不同文件

## Parallel Execution Examples

**Phase 2 内可并行**（不同关注点，同文件但不同函数）：
```text
T006 高程求解  ‖  T007 横坡插值
```

**US4 的测试可并行**（三个独立测试文件/用例）：
```text
T010 行数契约  ‖  T011 元测试  ‖  T012 κ(s) 单元  ‖  T013 交叉验证
```

**跨故事并行**（Phase 3 完成后）：
```text
US2（design_import 覆盖区间）  ‖  US3（sta.py 文本还原）
```

## Implementation Strategy

### MVP = Phase 1 + Phase 2 + Phase 3（T001–T018）

交付价值：**`geometry_point` 由 0 行 → 332 行**，且满足两条硬性验收：
- **可重建**：重算 ≡ 已存（T011 元测试保证可证伪）
- **同批次**：与 `alignment_element` 同事务（T014）

**这个 MVP 无外部依赖** —— 不需要 `.3DR`、不需要导师批准、不需要改 schema。

### 增量交付
1. Phase 1–2 → 求值器可用（内存验证）
2. + Phase 3 → **MVP**：空表填满，SC-004/SC-009 通过
3. + Phase 5/6 → 校验能力（US2/US3，独立可并行）
4. + Phase 4 → 桩号解耦（**须工单前置**）
5. + Phase 7 → 对外查询端点

---

## Notes

- **[P] 任务** = 不同文件或无依赖，可并行
- **[Story] 标签** 映射到 spec.md 的用户故事，便于追溯
- **每个用户故事可独立完成与测试**
- **实现前先确认测试失败**（本特性的元测试尤其重要）
- **按逻辑组提交**
- 避免：模糊任务、同文件冲突、破坏独立性的跨故事依赖

### 本特性的特别提示

> 本仓库已三次因「先动手、后查仓库」而重复劳动（见 research.md 末节）。
> **执行任何任务前，先读 `contracts/`、`adapters/` 与既有测试。**
> 若发现某个任务描述的工作**已经存在**，跳过它并在提交信息中说明 —— 那是正确行为，不是偷懒。