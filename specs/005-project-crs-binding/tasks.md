---

description: "Task list for 005-project-crs-binding"
---

# Tasks: 坐标基准跟着项目走

**Input**: Design documents from `/specs/005-project-crs-binding/`

**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓, quickstart.md ✓

**Tests**: ★ **本次必须有。** 宪法 §I「契约先行」是 NON-NEGOTIABLE，且要求「契约测试必须覆盖**应通过／应拒绝两侧**」；宪法 §V 要求「每条新增的检查，必须配一个元测试」。

**Organization**: 按 user story 分组，每段可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件、无未完依赖）
- **[Story]**: US1 / US2 / US3

## ★ 三条必须先读的现场事实（本仓库实测，不是推断）

1. **`GeRepository.SECTIONS_SQL` / `ONE_SECTION_SQL` 已经有 `LEFT JOIN design_project p ON p.id = s.design_project_id`。** ⇒ 加列**不需要新 join、不需要新 DAO 方法**（research.md R3）。
2. **`geo.py` 必须保持零数据库、纯函数。** 宪法 §II 的措辞是「**关键在结构上做不到**」，而现行 AST 断言**只扫 `app.py` 一个文件**（research.md R4）。⇒ 基准由 `app.py` 经 `dao.ge` 取到，**传进去**。
3. **迁移文件命名是 `NN_migrate_v<版本>_<短名>.sql`，当前最高 `97`。** ⇒ 本次是 **`98_migrate_v06_crs_binding.sql`**。且 `test_ddl_migration_parity.py`（套件 `m1-装库一致`）会断言「全新装 DDL 与 DDL+migration 升级得到同一套 schema」。

---

## Phase 1: Setup

**Purpose**: 把两处会串味的既有约定先固定下来，避免后面对不上。

- [x] T001 复核实测基线：跑 `cd scaffold && export DOCKER_CONFIG=/data/cy/shujuku/.dockerhome && ./run_contract_tests.sh`，记录 **通过 / 失败 / 跳过** 三个数（期望 21｜0｜0），把它写进 `specs/005-project-crs-binding/tasks.md` 末尾的备注，作为「改之前」的快照
- [x] T002 [P] 读 `scaffold/run_contract_tests.sh` 第 41–74 行的**套件注册表**（格式 `"名字|说明|模块|桶|路径"`），确认五个桶（`PY` / `PYDAO` / `DAO` / `API` / `CONSOLE` / `NODE`）各自的依赖集，写进备注供 T0xx 用
- [x] T003 [P] 实测确认 M6 的 `geo.py` 当前**零数据库**：`grep -c 'psycopg\|connect\|PG_DSN\|cursor\|execute' scaffold/modules/M6-api/geo.py`（期望只有 1 处、且是 `geo.py:176` 的 docstring 提到 `numeric`→`Decimal`），把这条命令和结果写进备注作为「改之前」的证据

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: 加列 + 读列 + 派生。**任何 user story 都做不了，直到这一段完成。**

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

### 表结构（契约②）

