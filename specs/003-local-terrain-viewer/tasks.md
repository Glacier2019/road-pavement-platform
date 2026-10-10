---
description: "Task list for 局部帧三维起伏查看器"
---

# Tasks: 局部帧三维起伏查看器

**Input**: Design documents from `specs/003-local-terrain-viewer/`

**Prerequisites**: [plan.md](./plan.md)、[spec.md](./spec.md)、[research.md](./research.md)、[data-model.md](./data-model.md)、[contracts/](./contracts/)、[quickstart.md](./quickstart.md)

**Tests**: 本 feature **要求测试**。理由是宪法原则 V（反空转）是本仓库的头号工程纪律，
且本 feature 有四条"反向"判据（验证系统**拒绝**什么）—— 没有它们，
正向判据全绿也证明不了系统会拒绝坏输入。

**Organization**: 按用户故事分相，每个故事可独立实现与验证。

## Format: `[ID] [P?] [Story] Description`

- **[P]**: 可并行（不同文件，无依赖）
- **[Story]**: 所属用户故事
- 每条任务给出精确文件路径

## Path Conventions

本 feature 在 **`scaffold/modules/M9-console/`** 内扩展，交付目录在 **仓库根下 `artifacts/sim/`**。

---

## ⚠️ 一处与 spec 优先级的偏离，以及理由

spec 把 **US4（稳定的交付目录）标为 P2**，但在实现上它**是 US1 的前置** ——
页面要渲染的网格必须有个地方放。

处理方式：把**交付目录的机制**（固定路径 + 只读挂载 + 白名单路由）放进
**Phase 2 Foundational**，作为 US1 的阻塞前置；
US4 自己的相位只保留**内容侧**（视频/轨迹搬入、缺失降级提示）。

★ 这是刻意的：**spec 的优先级说的是用户价值，不是实现顺序。**
把机制当前置、把内容当故事，两件事都不失真。

---

## Phase 1: Setup

**Purpose**: 目录骨架与契约归位

- [ ] **T001** 建交付目录骨架 `artifacts/sim/`（含 `.gitkeep`）
      ★ **已核实（2026-10-10）**：`.gitignore` L29-36 的 `*.csv` / `*.png` /
      `*.mp4` / `*.crg` 规则**是路径限定**的（`scaffold/simulator/crg/…`），
      所以 `artifacts/sim/` **不受影响** —— `git check-ignore` 对四个样本文件
      全部无输出。**这条前提已确认，不再是风险。**
      ★ 但仍要保留这一步：产物一旦落进被忽略的范围，它就永远进不了仓库，
      而本地测试全绿 —— 正是宪法「新增页面/资产必须同时登记」那条教训的形状。
      用 `git check-ignore` 复核一次比推理可靠

- [ ] **T002** [P] 建 `scaffold/modules/M9-console/config/crs.yaml`，
      含 `ready: false` / `assumed: true` / `evidence` 三条（见 data-model.md §4）

- [ ] **T003** 把契约搬到宪法要求的 `scaffold/contracts/delivery/artifact_manifest.v0.1.schema.json`
      （宪法「接入约束」要求契约文件提交到 `contracts/`），
      并在 `specs/003-local-terrain-viewer/contracts/README.md` 留一行指针
      ★ **只保留一份正本**。两份会漂移，而漂移的方向永远是设计稿过期

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ CRITICAL**: 本相完成前，任何用户故事都不能开始

- [ ] **T004** 写 `scaffold/simulator/crg/export_viewer_mesh.py`：
      CRG → 两档 LOD 二进制（u 步长 4.0 m 与 1.0 m，nv=9）
      ★ **必须断言 `nu*nv < 65536`**。越界时 `Uint16` 静默截断，
      几何以"看起来只是有点错"的方式坏掉 —— 不报错，只是错
      ★ 高程取 **CRG 实测地形高程**，不是中线控制点 z
      （后者比实际路面高约 200 mm 且无路拱）

