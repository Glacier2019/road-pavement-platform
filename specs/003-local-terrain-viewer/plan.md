# Implementation Plan: 局部帧三维起伏查看器

**Branch**: `003-local-terrain-viewer` | **Date**: 2026-10-10 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/003-local-terrain-viewer/spec.md`

## Summary

在 M9 平台管理台新增一个只读页面，用浏览器原生能力渲染这条路本身的三维起伏，
并把仿真产物（视频、轨迹、图）从一个固定的交付目录提供出来。

技术路线由**两条既有约束**夹出来，不是自由选择：

1. `test_console.py` 已经断言每个 M9 页面**不得引入外部 `script`/`link`（`https?://`）**
   —— CDN 版 three.js / CesiumJS 直接被既有测试判红。
2. 宪法「接入约束」要求**自包含 HTML，无构建步骤、无新依赖**，且
   **新增页面/资产必须同时登记进容器定义**（已有元测试守着）。

因此：**内联手写 WebGL，零依赖**。路面高程是规则网格 —— 这是最容易手写的几何形态。

★★ **101 MB 的 CRG 不能发给浏览器**（实测 `route_0p1m.crg` = 101,077,006 B）。
所以必须先由一个离线脚本把它**抽稀**成紧凑的二进制网格放进交付目录。
抽稀后的量级：2 m 步长 × 9 个横向样点 = **26,127 顶点 / 约 52,000 三角面 / 约 313 KB**。
**约 320 倍压缩。**

## Technical Context

**Language/Version**: Python 3.11（M9 服务端 + 离线抽稀脚本）；JavaScript ES2020（页面内联，无框架）

**Primary Dependencies**: FastAPI / Starlette（M9 已有）；**页面零新依赖**（无 three.js、无构建工具）

**Storage**: 交付目录（仓库内固定路径，只读挂载）。**本 feature 不新增数据库表，
不读数据库** —— 与 M9 既有契约一致（M9 容器不给 `PG_DSN`）

**Testing**: `scaffold/tests/contract/test_console.py`（既有，须扩充）；`run_contract_tests.sh` 总运行器

**Target Platform**: Linux 容器（`rp-console`，profile `m9`）；浏览器为客户端

**Project Type**: Web 服务 + 静态页面（M9-console 模块内扩展）

**Performance Goals**: 打开页面到可见路面 ≤ 3 s；交互 ≥ 30 fps（中端笔记本，软件渲染不可作为基线）

**Constraints**:
- 页面自包含：内联 JS + CSS，**无外部 `script`/`link`**（既有测试强制）
- 页面**不写死任何端口**（既有测试强制）
- 交付目录**只读**，零写入
- 页面**不接触数据库**

**Scale/Scope**: 1 个新页面（`sim.html`）、1 个新路由（`GET /sim`）、
2 个新静态路由（网格二进制、仿真视频）、1 个离线抽稀脚本、
1 份新契约（产物清单）、1 处 compose 改动（只读挂载）、1 处 C++ 改动（CSV 补三列）

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 本 feature 的合规性 | 强制手段 |
|---|---|---|
| **I 契约先行** | 通过。**不新增 MQTT topic、不改 `wim_axle.v1`、不改表结构、不改 M6 openapi。** 新增一份**产物清单契约**（`contracts/delivery/`），因为 M9 读交付目录是模块间新接口 | 新契约须有 schema + 两侧测试 |
| **II 三条数据硬线** | 通过，且是**加强**。写入：本 feature 无写入。读：**不读数据库**（读的是文件产物），因此比"读只经 M3"更严 —— 它根本不进数据链路 | `test_console.py` 既有 AST 断言 M9 无 `psycopg`；compose 无 `PG_DSN` |
| **III 证据优先级** | 通过。plan 中全部数字来自本次会话实测（CRG 尺寸、行数、顶点数），非引用 | 与本 plan 一并记录实测命令 |
| **IV 迁移不可变** | **不涉及**。无迁移、无 DDL 改动 | — |
| **V 反空转** | **本 feature 的主要风险面**。见下节 | 每条新检查配元测试 |
| **接入约束：五件套** | 通过。①compose 服务段已有，本次**加只读挂载**②`/healthz`＋`/metrics` 已有③配置全外置（带号进 `config/` + 环境变量）④契约提交到 `contracts/`⑤契约测试可离线跑 | `run_contract_tests.sh` |
| **接入约束：新增页面必须登记进容器定义** | **必须做，且已有机器守卫**。`test_console.py` 的 `PAGES` 字典 + Dockerfile COPY 清单**会红** | 既有元测试两条 |
| **未标定的阈值不得用于生产判定** | 通过。**本页面只显示，不判定。** 任何阈值只作色标参考，标 `calibrated: false`，不产出结论 | 页面不得出现"合格/不合格"字样 |

