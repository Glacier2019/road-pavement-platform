# Tasks: 空表缺口盘点与接入优先级

**Input**: Design documents from `/specs/001-empty-table-gap/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: 本特性**要求**测试（宪法原则 I「两侧都要测」＋ 原则 V「每条判定配元测试」）。

**Organization**: 按用户故事分组，每个故事可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无依赖）
- **[Story]**: 所属用户故事

## 关键路径警示

⚠ **T004 是整件事的地基**：段↔表映射若错，后面所有归因都是错的，**而且不会报错**。
⚠ **T012 是本特性最重要的一条测试**（SC-002 硬断言）：它必须能在实现写错时变红。

---

## Phase 1: Setup

**Purpose**: 确认起点状态，建立判据基线

- [ ] T001 实测记录基线：记下当前缺口数，作为「接完一条链」的对照
  `curl -s http://localhost:8024/gw/v1/catalog/tables`
  产出：62 / 36 有数据 / 26 空 的实测快照
- [ ] T002 [P] 确认 5 个土方段在实库确为 0 行（SC-002 的前提）
  若**不是** 0，说明数据已被导入过 ⇒ 停止，回来改 spec（前提变了，结论也要变）
- [ ] T003 [P] 确认 5 个段在 `weidi/__init__.py` 的 `IMPLEMENTED` 里

---

## Phase 2: Foundational（阻塞所有用户故事）

**⚠️ 关键**：这一段没做完，任何用户故事都不能开始

- [ ] T004 在 M2 建立**段↔表**映射的唯一入口
  文件：`scaffold/modules/M2-ingest/adapters/weidi/__init__.py`
  默认「段名==表名」＋ 显式例外 `design_control`（1 段 9 表）
  **必须**配套：遍历所有段，凡「段名 ≠ 任何物理表名」者必须在例外表里登记
  ⇒ 漏登记的段会被判成「没有对应表」而**静默消失**（宪法原则 V）
- [ ] T005 [P] M2 新增 `GET /v1/design/capabilities` (FR-005)
  文件：`scaffold/modules/M2-ingest/app.py`
  契约：`specs/001-empty-table-gap/contracts/v1-design-capabilities.md`
  要点：`supported` 与 `implemented` **都报**（INV-8）；`source_present` **现算**（不缓存）；
  返回**文件名**不返回绝对路径；`duplicates` 如实报出（INV-9）
- [ ] T006 [P] M3 `Dao.table_census()` 带出 `owner` 字段 (FR-003)
  文件：`scaffold/modules/M3-rpdao/rpdao/pool.py`
  真源：`catalog.TABLE_OWNER`（**不另抄一份**，FR-003）
- [ ] T007 M2 的段能力必须在 M6 可见（经 HTTP，**禁止 import**）(FR-007)
  文件：`scaffold/modules/M6-api/app.py`
  ⚠ 硬线 II 的落点：M6 import M2 会造成代码依赖，破坏「模块可拔插」

**Checkpoint**: 两边的事实都能取到，可以开始拼装

---

## Phase 3: User Story 1 - 一眼看出每张空表的「空因」(P1) 🎯 MVP

**Goal**: 26 张空表 100% 有明确分类（SC-001），且分类**正确**（SC-002）

**Independent Test**: `quickstart.md` 验收 2 与验收 3 全过

### Tests for User Story 1

- [ ] T008 [P] [US1] 新建契约测试 `scaffold/tests/contract/test_gap_attribution.py` (SC-001)
  覆盖：6 类空因各自的应通过/应拒绝两侧
- [ ] T009 [P] [US1] **元测试**：INV-1 短路顺序（先 `source_present` 再 `implemented`）(FR-005)
  构造「源里没有、但适配器已实现」的段（`.3DR` / `geometry_point`）
  ⇒ 断言判为 `source_absent`，**不是** `module_not_built`
  这是仓库里记着的**真实踩坑**：顺序反了会盖掉真正的原因
- [ ] T010 [P] [US1] **元测试**：INV-2 `has_data` 优先
  构造「有行数 + 源缺失」的表 ⇒ 必须报 `has_data`，不得因源缺失改口
- [ ] T011 [P] [US1] **元测试**：INV-3 `upstream_pending` 只给无对应段的表
  断言 `alarm_record` / `diagnosis_result` 报 `upstream_pending`，**不是** `source_absent`
