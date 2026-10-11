---
description: "Task list for 区域帧 —— 桩号与真实地理坐标的互算与呈现"
---

# Tasks: 区域帧 —— 桩号与真实地理坐标的互算与呈现

**Input**: Design documents from `specs/004-region-frame/`

**Prerequisites**: [plan.md](./plan.md)、[spec.md](./spec.md)、[research.md](./research.md)、[data-model.md](./data-model.md)、[contracts/](./contracts/)、[quickstart.md](./quickstart.md)

**Tests**: 本 feature **要求测试**。理由是宪法原则 V（反空转）：
本 feature 的原始 SC-001 就栽在这上面 —— 一条「往返闭合」检查看起来在验正确性，
实际只证明系统与自己一致。**没有元测试，就没有办法区分这两者。**

**Organization**: 按用户故事分相，每个故事可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件，无依赖）
- **[Story]**: 所属用户故事
- 每条任务给出精确文件路径

## Path Conventions

本 feature 在 **`scaffold/modules/M6-api/`（换算能力）** 与
**`scaffold/modules/M9-console/`（页面）** 内扩展。契约正本在
**`scaffold/contracts/openapi/`**。

---

## ⚠️ 一处与 spec 优先级的偏离，以及理由

spec 把 **US1（桩号↔经纬度）标为 P1**，与实现顺序一致，无须偏离。
但要注意：**US1 所需的全部零件是纯计算，没有一条依赖前端。**
所以 Phase 2（投影与线元求值）与 Phase 3（暴露成可核对的能力）之间的分界是
「纯函数」与「接口」，不是「底层」与「上层」。

★ 这个分界是刻意的：**SC-007 要求换算能在没有浏览器、没有地图服务的环境里
完整验证。** 把纯计算放进 Foundational，就等于让整个 MVP 在离线状态下可验 ——
这不是实现顺序问题，是**可验证性**问题。

---

## ⚠️ 实施阶段出现的一条**计划外改动**，以及它为什么必须做

**改动**：`scaffold/tests/contract/test_api_routes.py` 的 `contract_paths()`
从「只读 v0.3」改成「读 `contracts/openapi/m6-gateway*.yaml` 的并集」。

**为什么计划里没有它**：T001 只说了「契约归位：正本写进 `scaffold/contracts/openapi/`」，
没说这条既有测试只认识**一份**契约。

**为什么必须做**：T011–T013 加完三条路由，`m6-路由` 立刻红了 ——
它的第 2 组做的是**双向相等**（声明 ↔ 实现），而声明面当时只有 v0.3。
两条路可走，且只有一条对：

| 做法 | 判定 |
|---|---|
| 把三条 geo 路由也补进 v0.3 | ✗ 违反宪法原则 I「契约禁止就地改」。v0.3 是骨架期冻结的正本 |
| 让这条测试认识**增量契约** | ✓ 声明面本就是并集；扩展是新文件，正本不动 |

⇒ 这不是放松检查，是**把检查的范围说准**。改完后
「契约声明 20 条 / 实现 20 条 / 契约来源 2 份 / ✓ 双向一致」——
两侧条目数与改动前**一样严**。

★★ **顺带一提：这条红是好事。** 它证明那条双向相等检查**真的在跑**，
不是一个会自己变绿的摆设。

---

## Phase 1: Setup

**Purpose**: 前提核实与契约归位

- [x] **T001** 契约归位：正本写进
      `scaffold/contracts/openapi/m6-gateway.geo.v0.1.yaml`，
      并在 `specs/004-region-frame/contracts/README.md` 留指针
      ★ **新增文件，不就地改 v0.3**（宪法 Principle I「契约禁止就地改」）。
      就地升版看起来省事，但那正是该原则要挡的：
      改掉 v0.3，就再没人能读到 v0.3 说过什么

- [x] **T002** [P] 核实 `scaffold/modules/M9-console/config/crs.yaml` 可作为投影参数的
      唯一出处：`ready: true`、`assumed: false`、`zone_number: 39`、
      `central_meridian_deg: 117`、`epsg_after_confirm: 4548`
      ★ **已核实（2026-10-11）**：`assumed` 与 `epsg_after_confirm` 的一致性是
      **既有契约测试强制**的，两个方向都有 —— 本 feature 不得破坏它

- [x] **T003** [P] 核实 `alignment_element` 的读法：
      33 行、全部 `section_id=6`、`x` 是北坐标（约 278 万米）、
      `y` 是东坐标（约 53.5 万米）
      ★ **已核实（2026-10-11）**：读反的失败形态是
      **4.54°N / 137.16°E —— 太平洋里**，肉眼可辨（SC-004 就钉这一条）

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 本相完成前，任何用户故事都不能开始