### 无宪法违规

**Complexity Tracking 表为空** —— 本 feature 没有需要辩护的违规。

### 反空转（原则 V）的具体安排

本 feature 新增的每一条检查都配元测试，且**至少有一条是"这条守卫本身曾经缺位"的诚实记录**：

| 新检查 | 元测试（违规时必须红） |
|---|---|
| `sim.html` 在内联 JS 里不引用外部资源 | 塞一行 `<script src="https://cdn…">` 必须被认出 |
| 网格二进制大小在上限内 | 假装一个 5 MB 的文件必须被认出 |
| 交付目录只读（compose 无 `:rw`、无默认读写挂载） | 把 `:ro` 去掉必须被认出 |
| 产物清单与目录实际内容一致 | 藏一个未登记的文件必须被认出 |
| CSV 含 `x_m/y_m/heading_deg` | 删掉某一列必须被认出 |
| 页面不含"合格/不合格"判定字样 | 塞一句"判定为合格"必须被认出 |

★ **注意**：`test_console.py` 已有的 `PAGES` 一致性检查**本身就是"新增页面必须登记"的守卫**，
本次只需把 `sim.html` 加进那个字典 —— **不新建重复的守卫**。

## Project Structure

### Documentation (this feature)

```text
specs/003-local-terrain-viewer/
├── plan.md              # 本文件
├── research.md          # Phase 0：技术决定与理由
├── data-model.md        # Phase 1：产物与网格的数据形状
├── quickstart.md        # Phase 1：可跑通的验证步骤
├── contracts/           # Phase 1：新增契约
│   └── artifact_manifest.v0.1.schema.json
└── tasks.md             # Phase 2（/speckit-tasks 产出，不由本命令创建）
```

### Source Code (repository root)

```text
scaffold/
├── modules/M9-console/
│   ├── app.py                    # 加 GET /sim、GET /artifacts/{path}（只读白名单）
│   ├── sim.html                  # 新增：自包含三维起伏查看器（内联 WebGL）
│   ├── index.html                # 加一个指向 /sim 的入口
│   ├── Dockerfile                # 加 COPY sim.html
│   └── config/
│       └── crs.yaml              # 新增：带号 / 中央子午线 / 椭球 / assumed 标记
├── modules/M8-apps/              # 不动（CesiumJS 留给区域帧）
├── contracts/
│   └── delivery/
│       └── artifact_manifest.v0.1.schema.json   # 新增契约
├── simulator/crg/
│   ├── 05_vehicle_on_crg.cpp     # WheelLoadCsv::Write() 补 x_m, y_m, heading_deg
│   └── export_viewer_mesh.py     # 新增：CRG → 抽稀二进制网格（离线，写交付目录）
├── tools/                        # 见下方"交付目录"决定
├── docker-compose.skeleton.yml   # console 服务段加只读挂载
└── tests/contract/
    └── test_console.py           # 扩充：PAGES 加 sim.html + 本 feature 的新检查与元测试

artifacts/sim/                    # 交付目录（仓库内固定路径）
├── manifest.json                 # 契约实例
├── terrain_mesh.bin              # 抽稀网格（约 313 KB）
├── vehicle_on_crg_lane_right_10x.mp4
├── vehicle_on_crg_lane_right_realtime.mp4
└── wheel_loads.csv               # 补过三列的轨迹
```

**Structure Decision**: 在 **M9-console 模块内扩展**，不新建模块、不新建容器。
理由：M9 已经是平台集成面，已有 4 个页面与路由模式；本 feature 是第 5 个页面。
**M8-apps 不动** —— CesiumJS 是区域帧的事，本 feature 刻意排除区域帧。

## Phase 0 / Phase 1 产出

见 [research.md](./research.md)、[data-model.md](./data-model.md)、
[contracts/](./contracts/)、[quickstart.md](./quickstart.md)。