- [ ] T012 [US1] ★★ **SC-002 硬断言**（本特性最重要的一条测试）
  五个土方段必须全部判为 `source_ready_not_imported`，**零张**判为「适配器未实现」
  **必须先写、先看它红**：故意把短路顺序写反，它必须报错
- [ ] T013 [P] [US1] **元测试**：T004 的例外表完整性
  删掉例外表里 `design_control` 一项 ⇒ 必须报「有段没有对应表」

### Implementation for User Story 1

- [ ] T014 [US1] 实现空因判定逻辑 (FR-001/002/005/010)
  文件：`scaffold/modules/M6-api/app.py`
  依据：`data-model.md` 实体 1 的判定表 + INV-1/2/3/4
  ⚠ **FR-010 落在这一条**：判定不出来时**必须**报 `unknown` 并在 `note` 里说明
  为什么判不出来，**不得**猜一个分类填上 —— 猜出来的分类无法被任何人发现是错的
- [ ] T015 [US1] 新增 `GET /v1/catalog/gaps` (FR-007)
  契约：`specs/001-empty-table-gap/contracts/v1-catalog-gaps.md`
- [ ] T016 [US1] 登记进 OpenAPI 契约（否则 `m6-路由` 报「实现缺契约」）
  文件：`scaffold/contracts/openapi/m6-gateway.v0.3.yaml`
- [ ] T017 [US1] 503 分支：M2 / M3 分别点名（两者故障含义不同）
- [ ] T018 [US1] 跑 `./run_contract_tests.sh`，确认新套件绿且旧 12 套件未回归 (FR-008/SC-005)

**Checkpoint**: P1 完成 —— 缺口已可归因，可独立演示

---

## Phase 4: User Story 2 - 按「离可演示链路多远」排序 (P2)

**Goal**: 给出**有判据、可复算**的优先级（SC-003）

**Independent Test**: 复核者仅凭 `priority_keys` 能独立复算出同一次序

- [ ] T019 [P] [US2] 排序稳定性测试：同输入跑两次，次序必须**完全一致**
- [ ] T020 [P] [US2] 三键优先级测试：改动模块数 → 源是否具备 → 域序
- [ ] T021 [US2] **元测试**：把某个键的比较方向反过来 ⇒ 测试必须红
- [ ] T022 [US2] 实现三键稳定排序（research.md R3）(FR-006)
  同三维相同以 `table` 字典序决胜 ⇒ 完全确定
  ⚠ **不许**加「少数手工调序」—— 那会让 SC-003 无法成立
- [ ] T023 [US2] 在响应里暴露 `priority_keys`（复核者要靠它重算）(FR-006/SC-003)
- [ ] T024 [US2] `action` 文案 ＋ **指出缺哪一次导入** (FR-004/006)
  对 `source_ready_not_imported` 的表，**必须**给出具体的段名与后缀
  （如「跑一次设计导入：段 `earthwork_transfer`，文件后缀 `.tsftxt`」），
  **不得**只说"源已具备" —— 用户真正要的是"跑哪一次导入"，
  只说前者等于让他自己去翻目录，缺口没闭合（这是分析报告 HIGH-1）

**Checkpoint**: P2 完成 —— 排序可复核

---

## Phase 5: User Story 3 - 接完一条链后统计自动更新 (P3)

**Goal**: 「接完」由数据自己说话，无需改代码（SC-004）

**Independent Test**: `quickstart.md` 验收 6

- [ ] T025 [P] [US3] 测试：`source_present` 不得被缓存（新增源文件后须立刻可见）(FR-002)
- [ ] T026 [P] [US3] 测试：删除数据后重新盘点，如实变回空表（FR-009）
  **不得**因「文档说接完了」而继续报有数据
- [ ] T027 [US3] M9 `tables.html` 改版：空表区显示 空因 / 归属模块 / 建议动作
- [ ] T028 [US3] 页面提示必须说「源已具备」，**不得**说「可以导入」
  （后者是会落空的承诺 —— 见契约的措辞约束）
- [ ] T029 [US3] `unknown` 在页面上一眼可见，**不得**默认折叠或并入有数据（INV-4）
- [ ] T030 [US3] 新增静态资源必须同步 `scaffold/modules/M9-console/Dockerfile` 的 COPY
  ⚠ 已踩过：漏 COPY ⇒ 容器里 500 而宿主机测试全绿