**Purpose**: 投影与线元求值 —— 两块纯计算，离线即可完整验证

- [x] **T004** 把线元求值搬进 M6：在 `scaffold/modules/M6-api/Dockerfile` 加
      ```dockerfile
      RUN mkdir -p /app/m2geom && printf '' > /app/m2geom/__init__.py
      COPY modules/M2-ingest/adapters/errors.py /app/m2geom/errors.py
      COPY modules/M2-ingest/adapters/geom.py   /app/m2geom/geom.py
      COPY modules/M9-console/config/crs.yaml   /app/crs.yaml
      ```
      ★ **两个 .py 必须逐字节原样，一个字都不改。**
      `geometry_solver.py` 自己写着「另起炉灶必然漂移」——
      本 feature 在已验证到 3.2×10⁻⁸ m 的实现之上加投影，**不重写它**
      ★ 不整个搬 `adapters/`：它的 `__init__.py` 会 `import weidi`，
      拖进 19 个文件格式解析器。`geom.py` 的包内依赖只有一个
      （`from .errors import SourceInvalid`），所以子包方案成立

- [x] **T005** 写 `scaffold/modules/M6-api/geo.py`：椭球参数（CGCS2000：a、f）
      与**高斯-克吕格正算**（(x, y) → (lat, lon)），参数全部**从 `crs.yaml` 读**
      ★ 零硬编码（FR-005；宪法「配置全外置」）。带号、中央子午线、假东偏
      一律来自配置，代码里不得出现 `117` 或 `500000`

- [x] **T006** 同文件加**逆算**（(lat, lon) → (x, y)）与**带内检查**
      ★ 3 度带半宽 1.5°；本路线距中央子午线仅 0.34–0.38°，
      **完全落在带内**，但超带必须报错而不是硬算
      ★ 依赖 T005（同一文件，不并行）

- [x] **T007** 写 `scaffold/modules/M6-api/geo.py` 的配置读取：
      从 `/app/crs.yaml` 读 `geographic_frame`，**同时读 `assumed`**
      ★ FR-005 要求 `assumed: true` 与 `false` 两种状态下都能工作 ——
      前者照常换算，但输出必须带上「值是推断的」这个事实
      ★ 容器里读不到配置时**必须显式失败**，不得静默用默认值。
      M6 已经踩过一次同类坑：`modules.yaml` 读不到时 `_module_phases()`
      静默返回 `{}`，`phase` 永远是 `null`

- [x] **T008** 写 `scaffold/tests/contract/test_region_frame.py` 骨架：
      `_refusal(fn) -> str` 辅助（把「应当失败」写成可断言的形式），
      并接进 `scaffold/run_contract_tests.sh`
      ★ 元测试的 `except` 子句**必须窄** —— 本仓库踩过两次：
      宽 `except` 会把「函数根本没被调用」也算成「正确拒绝了」

- [x] **T009** [P] 在 `test_region_frame.py` 加**搬运不漂移**的守卫：
      断言 `/app/m2geom/geom.py` 与 `modules/M2-ingest/adapters/geom.py`
      **逐字节相同**（`errors.py` 同理）
      ★ 这是本 feature 唯一的结构性风险：同一份数学存在于两个位置。
      **不靠「记得同步」，靠「忘了会红」**
      ★ 元测试：改原件一个字节，这条必须红

- [x] **T010** [P] 在 `test_region_frame.py` 加**正算/逆算往返**检查
      （≥ 5 点，跨 116.5–120.34°E、24.5–26°N）
      ★ **实测基线：最大偏差 0.457 mm**（本会话已测）。
      判据取 **1 mm** 留一倍余量
      ★ 这条**只作必要条件**。它闭合不代表换算对 —— 见 T016

**Checkpoint**: 投影与线元求值离线可验 —— US1 可以开始

---

## Phase 3: User Story 1 - 桩号与经纬度互相算得出来，而且能核对 (P1) 🎯 MVP

**Goal**: 拿到桩号能算出经纬度，反过来也能；两者在米级以内闭合，
且**用一条真能失败的检查证明它是对的**

**Independent Test**: `curl` 一个已知桩号，得到经纬度，再算回去；
把 `crs.yaml` 的中央子午线临时改成 114，包围盒检查**必须失败**
（SC-008）。**不需要浏览器、不需要地图服务**