- [ ] **T005** 同脚本产出 `artifacts/sim/manifest.json`，符合 T003 的契约。
      `bytes` 必须取自磁盘实测，不是估算
      ★ 依赖 T004

- [ ] **T006** 在 `scaffold/modules/M9-console/app.py` 加
      `GET /artifacts/{name}`：**只接受清单里登记的 name**，其它 404；
      路径穿越 404；**必须支持 Range（返回 206）**
      ★ 不返回 206 的实现在浏览器里"能播、但拖不动进度条"，
      而"能播"会让粗看的人以为它对了
      ★ 依赖 T005

- [ ] **T007** `scaffold/docker-compose.skeleton.yml` 的 `console` 服务段加
      `volumes: - ./artifacts/sim:/data/sim:ro` 与 `SIM_ARTIFACTS_DIR: /data/sim`
      ★ **这是 `console:` 的第一个 `volumes:`** —— 它改变了 M9 的暴露面，
      提交信息里要写清楚

- [ ] **T008** 验证只读**确实生效**：`docker exec rp-console touch /data/sim/x` 必须失败
      ★ **真的试一次，不是读 compose 确认写了 `:ro`。**
      宪法原则 II 的口径是"结构上做不到"优于"约定不这么做"
      ★ 依赖 T007

**Checkpoint**: 交付目录可读、容器不可写、白名单路由在跑 —— US1 可以开始

---

## Phase 3: User Story 1 - 看见这条路本身的三维起伏 (P1) 🎯 MVP

**Goal**: 浏览器里看到这条 5805.4 m 山路的三维起伏，可交互、可调垂直放大

**Independent Test**: 打开 `/sim`，路面可见；把垂直放大调到 1.0 时画面变平；
调整视角时几何实时响应。**不需要仿真、不需要带号、不需要外部服务**

- [ ] **T009** [US1] 建 `scaffold/modules/M9-console/sim.html` 骨架：
      自包含（内联 `<script>` + `<style>`）、无外部 `script`/`link`、
      无写死端口
      ★ 这三条**已有契约测试**会验，不是新要求

- [ ] **T010** [US1] 内联手写 WebGL：网格上传（`Float32` 位置 + `Uint16` 索引）、
      Lambert 着色、轨道相机
      ★ 依赖 T009

- [ ] **T011** [US1] 垂直放大控件：**系数始终可见**、真尺度（1.0）可取、
      **禁止自动施加未声明的放大**
      ★ 平面 4521 m 对高差 29.75 m = 1:152，真尺度看起来就是一张平面图。
      这条不是渲染细节，是防止有人拿放大过的图判断真实纵坡
      ★ 依赖 T010

- [ ] **T012** [US1] 路面之外显示"此处无数据"，**不得延伸出臆造的地面**
      ★ 依赖 T010

- [ ] **T013** [US1] 加 `GET /sim` 路由（照 `geometry_page()` 的形状）、
      `Dockerfile` 加 `COPY sim.html ./`、`index.html` 加入口、
      `test_console.py` 的 `PAGES` 字典登记 `sim.html`
      ★ **四处缺一不可**：漏 COPY 会让容器里 500 而宿主机测试全绿；
      漏 PAGES 登记会让清单检查红
      ★ 依赖 T012

- [ ] **T014** [US1] 契约测试新增：页面不引用外部资源、
      交付目录只读、网格大小上限，**每条配元测试**
      ★ 依赖 T013

**Checkpoint**: US1 可独立交付 —— 用户要求的"必须"那件事已经能交出去

---

## Phase 4: User Story 2 - 看见车在这条路上走 (P1)

**Goal**: 在三维路面上叠一条车辆轨迹

**Independent Test**: 轨迹从起点走到终点；车辆符号可辨识；
删掉 CSV 的 `x_m` 列后页面**明确报告缺位姿**，而不是画在 (0,0)