**Checkpoint**: P3 完成 —— 缺口对使用者可用

---

## Phase 6: Polish & Cross-Cutting

- [ ] T031 [P] 更新 `scaffold/tests/contract/test_console.py`：空因展示组 + Dockerfile COPY 钉子
  另补两个断言（分析报告 MEDIUM-3）：
  · **分区父表空仍报空** —— `wim_axle_record` 清空后必须报父表空，
    不得因 39 个分区存在而报有数据（回归钉子，防未来改坏）
  · **登记与实测不符要可见** —— 模块登记说"骨架"而该表其实已通时，
    以实测为准且不符处可见
- [ ] T032 [P] 更新 `scaffold/modules/M9-console/README.md`（仍写着「前端界面 TODO / 骨架」）
- [ ] T033 [P] 更新 `scaffold/sql/数据字典-v0.5.md` 中与缺口相关的表述（若有）
- [ ] T034 跑完整 `quickstart.md` 七组验收，逐条留证据
  ＋ **SC-006 计时断言**：`curl -w "%{time_total}"` 对 `/gw/v1/catalog/gaps` 必须 < 3s
  （分析报告 HIGH-2）。这条不是形式 —— 本特性让 `/tables` 多取了一路 M2，
  若目录扫描写成"逐段一次 stat"，页面就会变慢，而功能测试全绿
- [ ] T035 重建 M2/M6/M9 容器并实测（`DOCKER_BUILDKIT=0`，本沙箱 BuildKit 不可用）
- [ ] T036 提交：提交信息写明「为什么」与「靠什么防住」

### 分析报告追加（`analysis.md` Remediation，本轮已并入正文）

- [ ] T037 [US1] **元测试**：FR-010 服务端不得猜
  构造一张「既无对应段、又非下游产出」的表（如临时把某表从 TABLE_OWNER
  与段映射里同时摘掉）⇒ 必须报 `unknown` ＋ 原因，**不得**归入任何其它分类

---

## Dependencies & Execution Order

| 阶段 | 依赖 | 说明 |
|---|---|---|
| Phase 1 Setup | 无 | 立即可开始 |
| Phase 2 Foundational | Phase 1 | **阻塞所有用户故事** |
| Phase 3 US1 (P1) | Phase 2 | MVP |
| Phase 4 US2 (P2) | Phase 2；弱依赖 US1 的条目结构 | |
| Phase 5 US3 (P3) | Phase 2；展示依赖 US1 的 `empty_reason` 与 US2 的排序 | |
| Phase 6 Polish | 全部 | |

### 可并行

- T002 / T003 可并行
- T005 / T006 可并行（M2 与 M3 互不相干 —— 这正是硬线的收益）
- T008–T013 六条测试可并行写
- T031 / T032 / T033 三条文档可并行

### 用户故事之间的依赖

- **US1 可独立交付**：即使不做 US2/US3，也能回答「这张表为什么空」
- **US2 依赖 US1 的产物结构**，但排序逻辑本身可独立测
- **US3 是展示层**，依赖 US1+US2 的字段；但它验的「不落库、自动更新」
  是 US1 实现方式的属性 ⇒ 若 US1 用了缓存，US3 会红 ——
  这是**有意的耦合**，专门用来抓住缓存这个错误

---

## Implementation Strategy

### MVP First

1. Phase 1 + Phase 2（地基）
2. Phase 3 US1 ⇒ **停下来验 SC-002**
3. 若 SC-002 过 ⇒ 缺口归因已可用，可交付

### 增量交付

US1（归因）→ US2（排序）→ US3（展示）逐段交付，每段都保持契约测试全绿。

### 风险与对策

| 风险 | 对策 |
|---|---|
| 段↔表映射漏登记 ⇒ 表静默消失 | T004 的配套检查 + T013 元测试 |
| 短路顺序写反 ⇒ 归因错但不报错 | T009 元测试 + T012 硬断言 |
| 把 `null` 的 `source_present` 压成 `false` | INV-5 测试（T011 覆盖同类） |
| 缓存源文件存在性 ⇒ 接完不更新 | T025 测试 |
| 漏 COPY ⇒ 线上 500 而本地全绿 | T030 + T031 |