- [x] **T011** [US1] 在 `scaffold/modules/M6-api/app.py` 加
      `GET /v1/geometry/sections/{section_id}/geo`（桩号 → 经纬度）
      与 `GET …/geo/reverse`（经纬度 → 桩号）
      ★ 输出**必须带 `epsg`（4548）**，不得只写「39 带」——
      39 带有两个 EPSG 编码，取错东坐标差 **39,000,000 m**（FR-003）
      ★ 同时回带中间量 `x` / `y`：出错时能一眼看出是**几何**错了
      还是**投影**错了

- [x] **T012** [US1] 同文件加**越界拒绝**（FR-004）：
      桩号超路段范围 → 400；`section_id` 不存在 → 404；经纬度出带 → 422
      ★ **不得返回最近点、不得返回原点、不得返回空对象冒充成功**
      ★ 依赖 T011（同一文件）

- [x] **T013** [US1] 加 `GET /v1/geometry/sections/{section_id}/geo/route` 的
      **桩号域部分**（33 段的元数据：桩号区间、长度、半径、方位角、
      每段点列、`bbox`）
      ★ **每段必须给点列，不能只给首末点** —— 圆曲线矢高最大 **12.588 m**、
      缓和曲线最大 **1.736 m**，只连首末点会把曲线变成折线（FR-006）

- [x] **T014** [US1] 在 `test_region_frame.py` 加**应通过**侧：
      三条路径的正常输入、输出字段齐全性（含 `epsg` / `assumed`）
      ★ 契约测试必须覆盖**应通过 / 应拒绝两侧**（宪法 Principle I）

- [x] **T015** [US1] 在 `test_region_frame.py` 加**应拒绝**侧：
      超范围桩号、不存在的 `section_id`、出带经纬度，各断言明确的错误码
      ★ **SC-006：100% 报错，无一例返回看似成功的值**
      ★ 元测试：把拒绝逻辑改成返回 `None`，这条必须红

- [x] **T016** [US1] ★ 在 `test_region_frame.py` 加 **SC-001：与独立估计一致**
      在**非格点**桩号上，比 `point_at`（精确积分）与
      「`geometry_point` 相邻两个 20 m 格点的线性插值」，**≤ 1 m**
      ★★ **这条不能直接拿 `geometry_point` 在格点上比** ——
      那张表**就是 `point_at` 自己写进去的**（`geometry_solver.py` 委托
      `adapters.geom`）。在格点上比 = **拿函数跟自己对**，与原始
      SC-001「往返闭合」是同一类错误
      ★ 预期差约 **0.196 m**（R=255、20 m 弦的矢高）—— 会通过，但**不空转**
      ★ 元测试：把插值端点取错一格，这条必须红

- [x] **T017** [US1] 加 **SC-002：包围盒**：33 段端点落点全部在
      **25.16–25.21°N / 117.34–117.38°E**
      ★ 措辞纪律：这条**不是**「核实位置对不对」，是**算术检查** ——
      它钉住投影参数没有取错。实测中心 25.18596°N / 117.35926°E
      **不是独立测量值**，是同一批坐标算出来的，**不得当作位置已证实的依据**

- [x] **T018** [US1] ★★ 加 **SC-008：对照**（宪法 Principle V）——
      把中央子午线**故意取错一整带**（117°E → 114°E）时，
      **SC-002 必须失败**
      ★ **这是本 feature 最要紧的一条检查。** 若改错带号它照样通过，
      说明 SC-002 量不到它声称在量的东西 —— 而原始 SC-001 正是栽在这个坑里
      ★ 这条本身即元测试，不需要再配一条

- [x] **T019** [US1] 加 **SC-003 方位角**（两端逆算的方位角与 `azimuth_deg`
      相差 ≤ 0.5°）与 **SC-004 读反**（`x`/`y` 故意调换后纬度 **< 10°N**）
      ★ SC-004 钉的是**失败形态必须肉眼可辨** ——
      读反的落点在太平洋，不能伪装成一个合理的近邻

**Checkpoint**: US1 完成且可独立验证。**这就是 MVP。**

---

## Phase 4: User Story 2 - 在真实地理坐标下看到路网骨架 (P2)

**Goal**: 页面上看到毕设路 5.8 km 的走向，33 段线形可区分、可点击

**Independent Test**: 页面上量取首末点经纬度，与 US1 算出的值一致；
圆曲线段**是弯的**；3 条 0 路段的 `road_line` 不出现