- [ ] **T015** [US2] 改 `scaffold/simulator/crg/05_vehicle_on_crg.cpp` 的
      `WheelLoadCsv::Write()`（约 L249-261）：追加 `x_m, y_m, heading_deg`
      ★ **追加，不重排。** 已有列已被 `plot_wheel_loads.py` 消费，
      重排会让它**静默读错列** —— 不报错，只是图变成错的

- [ ] **T016** [US2] 重跑仿真产出新 CSV，搬进 `artifacts/sim/`，登记进清单
      ★ 这是本 feature 内**唯一需要执行仿真**的地方
      ★ 依赖 T015

- [ ] **T017** [US2] 页面解析轨迹、叠车辆符号；**符号放大倍数必须可见**
      ★ 鸟瞰下车辆宽 **0.442 px** —— 画真实尺寸等于什么都没画
      ★ 车辆贴在**网格表面**上，`z` 由网格查得，不用 `chassis_z_m`
      ★ 依赖 T016

- [ ] **T018** [US2] 缺平面位姿时**明确报告**，不得静默画在原点
      ★ 判据：车辆的平面位置是否始终落在网格的平面外接矩形内；
      越界即报"轨迹与路面可能不同批次"
      ★ 依赖 T017

- [ ] **T019** [US2] 契约测试：CSV 表头含三列，**删掉任一列必须被认出**（元测试）
      ★ 依赖 T016

**Checkpoint**: US1 + US2 都独立可验

---

## Phase 5: User Story 3 - 看见数据沿着路分布 (P2)

**Goal**: 按桩号把沿程量染到路面上

**Independent Test**: 切换沿程量时颜色改变；无数据的区段与数值 0 **视觉可区分**

- [ ] **T020** [US3] 沿程量选择器：高程 / 轮荷 / 曲率

- [ ] **T021** [US3] ★ **无数据的区段必须与数值 0 使用不同的视觉编码**
      ★ 这是本项目一条反复出现的教训：一个表示"没有数据"的零，
      不该和一个实测出来的零共用一种表达
      ★ 依赖 T020

- [ ] **T022** [US3] 沿程查询交互（点击/悬停读出该处数值与里程）
      ★ 依赖 T020

- [ ] **T023** [US3] 契约测试：页面不含"合格/不合格/超标"判定字样，
      **塞一句"判定为合格"必须被认出**（元测试）
      ★ 宪法「未标定的阈值不得用于生产判定」。页面只显示，不判定
      ★ 依赖 T021

---

## Phase 6: User Story 4 - 仿真产物从一个稳定的交付目录提供 (P2)

**Goal**: 视频与轨迹从固定路径提供，缺失时降级而不是报错

> 交付目录的**机制**已在 Phase 2 完成；本相只做**内容侧**。

**Independent Test**: 视频在页面里能播**且能拖进度条**；删掉一个产物后页面提示缺失而非崩溃

- [ ] **T024** [US4] 把两个 MP4 与 CSV 搬进 `artifacts/sim/` 并登记进清单

- [ ] **T025** [US4] 缺失产物的降级提示：清单里 `present: false` 的项
      在页面上显示为"未产出"，而不是空白或错误
      ★ 依赖 T024

- [ ] **T026** [US4] 契约测试：`Range` 请求返回 **206** 且带 `Content-Range`
      ★ 这条必须单独验：不返回 206 时视频**照样能播**，只是拖不动
      ★ 依赖 T024

---

## Phase 7: User Story 5 - 带号是一个可以被看见的假定 (P3)

**Goal**: 带号可配置、标记可见、依据可查

**Independent Test**: 页面上能读到当前带号与 `assumed` 状态；改 `crs.yaml` 后页面随之改变