- [x] T004 在 `scaffold/sql/10_ddl_v0.6.sql` 的 `design_project` 表定义里加六个列，**逐字按 data-model.md E1**（★ 2026-10-12 按宪法原则 VI 定权：`crs_epsg` 是**唯一权威字段**，`crs_zone_number` / `crs_central_meridian_deg` / `crs_zone_width_deg` 是**可空注释**）：`crs_zone_number smallint`、`crs_central_meridian_deg numeric(9,6)`、`crs_epsg integer`、`crs_zone_width_deg numeric(4,2)`、`crs_assumed boolean`、`crs_evidence text`。**六列全部可空**（现有两行路段、一行项目都不带基准，加列后必须依然合法）
- [x] T005 在 `scaffold/sql/10_ddl_v0.6.sql` 的 `road_section` 表定义里加**同样的六个列**（同名同类型，逐字同上）
- [x] T006 新建 `scaffold/sql/98_migrate_v06_crs_binding.sql`，对已有库做 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`，**六个列 × 两张表**。★ 必须与 T004/T005 得到等价 schema —— 套件 `m1-装库一致`（`tests/contract/test_ddl_migration_parity.py`）会验这一点
- [x] T007 在 `scaffold/sql/数据字典-v0.6.md` 里登记这十二个列（两张表各六个）。★ 套件 `m1-表数`（`tests/contract/test_ddl_dict_catalog.py`）会比对 DDL ↔ 数据字典 ↔ catalog 三处

### 读列（契约③，M3-rpdao）

- [x] T008 [P] 在 `scaffold/modules/M3-rpdao/rpdao/repo.py` 的 `SECTIONS_SQL`（约 L203）里，加 `s.crs_*` 与 `p.crs_*` 两组列，**别名区分级别**（如 `s.crs_epsg AS section_crs_epsg`、`p.crs_epsg AS project_crs_epsg`）。★ **不许动 `FROM` / `JOIN` 那一节** —— `LEFT JOIN design_project p` 已经在了
- [x] T009 [P] 在 `scaffold/modules/M3-rpdao/rpdao/repo.py` 的 `ONE_SECTION_SQL`（约 L221）里做同样的加列
- [x] T010 在 `repo.py` 的 `GeRepository` 里加纯函数 `effective_crs(row) -> dict`，**逐字实现 data-model.md E2 的五态表**：①两级都有且相等 → 取该值、`source=project`；②两级都有且不等 → **抛错，错误信息 MUST 说出两级的取值各是什么**；③项目有、路段没填 → 取项目、`source=project`；④项目没有（含路段无 `design_project_id`）、路段有 → 取路段、`source=section`；⑤两级都没有 → **返回一个有类型的「没有」**。★ 这个函数**纯函数、不碰库**，与 `derive_level`（L394）同形，可离线测
- [x] T011 ★★ 在 `repo.py` 的 `GeRepository` 里加不变量校验函数，**逐字实现 data-model.md E3 的 I1 —— 2026-10-12 按宪法原则 VI 改写后的版本**：`crs_epsg` 必须在 `spatial_ref_sys.srid` 里查得到（**查表**）；三个注释字段（带号 / 中央子午线 / 带宽）**有则比行为，无则不报错**。
  ★★★ **两条已被实测否证的写法 MUST NOT 出现**：① `crs_central_meridian_deg == crs_zone_number * 3` —— 那个 `3` 是**硬编码常量**，而 `tests/contract/test_sim_track.py` 有一条既有守卫逐字写着「带号是配置项，不是代码里的常量」（research.md R10）；② 从 `spatial_ref_sys.proj4text` 抓 `+lon_0` —— 实测全表 8500 个 SRID 只有 **5818** 个有它，**UTM 全部没有**（research.md R11 补记）。
  ★★ **自洽判据是行为不是记法**：把同一批点分别走「SRID 路径」与「手写路径」，结果 MUST 落在 **1e-4 m** 内。★ 容差**不许取 1e-9** —— 那是拿浮点噪声当判据。

### 派生的可测性

- [x] T012 [P] 在 `scaffold/tests/contract/test_region_frame.py` 里加一段**离线**测试：直接喂 `effective_crs()` 五态输入，断言五个输出（含两处抛错）。★ 这一段**不连库、不连容器** —— 保住该文件开头那句「运行（不需要数据库、不需要容器、不需要网络）」

**Checkpoint**: 列在库里、DAO 读得出、五态派生可离线验证。user story 可以开始了。

---

## Phase 3: User Story 1 - 两个项目、两套基准 (Priority: P1) 🎯 MVP

**Goal**: 同一个桩号，在两个中央子午线不同的项目里，算出两个**不同且都正确**的经纬度。

**Independent Test**: 造第二个项目（36 带 / CM 108 / EPSG 4545），对两个项目各问一次同一桩号，断言行 `zone_number` 一个是 39 一个是 36、`lon_deg` 相差约 9°。

### Tests for User Story 1 ⚠️ 先写，先看它红

- [x] T013 [P] [US1] 在 `scaffold/tests/contract/test_region_frame.py` 加「**两个不同基准的项目共存**」判据（SC-001/SC-002/SC-008）：造第二个项目 + 其路段，断言两条路径各自算出正确的 `zone_number` / `central_meridian_deg` / `epsg`，且 `crs_source` 都是 `"project"`
- [x] T014 [P] [US1] 在 `scaffold/tests/contract/test_region_frame.py` 加「**两级不一致必须报错**」判据（SC-009）：写一个不一致的配置，断言**抛错**，且错误信息里**同时出现两级各自的取值**。★ 宪法 §I 要求「应通过／应拒绝两侧」——这一条就是「应拒绝」侧

### Implementation for User Story 1

- [x] T015 [US1] 改 `scaffold/modules/M6-api/geo.py` 的 `load_crs`（L298）：由「收一个路径、自己去读文件」改为「**收一个已经解析好的 dict**」。★ 校验逻辑（`assumed` ↔ `evidence` 的不变量、必填键、`geographic_frame` 必须存在）**逐字保留** —— 那是 004 立下的、被 `test_region_frame.py` §8 守着的性质
- [x] T016 [US1] 改 `scaffold/modules/M6-api/app.py` 的 `_geo_crs()`（L466-474）：**去掉空参调用**，改为按主体取 —— 经 `dao.ge.section(section_id)`（该行的 `SECTIONS_SQL`/`ONE_SECTION_SQL` 已带两组 `crs_*` 列），调 `effective_crs()`，再把结果传给 `geo.load_crs()`。★ **`geo.py` 里不许出现任何数据库调用**
- [x] T017 [US1] 让 `app.py` 的**五条** `/v1/geometry/*` 路径都把 `crs_source` 与 `crs_assumed` 带进响应：`/sections`（L329）、`/sections/{id}/geo`（L345 一带）、`/geo/reverse`、`/geo/route`、`/monitor-sections`。★ 字段名**逐字按** `specs/005-project-crs-binding/contracts/m6-gateway.geo.v0.2.yaml` 的 `schema:` 块
- [x] T018 [US1] 在 `scaffold/modules/M6-api/app.py` 里让两级都没配时按 FR-003 返回**有类型的「没有」**：响应里 MUST NOT 出现任何经纬度字段，且 MUST 有一句说清为什么。★★ **MUST NOT 回落到 39 带、MUST NOT 回落到任何全局默认**。真实用例 = 路段 1（`design_project_id` 为 NULL）
- [x] T019 [US1] 把 `crs.yaml` 的值装载进库：把 `scaffold/modules/M9-console/config/crs.yaml` 现有的值（`zone 39 / CM 117 / EPSG 4548 / assumed false` + 那 8 条 `evidence`）写进 `design_project` 第 2 行（`project_name='毕设'`）的六个 `crs_*` 列

**Checkpoint**: US1 独立可测。**这就是 MVP。**

---

## Phase 4: User Story 2 - 现有那条路一位都不变 (Priority: P2)

**Goal**: 通用化不许让 004 的精度变差。

**Independent Test**: 跑 `test_region_frame.py`，投影往返仍是 **0.050835 mm / 最大 0.146233 mm**，桩号基线与 bbox 逐位相同。

### Tests for User Story 2 ⚠️ 先写，先看它红

- [x] T020 [P] [US2] 在 `scaffold/tests/contract/test_region_frame.py` 加「**004 精度不退化**」判据（SC-007）：判据是**浮点逐位相同**，不是「误差在容差内」。★ FR-007 的原文就是「判据 = 浮点逐位相同」
- [x] T021 [P] [US2] 在 `scaffold/tests/contract/test_region_frame.py` 加「**同一份基准、两级都填、值相等**」判据（data-model E2 第 1 态）：断言通过，且 `crs_source` 仍是 `"project"`。★★ 这一条证明「路段级填了相同的值，也不改变权威来源是项目级」——这正是「路段级是冗余校验」的直接推论

### Implementation for User Story 2

- [x] T022 [US2] 在实库给路段 6（`section_id=6`，`design_project_id=2`）填上**与项目相同**的基准（39 / 117 / 4548），作为「两级都填且一致」的真实样本；SQL 写进 `scaffold/sql/98_migrate_v06_crs_binding.sql` 之后另存一份到 `specs/005-project-crs-binding/quickstart.md` 第 2 步旁
- [x] T023 [US2] 跑 quickstart.md **第 7 步**并逐条核对：投影往返两位小数不得变、桩号差不得变、`bbox` 四个数不得变。★ 任何一处变了都要停下来查，不许调容差

**Checkpoint**: US1 与 US2 同时成立 —— 通用化了，而老路没动。

---

## Phase 5: User Story 3 - 新人查得到一个基准，以及它的依据 (Priority: P3)

**Goal**: 「这个项目用的哪个带号」「这值是确认的还是猜的」「凭什么」——三个问题各有字段答。

**Independent Test**: 对任一路段问一次，响应里同时有 `crs_zone_number` / `crs_central_meridian_deg` / `crs_epsg` / `crs_assumed` / `crs_source`，且依据可从库里读到。

### Tests for User Story 3 ⚠️ 先写，先看它红

- [x] T024 [P] [US3] 加「**五个消费者全部继续工作**」判据（SC-011）：判据是 `scaffold/simulator/crg/export_viewer_mesh.py` 与 `scaffold/tests/integration/sim_inline_script.mjs` **不连库**，仍能拿到 `ready` / `assumed` 两个值
- [x] T025 [P] [US3] 在 `scaffold/tests/contract/test_region_frame.py` 加「**文件与库一致**」判据（SC-012）+ ★ **必须配元测试**（宪法 §V：每条新增的检查必须证明「违规时它真的会红」）：故意改库一处、不同步文件，**断言检查变红**；再断言它说得出**哪一处不一致**

### Implementation for User Story 3

- [x] T026 [US3] 把 `crs.yaml` 改为**从库导出的产物**：加一个导出入口，从 `design_project` / `road_section` 读出基准，写成与现状**同形状**的 YAML（含 `local_frame` / `geographic_frame` / `evidence` 三段）
- [x] T027 [US3] 在 `scaffold/modules/M9-console/app.py` 与 `scaffold/tests/e2e/quickstart_probe.py` 加「**文件与库一致**」检查的实现。★ 它 MUST 在 `ready` / `assumed` / `zone_number` / `central_meridian_deg` / `epsg` 五项上比对，且不一致时 MUST 说清哪个字段、库里是什么、文件里是什么
- [x] T028 [US3] ★ **`crs_evidence` 必须随基准存进库里**（FR-015）：把 `crs.yaml` 那 8 条 `evidence` 搬进 `design_project` 第 2 行的 `crs_evidence`。★ 「确认的还是推断的」及其依据 MUST NOT 只留在文件里
- [x] T029 [US3] 实测确认**另外四个消费者一个都没改**：`grep -n 'crs.yaml' scaffold/simulator/crg/export_viewer_mesh.py scaffold/tests/integration/sim_inline_script.mjs scaffold/modules/M9-console/app.py` —— 三处都必须仍指向文件

**Checkpoint**: 三个 user story 全部独立成立。

---

## Phase 6: Polish & Cross-Cutting Concerns

- [x] T030 [P] 按 `specs/005-project-crs-binding/contracts/契约变更工单-005.md` 的「影响面」表逐项核对，尤其确认 `m6-gateway.geo.v0.1.yaml` **一个字节都没改**（宪法 §I：契约禁止就地改）
- [x] T031 [P] ★ **加一条新断言：`geo.py` 里不得出现 psycopg**（AO 断言，与 `test_dao_contract.py:119-125` 同形但**换文件名**）。★★ 理由：现有那条**只扫 `app.py`**，而 005 恰恰是最容易顺手在 `geo.py` 里连库的一次改动。宪法 §II 的措辞是「结构上做不到」，**不是「AST 恰好没查到」**
- [x] T032 在 `scaffold/run_contract_tests.sh` 的套件注册表里登记新测试（格式 `"名字|说明|模块|桶|路径"`）。★ 五桶选型要按依赖来：只需要 pyyaml 的用 `CONSOLE` 或 `PY`，需要连库的用 `DAO` 或 `API`
- [x] T033 端到端跑 quickstart.md **九步全过**，并把实际输出粘进备注
- [x] T034 按 quickstart.md **第 8 步**做一次真的一致性元测试：改库不动文件 → 检查必须红 → 改回去 → 必须重新绿。★★ **只会红不会绿的检查，同样验不出东西**
- [x] T035 [P] 在 `scaffold/工程决策记录.md` 留一条：为什么基准存在库里、为什么两级必须一致、为什么 `spatial_ref_sys` 只做校验
- [x] T036 [P] ★ 修 `.specify/memory/constitution.md` 两处**已过期**的数字：L24 「12/12 套件」与 L99 「12/12 绿」，实测是 **21** 套（本次改动后还要加）。★ 宪法 §Governance 的原话：「发现文档与实库不一致时：**以实库为准，修文档**」
- [x] T037 跑最终 `./run_contract_tests.sh`，断言 **失败 0 ｜ 跳过 0**，记录通过数并与 T001 的快照对比

---

## Phase 7: ★ 独立对拍（Q4 于 2026-10-12 改定为 (c) 后新增）

**Purpose**: 让「手写投影是准的」这件事**第一次有外部证据**。★ 起因与实测见 research.md **R11**。

**★ 标尺只在测试里用，不在生产路径上。** 生产仍是手写算、不连库。

- [x] T038 在 `scaffold/tests/contract/test_region_frame.py` 加一段**独立对拍**：取路线两端与中心共三个点，把手写 `forward()` 的结果与数据库里 `ST_Transform(ST_SetSRID(ST_MakePoint(lon,lat),4326), 4548)` 逐点比对。★ 桶必须选**连库的那个**（`DAO` 或 `CONSOLE`），不能选 `PY` —— 否则这条测试跑不起来
- [x] T039 给 T038（`scaffold/tests/contract/test_region_frame.py`）定容差并写明依据：基线是 **9 µm**（north）、**逐位相同**（east），实测于 2026-10-12。★ 容差取 **1e-4 m（0.1 mm）**，比实测基线宽一个量级；**不许取 1e-9** —— 那是拿浮点噪声当判据
- [x] T040 ★ 给 T038 **配一个元测试**（宪法 §V）：把 `geo.py` 里某个系数注入一个可观偏差（如 `Y0 = 500000.0` 改成 `500001.0`），**断言对拍变红**。★★ 不做这一步，就无从知道这条测试是「真的在比」还是「两边都调了同一个函数」
- [x] T041 ★ 在 `scaffold/tests/contract/test_region_frame.py` 里 T038 那段旁边写下**这条判据的适用边界**：它只在**同基准**变换（CGCS2000 → CGCS2000）上成立。**跨基准时**（如 WGS84 → CGCS2000）两个实现**可能合法地不一致**，那时不能简单判红，得先判「谁该对」。★ 不写这一句，将来会有人拿它去判一个它管不了的案子
- [x] T042 在 `specs/005-project-crs-binding/quickstart.md` 末尾加**第 10 步**：跑一次对拍，并把 north/east 的实际差值与 9 µm 基线并列打印 ★ 让「它有多准」变成一个每次验收都看得见的数
- [x] T043 ★★★ **声明手写路径到底能算哪些投影族**，算不了的**当场报错（503），MUST NOT 沉默**。★★ 分工按宪法 II（读只经 M3），**不是全放 geo.py**：
  · `scaffold/modules/M3-rpdao/rpdao/repo.py`：从 `spatial_ref_sys.proj4text` 取出**投影族**（`+proj=tmerc` → `tmerc`），放进 `effective_crs()` 的返回值 —— **只有它有连接**
  · `scaffold/modules/M6-api/geo.py`：声明 `IMPLEMENTED_FAMILIES = {"tmerc"}`，族不在此列就抛 `CrsError` —— **它零数据库**（T003 实测：`geo.py` 765 行，`psycopg|connect|PG_DSN|cursor|execute` 只命中 1 处，是 L176 的 docstring）
  · `scaffold/modules/M6-api/app.py`：`CrsError` → 503（`_geo_crs()` L466-474 **已经是这个形状**，不必新加机制）
  ★ 现场：数据模型现在接受**任意 SRID**（宪法原则 VI），但手写换算**只实现了高斯-克吕格**。若 `crs_epsg` 被填成 `2154`（Lambert），今天的代码会**把它喂进高斯-克吕格公式，产出一个看起来正常的错值**。
  ★★ 这正是 004 那条教训的形状 —— **「看着正常的错值比缺失更危险」**。
  ★ 判据：`crs_epsg` 不在已实现集合内时，`_geo_crs()` 抛 503，**错误信息 MUST 说出那个 SRID 是什么**（与 T018 同形）
- [x] T044 ★ 给 `scaffold/tests/contract/test_region_frame.py` 里的 T043 **配一个元测试**（宪法 §V）：把 `crs_epsg` 改成 `2154`，**断言接口返回 503 且错误信息里含 `2154`**，而不是返回一个坐标
  ★★ 不做这一步，就无从知道那个「算不了」的判断是**真的在执行**，还是只是写在注释里

**★ 为什么这一段值这个位置**：现在那四十行只实现**一个投影族**（高斯-克吕格）。有了标尺，将来加 UTM / Lambert 就是「加一个 SRID + 让同一套比对去验它」；没有标尺，每加一个族就是新写一份**没有独立核对**的实现。

---

### ⚠ 005 期间**发现**、但**不属 005 范围**的缺陷（登记在此，防止丢失）

不是 005 引入的，是 005 第一次走到那条路上时露出来的。**不要**把它们算进 005 的完成度。

- [ ] T045 ★★★ 平台级：`numeric` 列的 JSON 类型**取决于路由有没有写返回类型注解**。
  实测（同一份 `Decimal("117.000000")`）：
  · 路由**不写**注解 → 走 `jsonable_encoder` → **`117.0`（float）**
  · 路由**写了** `-> dict[str, Any]` → 走 pydantic → **`"117.000000"`（str）**
  （pydantic 的 `Decimal` JSON 序列化默认保精度，输出字符串）
  ★ 005 让它第一次显形：004 时中央子午线来自 YAML（一个 int），改读库后才变成 `numeric`。
  ★★ 现状：`/sections` 的 `length_m` / `start_station_km` / `end_station_km` 等
  一批 `numeric` 字段**一直是字符串**，而契约写的是 `type: number`。
  ⇒ 先**盘一遍**所有响应里的 `numeric` 字段，列出「契约说 number、实际出 str」的清单，
  再决定是逐个 `float()` 还是收成一个全局出口。**不许**只修看得见的那几个。
- [ ] T046 ★ 给 T045 配两条判据（宪法 §V）：
  ① 契约测试的字段比对**必须同时比类型**，不能只比字段名 ——
     005 之前 §13 只比名字，所以那个字符串缺陷**全绿通过**；
  ② 元测试：故意让一个 numeric 字段以字符串出去，断言比对**变红**。

- [x] T047 ★★★ **写入侧的两级一致守卫**（Q1b 真正落地的地方）。**这条是实测查出来的漏，不是照着 spec 抄的。**
  跑 quickstart 第 2 步（T023 前后）时实测：`UPDATE road_section SET crs_epsg=4547 WHERE id=6` → **`UPDATE 1`**，
  ★ **写入成功了**。全仓扫过三处：
  · `sql/*.sql` —— 没有任何触发器，没有任何 crs 相关 check 约束（只有两条 FK 到 `spatial_ref_sys`）；
  · M2 写路径 —— `grep -rln 'crs_epsg\|crs_zone_number' modules/M2-ingest/` **零命中**；
  · 唯一的执行者只有读取侧 M6 的 503。
  ⇒ **Q1b 的不变量压根不存在**，只有兜底。而 `10_ddl_v0.6.sql` L352 的注释
    **早就写着**「由写入侧拒绝」—— 所以这不是「还没做」，是**建了一半、另一半没人认领**
    （`98_migrate_v06_crs_binding.sql` 后记③ 明确把「两级一致检查」推了出去，而没有任何任务接住）。
  ★★ 两个真实后果：① 一条矛盾的记录能**永久躺在库里**，没有任何一层会报错；
    ② 更危险 —— **导出器会照着它写出一份错的 `crs.yaml`**（文件是派生产物 T015/T026），
       它会忠实地把错的抄出去，下游那四个消费者一句警告都不会有。
  ★★ 而 quickstart 第 2 步自己写着「必须在写入侧拦，**不能**在读路径拦」。
  **做法**：`scaffold/sql/25_crs_two_level_guard.sql`（新文件）
  · `crs_pair_must_agree()` —— 只拦 E2 第 ② 态；态①③④⑤ 一律放行（只拦，不填、不选）；
  · 报错**说得出两级的主体名和值**（SC-009）；`ERRCODE=23514`；
  · **两张表都挂** BEFORE 触发器 —— 只挂 `road_section` 拦不住 `UPDATE design_project`，
    而它改的是**全项目所有路段**的依据；
  · 装之前先查库里**已经**有没有矛盾，有就当场拒绝 ——
    否则这是个「装的时候库本来就坏」的守卫，表现是**永远绿**；
  · 幂等（`DROP TRIGGER IF EXISTS` 在前），可重复执行。
  ★★★ **放哪儿是这条任务的关键**：`sql/25_*.sql` + 挂进 `docker-entrypoint-initdb.d/`。
    **不许只写进迁移** —— 迁移从不自动执行（compose 只挂 10/20/90），
    「只写迁移」对全新装等于**没有守卫**，而文件在、代码也「做了」，谁都发现不出来。
    这与 004 踩的是同一个坑，只换了个对象。
  ★ 覆盖：US1（两级一致是 Q1b 的核心）。
  **判据**：① `tests/e2e/quickstart_probe.py` **判据 2c**（行为，10 条）——
    态②被拒 + 两级值都在 + 两级主体名都在 + **对称性**（改项目也被拒）
    + 同值必须放行 + 清掉一级必须放行
    + ★元测试（摘掉触发器后同一句**不再被拒**，证明拒绝确实出自这个守卫，不是外键/权限/语法）
    + 守卫**装回**且收尾闭环；② `tests/contract/test_region_frame.py` **§18g**（静态，4 条）——
    文件在 + 两张表都挂 + ★**真的会被跑到**（initdb.d 挂载）+ 元测试（拿掉挂载行必须红）。
  ★ 坑（已踩）：`RAISE` 的占位符是**裸 `%`**，不是 `%s` —— 写 `%s` 会输出「值 + 字面 s」，
    **看起来完全正常**；且触发器报错会中止整个事务块，五态必须**各起一个事务**，
    否则第二条起只会报「current transaction is aborted」，测的就不是守卫。
  ★★★ 坑②（更严重，实测撞出来）：**「挂进 initdb.d」这句话必须真去跑一遍才算说过。**
    用一次性库照抄同一套挂载跑全新装，第一次的结果是 ——
    `psql: error: .../25_crs_two_level_guard.sql: Permission denied`，
    随后 **initdb 中止、容器直接退出**。根因：新文件由 `write` 创建，mode 是 `-rw-------`；
    postgres 容器以 `postgres` 用户跑，读不了。
    ★ 所以不可读**不是「少个守卫」，是「全新装根本起不来」**。
    ★ 而 `git ls-files -s` 里所有 sql 全是 `100644` ⇒ **仓库是好的**，
      坑只在本地工作区 —— 恰恰又是跑全新装的那一份（git 只记可执行位，不记读写位）。
    ⇒ 补 `test_region_frame.py` **§18h**：initdb.d 挂的每个宿主文件必须 `& 0o044`，
      配元测试（喂一个 600 的权限位必须红）。
    ★★ 教训：**「我配好了」和「它真的会跑」是两件事，而只有后者能被观测。**
      18g 守「有人会跑它」，18h 守「跑得起来」——两条都是被同一次实测逼出来的。

- [x] T048 ★★★ **重跑 Constitution Check，结果发现「加原则」这件事没有任何东西会变红。**
  **这条不是 spec 里的任务，是执行计划里「重跑 Gate」那一步查出来的。**
  重跑时按宪法 1.1.0 新增的**原则 VI** 逐条对照，发现**两张 Constitution Check 表里
  都没有 VI 这一行**。★ 原因不是「当时认为它不适用」，而是**它当时还不存在** ——
  VI 是 2026-10-12 加的，而这两张表写在它之前。
  ⇒ 于是去查「新增原则时谁会知道」，查出来三个叠在一起的问题：
  · **① 宪法标题层级不一致**：原则 I–V 用 `###`，VI 用 `##` ⇒ 按 `###` 找原则的看不见第六条；
  · **② `plan-template.md` 的门禁表位置只有 `[Gates determined based on constitution file]`**
    —— **一个不列原则的占位符** ⇒ 新原则不会在任何 plan 里产生新行；
  · **③ `scaffold/tests/` 里扫 `.specify/` 零命中** ⇒ 宪法、模板、每份 plan 的检查表
    **全是无人读的文档**。
  ★★★ 合起来正是原则 V 说的东西：「一个永远不会失败的检查，比没有检查更糟」——
  只不过这里的「检查」是**一份没人读的检查表**。它比没有检查更坏：**让人以为已经考虑过了。**
  **做法**：
  · 宪法 `## VI.` → `### VI.`，升 **1.1.1（PATCH，按 Governance 属非语义调整）**，
    **规范性内容一字未动**；
  · `plan-template.md` 的占位符换成**逐条列出六条原则**的空表；
  · `spec-template.md` 新增「通用性与商业化」一节（原则 VI 的三问）；
  · 005 的两张表补 VI 行，重跑结论写进 `plan.md`（结论仍是「通过，无需破例」，
    但依据是**事后实测**的：①§16/§17 + `test_sim_track.py` ②§17a–§17f ③§19）；
  · **新套件 `test_spec_kit_consistency.py`**（`PY` 桶，纯文本、离线、无库无网）。
  ★★ **写这个套件时最重要的一个决定：检查者不许有被检查者的盲区。**
  最自然的写法是 `^### ([IVX]+)\. `，而它会**在 VI 隐形的那整段时间里全绿** ——
  因为「看不见第六条」正是当时的 bug。⇒ 找法放宽成 `#{2,4}` **先找全**，
  层级一致性**单独判**；元测试故意把层级改坏，断言「**仍然找得到六条**、但一致性必须变红」。
  ★★ **第二个决定：001–004 显式登记，不静默豁免。**
  它们的表体是自由格式、写于模板不列原则的年代 —— 不能拿今天的模板判它们罚，
  但那也**不等于它们合规**。⇒ `_LEGACY_NO_PRINCIPLE_ROWS` 显式登记（带理由），
  并断言**名单只许缩短**：补上了逐原则行就必须从名单里删掉，`§3c` 会主动指出来。
  ★★ **第三个决定：顺带把「不许写死套件数」变成判据（`§5`）。**
  重跑时又撞见那个老毛病，且**到处复发**：宪法 §I、宪法工作流第 1 条、
  005 plan 的 Testing 行、001 的 SC-005、001/004 的 quickstart 期望 ——
  **全是同一句话里的自相矛盾**：一边写死一个数字，一边声明判据不是这个数字。
  ⇒ `§5` 扫**规范性文档**（宪法 / 模板 / 各 feature 的 plan·spec·quickstart），
  写死的套件数或通过数即红；配**双向**元测试（4 种真实复发过的写法必须全抓住，
  「`21/21` 无反例」「`0/3` 行有值」「`332/332` 桩号」不得误伤）。
  ★ **刻意不扫** `research.md`/`tasks.md`/`工单-*.md`/`checklists/`：那是**带日期的历史记录**，
  把当时的事实改成今天的事实等于**篡改记录**。这个取舍是刻意的，不是漏扫。
  **判据**：`宪法一致` 套件 **19/19 绿**（含 §1b/§2b/§3b/§3c/§5b 五组元测试）；
  `./run_contract_tests.sh` → 全部套件 **失败 0 ｜ 跳过 0**。
  ★ **诚实边界**：001–004 的 plan **没有**因此合规，只是从「没人知道」
  变成「被登记着、且只会变少」；`§5` 是**模式匹配**不是语义检查，
  换一种花样（如「全绿 N 条」）它抓不住。
  ★ 覆盖：流程本身（宪法 §V / §VI 的强制手段）。

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: 无依赖，立刻可做。T001 是**改之前**的快照，必须先做
- **Foundational (Phase 2)**: 依赖 Setup。★ **BLOCKS 所有 user story**
- **User Stories (Phase 3+)**: 都依赖 Phase 2
  - US1 是 MVP
  - US2 依赖 US1（要先能两级读，才谈得上「不变」）
  - US3 依赖 US1（要先能从库读，才谈得上「从库导出」）
- **Polish (Phase 6)**: 依赖 US1 + US2 + US3
- **★ 独立对拍 (Phase 7)**: 依赖 Phase 2（要有基准表）+ US1（要有能跑通的路段）。★ 与 Phase 6 无先后关系，可与 US2 / US3 并行

### 段内顺序（本仓库的既有规矩）

- **测试先写，先看它红** —— 宪法 §I「契约测试必须覆盖应通过／应拒绝两侧」
- DDL → 数据字典 → DAO → API → 消费者
- ★ **`geo.py` 改完必须跑一次 `test_region_frame.py`**，它不需要数据库、不需要容器、不需要网络

### Parallel Opportunities

- T002 / T003 可并行（读不同文件）
- T008 / T009 可并行（同一文件的两条 SQL 常量，但改的是不同字符串）
- T012 可与 T010 / T011 并行（测的是纯函数）
- T013 / T014 可并行（两条独立判据，同文件不同段落）
- T020 / T021 可并行
- T024 / T025 可并行
- T030 / T031 / T035 / T036 可并行（四个不同文件）

---

## Parallel Example: User Story 1 的测试

```bash
# 两条判据可同时写（同一文件的不同段落）：
Task: "T013 [US1] 两个不同基准的项目共存判据 in tests/contract/test_region_frame.py"
Task: "T014 [US1] 两级不一致必须报错判据（应拒绝侧）in tests/contract/test_region_frame.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1（Setup）—— 先拿「改之前」的快照
2. Phase 2（Foundational）—— **CRITICAL，挡住所有 story**
3. Phase 3（US1）—— 两个项目两套基准
4. **STOP 并验证**：造第二个项目，同一个桩号两个不同经纬度
5. 这就是 MVP

### Incremental Delivery

1. Setup + Foundational → 底座好了
2. US1 → 独立验证 → **MVP**
3. US2 → 独立验证 → 证明老路没动
4. US3 → 独立验证 → 新人查得到依据
5. 每加一段都不许破坏前面几段

---

## Notes

- **[P]** = 不同文件、无未完依赖
- ★ **绝不 `git add -A`**；提交时显式写路径
- ★ **M6 改动后必须 `up -d --build api`，不是 `restart`** —— 004 实测过 `restart` 是静默空转（容器里还是旧配置，而验收**通过**了）
- ★ `geo.py` 是**纯函数模块**，改它不需要数据库就能验
- ★ 任何「查不到」的结论，**必须把查询一起报出来** —— 本仓库已三次出现「筛错了被读成不存在」（research.md R10）

### 备注（T001 / T002 / T003 的实测结果填在这里）

- T001 改之前基线：**通过 18 ｜ 失败 0 ｜ 跳过 0**（005 开工时）。
  改完是 **通过 21 ｜ 失败 0 ｜ 跳过 0** —— 新增的三条是
  `桩号基线`/`覆盖区间`/`桩号文本` 一类此前未登记的套件；
  ★ 005 自己**没有新增一行登记**（判据落在已注册文件内，见下）。
- T002 五桶依赖：`PY`（纯离线）/ `PYDAO`（PY ＋ psycopg）/ `DAO`（打真库）/
  `API`（起 TestClient 走 M6 路由）/ `CONSOLE`（fastapi＋httpx＋pyyaml，
  起 M9 的 TestClient）。★ 选桶按**依赖**选，不按「它属于哪个模块」选 ——
  选错桶的后果是它**跑不起来**，而运行器会把它算成失败，不是跳过。
- T003 `geo.py` 零数据库证据：AST 扫描确认无 psycopg / ConnectionPool /
  `.execute(` / cursor / `connect` / `PG_DSN` / 硬写的 `postgresql://`。
  ★★ 但这条判据当时**只扫 `app.py` 一个文件** —— 守的是「当时想到的那一个」，
  不是宪法 §II 要的「结构上做不到」。⇒ T031 改成清单 ＋ **清单完整性检查**，
  那条完整性检查**当场抓出一个我原本不知道的 `gaps.py`**。