- [ ] **T020** [US2] 建 `scaffold/modules/M9-console/geo.html`：
      **自包含 HTML**（内联 `<script>` + `<style>`）、
      **无外部 `script`/`link`**、**无写死端口**
      ★ 这三条**已有契约测试**会验，不是新要求
      ★ 用 CDN 版 CesiumJS 会被既有测试直接判红 ——
      那条断言当初正是为挡它写的（见 research.md R6）

- [ ] **T021** [US2] 在 `geo.html` 里画 33 段：直线 / 圆曲线 / 缓和曲线
      **用不同画法区分**（FR-006）
      ★ **按点列画，不按首末点连直线** —— 否则曲线退化成折线

- [ ] **T022** [US2] 保持**真实比例与真实方位**（FR-007）：
      南北与东西**不得互换**，长宽比不得失真
      ★ 实测包围盒 **4.552 km（南北）× 3.106 km（东西）**
      ★ SC-005：长宽比相差 ≤ 1%

- [ ] **T023** [US2] 点击任意段弹出桩号区间、长度、半径、方位角（US2 场景 3）

- [ ] **T024** [US2] **排除 3 条 0 路段的 `road_line`**（`TEST-PRJ-*`，FR-008）：
      不出现，或明确标为「无几何数据」
      ★ 空壳画出来是**一个永远空的图层**，看起来像 bug、实际是没数据

- [ ] **T025** [US2] 在 `scaffold/modules/M9-console/app.py` 加 `GET /geo`，
      页面经**既有的只读代理 `/gw/{path}`** 取 M6 的数据
      ★ **M9 不得直连数据库**（宪法 Principle II）。既有 AST 断言守着这条
      ★ `geo.html` 用 `.read_text()` → `HTMLResponse`，延续 003 的五行

- [ ] **T026** [US2] `scaffold/modules/M9-console/index.html` 加一个指向 `/geo` 的入口

- [ ] **T027** [US2] `scaffold/modules/M9-console/Dockerfile` 加 `COPY geo.html`
      ★ **容器里有路由、镜像里没文件 = 线上 500、而宿主机测试全绿。**
      这条宪法教训已经踩过一次

- [ ] **T028** [US2] 把 `geo.html` 加进 `test_console.py` 的 `PAGES` 字典
      ★ 那条一致性检查**本身就是「新增页面必须登记」的守卫** ——
      **不新建重复的守卫**

- [ ] **T029** [US2] 在 `test_console.py` 加本 feature 的新检查：
      `geo.html` 不引用外部资源、不含「合格/不合格」判定字样
      ★ 页面**只换算、不判定**。1 m 是**换算正确性**的判据，不是养护阈值

**Checkpoint**: US1 与 US2 都可独立工作

---

## Phase 5: User Story 3 - 沿线设施在地图上标得出来 (P3)

**Goal**: 3 个断面（含仿真设备所在的 `K2+900`）在图上看到真实位置

**Independent Test**: 3 个断面算出的经纬度全部落在 US1 给出的包围盒内

- [ ] **T030** [US3] 位置**由桩号算出**，并在界面上标注
      「位置由桩号换算得到，库中经纬度列为空」（FR-009）
      ★ 库里的 `lat/lon` 列**全空（0/3 行有值）** —— 位置没有别的来源

- [ ] **T031** [US3] ★ 加 **FR-010：`NULL` 不得读成 `(0, 0)`**
      ★ 若把 `NULL` 读成 `0`，3 个断面会一起堆在几内亚湾外的 `(0°,0°)`
      而**不报任何错** —— **它是静默的**，所以必须有显式检查
      ★ 元测试：把某行的 `lat/lon` 置 `NULL`，这条必须红

- [ ] **T032** [US3] `SIMWIM01`（仿真设备）与真实设备**视觉上可区分**
      ★ 延续既有的溯源约定：仿真数据不冒充实体资产

**Checkpoint**: 三个故事全部可独立工作

---

## Phase 6: Polish & Cross-Cutting

- [ ] **T033** [P] 按 [quickstart.md](./quickstart.md) 的**七步**逐条跑一遍，
      把实测输出记进 quickstart 或提交信息
      ★ 特别是第 2c 步（故意错带号）与第 3 步（越界拒绝）——
      这两步是**验收动作**，不是可选演示

- [ ] **T034** [P] 跑 `cd scaffold && ./run_contract_tests.sh`，
      确认**全部套件绿**（当前基线 20 个套件）