- [ ] **T027** [US5] 页面/日志显示当前坐标基准与 `assumed` 标记，
      以及推断依据（见 data-model.md §4）
      ★ `ready` 与 `assumed` **是两个不同的标记**，不能合并：
      一个说"能不能用"，一个说"值可不可信"

- [ ] **T028** [US5] 契约测试：`assumed` 标记在页面上可见；
      **把它删掉必须被认出**（元测试）
      ★ 带号错了，位置误差是几百公里，而任何局部视图里都看不出来。
      它只在交付那一刻暴露
      ★ 依赖 T027

---

## Phase 8: Polish & Cross-Cutting

- [ ] **T029** 跑完 [quickstart.md](./quickstart.md) 的八条判据，
      **四条反向判据必须真的反向**（404 / 404 / 写入失败 / 无判定字样）

- [ ] **T030** 跑 `cd scaffold && ./run_contract_tests.sh`，
      确认**套件数比改动前多**
      ★ 套件数没变说明新检查没挂进总运行器 —— 那是宪法原则 V 说的空转

- [ ] **T031** 更新 `scaffold/modules/M9-console/README.md` 与
      `scaffold/simulator/crg/README.md`（新页面、新脚本、新挂载）

- [ ] **T032** 提交：显式路径，提交信息写清"为什么"，
      尤其是 `console:` 的第一个 `volumes:` 改变了 M9 的暴露面

---

## Dependencies & Execution Order

### Phase Dependencies

```text
Phase 1 (Setup)
    ↓
Phase 2 (Foundational)  ← 阻塞所有用户故事
    ↓
    ├── Phase 3 (US1, P1) 🎯 MVP
    │       ↓
    │   Phase 4 (US2, P1)   ← 依赖 US1 的网格渲染
    │       ↓
    │   Phase 5 (US3, P2)   ← 依赖 US1 的着色管线
    │       ↓
    │   Phase 6 (US4, P2)   ← 与 US2 共用轨迹产物
    │       ↓
    └── Phase 7 (US5, P3)   ← 独立，可随时做
            ↓
        Phase 8 (Polish)
```

### ★ 三个不能弄反的先后关系

| 关系 | 弄反的后果 |
|---|---|
| 抽稀脚本 → 页面 | 页面没有数据可画，调试时看到的空白会被误当成渲染 bug |
| compose 挂载 → 容器验证 | 404 会掩盖真问题，因为缺挂载和缺文件都是 404 |
| C++ 改列 → 重跑仿真 | 重跑白跑，且新 CSV 仍然没有三列 |

### Parallel Opportunities

- **T002 / T003** 可并行（不同文件）
- **T020 / T022** 可并行（选择器与查询交互是两段独立 UI）
- **Phase 7（US5）与 Phase 3-6 完全独立**，可随时插入
- ★ **但 T004→T005→T006→T007→T008 是严格串行**，不能并行

---

## Implementation Strategy

### MVP First

1. Phase 1 + Phase 2 → 交付目录就绪
2. Phase 3（US1）→ **停下，独立验证，这就是 MVP**
3. ★ **此时用户要求的"必须"那件事已经交付**，即使仿真那一半卡住

### Incremental Delivery

1. US1 交付 → 三维起伏可见
2. US2 交付 → 车辆轨迹可见
3. US3 交付 → 数据沿路着色
4. US4 交付 → 视频可播可拖
5. US5 交付 → 带号假定可见

★ **每一步都不断掉前一步。** 这是 spec 的 `Independent Test` 在实现期的兑现。

---

## Notes

- `[P]` 任务 = 不同文件、无依赖
- **每条新检查必须配元测试**（宪法原则 V），且元测试要证明"违规时它真的会红"
- 既有守卫**复用不重建**：`test_console.py` 的 `PAGES` 一致性检查
  已经是"新增页面必须登记"的守卫，本 feature 只往里加一条，不新建重复守卫
- 提交信息要写"为什么"，尤其是"这里踩过什么坑、改动后靠什么防住"