- [ ] **T035** 在 `scaffold/contracts/openapi/m6-gateway.geo.v0.1.yaml` 里
      核对三条路径与实现一致（真源是运行时的 `/openapi.json`）
      ★ 本文件是**人工摘要**，不参与运行时校验 —— 发现不一致时以实库为准

- [ ] **T036** ★ 收尾时**重新审视** `research.md` R4 记的那笔技术债：
      `crs.yaml` 住在 `modules/M9-console/config/` 而 M6 也要读它。
      本 feature 再 COPY 一份是**权宜**，不是终局
      ★ **还债时机**：第三个模块需要读投影参数时。届时搬到公共位置，
      并同步改 003 的三处（M9 的 Dockerfile、003 的 `data-model.md`、
      `test_console.py`）

- [ ] **T037** ★ 修正 `scaffold/modules/M9-console/config/crs.yaml` 的
      `evidence:` 第 3 条的**过时数值**：现写「落点为 25.08°N / 117.34°E」，
      但实测路线范围是 **25.168–25.209°N**。`25.08` 是早期单点采样的值
      ★ 修的时候**不要删掉旧值**，要标清它是什么 —— evidence 是这个值的
      **历史**，也是复核的起点

- [ ] **T038** [P] 在 `scaffold/modules/M6-api/` 留一行说明：
      本模块的 `m2geom/` 是**逐字节搬来的副本**，为什么搬、谁在守它
      ★ 接手者看到两个同名文件时的第一个问题是「哪个是真的」，
      这一行就是答案

---

## Dependencies & Execution Order

### Phase Dependencies

```text
Phase 1 Setup（已基本完成）
   └→ Phase 2 Foundational（阻塞全部故事）
        ├→ Phase 3 US1 (P1) 🎯 MVP ──┐
        │                             ├→ Phase 6 Polish
        ├→ Phase 4 US2 (P2) ─────────┤
        │    ★ 依赖 US1                │
        └→ Phase 5 US3 (P3) ─────────┘
             ★ 依赖 US1
```

### ★ 三个不能弄反的先后关系

1. **T004 先于一切** —— `m2geom/` 不到位，T005 起的每一件事都跑不起来。
2. **T018（SC-008 对照）必须在 T017（SC-002）之后、且必须真的跑一遍。**
   先写对照再写判据，等于先想好怎么验再决定验什么。
3. **T016 的「非格点」是硬要求。** 图省事改成在格点上比，这条检查就退回
   空转 —— 而且它会**照样通过**，不会有人发现。

### Parallel Opportunities

| 可并行 | 说明 |
|---|---|
| T002 / T003 | 两条独立的前提核实 |
| T009 / T010 | 两个独立文件里的检查 |
| T031 / T032 | US3 的两条独立项 |
| T033 / T034 / T038 | Polish 里的独立项 |
| US2 ∥ US3 | 都只依赖 US1，彼此独立 |

---

## Implementation Strategy

### MVP First

**只做 Phase 1 + 2 + 3（T001–T019）= MVP。**

MVP 是一个**纯粹的计算能力**：桩号 ↔ 经纬度，加上七条判据
（SC-001 / 001b / 002 / 003 / 004 / 006 / 008）。
**它没有页面，且不需要页面** —— SC-007 明确要求它能在
没有浏览器、没有地图服务的环境里完整验证。

★ 这也是本 feature 的价值主张：**先把「算得对」钉死，再谈「看得见」。**

### Incremental Delivery

| 交付 | 内容 | 用户能做什么 |
|---|---|---|
| 1 | Phase 1–3 | 用 `curl` 换算桩号与经纬度，并经得起错带号对照 |
| 2 | + Phase 4 | 在页面上看到路网骨架 |
| 3 | + Phase 5 | 看到 3 个断面在哪 |
| 4 | + Phase 6 | 全部验收通过、技术债与文档收尾 |

---

## Notes

- **契约禁止就地改**：本次是**新增** `m6-gateway.geo.v0.1.yaml`，
  不是把 v0.3 升成 v0.4。改掉 v0.3 就再没人能读到 v0.3 说过什么。
- **M9 不碰数据库**：页面只经既有的 `/gw/` 只读代理取数。既有 AST 断言守着。
- **不声称位置**：坐标与设计者自己的地形对不上（r = −0.144，系统差 +825 m），
  五种带号试遍仍无相关。**两种读法都还开着**，平台不替来源不明的坐标背书。
  详见 `research.md` R7 与 spec 的已澄清第 4 条。
- **提交纪律**：一次只动一件事；提交信息写**为什么**，
  尤其是「这里踩过什么坑、改动后靠什么防住」。
