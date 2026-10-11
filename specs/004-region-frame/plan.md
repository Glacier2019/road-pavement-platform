# Implementation Plan: 区域帧 —— 桩号与真实地理坐标的互算与呈现

**Branch**: `004-region-frame` | **Date**: 2026-10-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/004-region-frame/spec.md`

## Summary

给平台补上**第二种定位语言**：桩号 ↔ 经纬度。

实测前提：全平台只有 `monitor_cross_section.lat/lon` 两列经纬度，而它们
**0/3 行有值**（全空）；`alignment_element` 有 33 段几何但**只有 x/y**。
所以这段换算**库里没有任何现成结果可取**，必须现场算出来。

技术路线由**三条既有事实**夹出来，不是自由选择：

1. **换算属于 M6（数据出口），不属于 M9。** 宪法 Principle II：
   M9 容器**不给 `PG_DSN`**。M9 只能用既有的只读代理 `/gw/{path}` 去取。
2. **线元恢复已有实现，实测残差 3.2×10⁻⁸ m。** `adapters/geom.point_at`
   （steps=64）对应论文 §2.2「桩号 → (x,y)」。**本 feature 在它之上加投影，
   不重写它** —— 它自己的注释写着「另起炉灶必然漂移」。
3. **页面落 M9，自包含 HTML，不用 CesiumJS**（既有契约测试挡着 CDN，
   且 5.8 km / 33 段的量级用不上地球引擎）。

★★ **本 feature 的实质是「加一层」**：
`几何（已验证）→ 投影（新）→ 经纬度（新）`。
第一层不动，只在上面接第二层；第三层是第二层的输出。

## Technical Context

**Language/Version**: Python 3.11（M6 服务端）；JavaScript ES2020（页面内联，无框架）

**Primary Dependencies**: FastAPI / Starlette（M6 已有）；
**无新第三方依赖** —— 投影按公式手写（见 research.md 决定 R1），
契约测试只用标准库 `math`

**Storage**: **不新增表、不改表结构。** 读 `alignment_element`（33 段）。
`geometry_point`（332 行派生缓存）**只作对照用**，不作数据源（理由见反空转一节）。
`crs.yaml` 是投影参数的唯一出处

**Testing**: `scaffold/tests/contract/test_region_frame.py`（**新增**，离线可跑）；
`run_contract_tests.sh` 总运行器；`test_console.py` 扩充（新页面登记）

**Target Platform**: Linux 容器（`rp-api` 与 `rp-console`，compose 服务段均已存在）

**Project Type**: Web 服务（M6 API 扩展）+ 静态页面（M9-console 扩展）

**Performance Goals**: 单点换算 ≤ 50 ms（含一次 DB 读）；
整条路线 33 段一次取回 ≤ 300 ms；页面打开到可见路线 ≤ 3 s

**Constraints**:
- M9 **不接触数据库**、**不写死端口**、**不引用外部 `script`/`link`**（三条既有测试强制）
- 投影参数**从 `crs.yaml` 读取**，代码里零硬编码（FR-005；宪法「配置全外置」）
- 搬进 M6 的 `geom.py` / `errors.py` 必须是**逐字节复制**，不得改写
- 换算必须能在**没有浏览器、没有地图服务**的环境里完整验证（SC-007）

**Scale/Scope**: 1 个新 M6 路由组（3 条）、1 个新 M6 模块文件（投影）、
1 处 M6 Dockerfile 改动、1 个新页面、1 处 M9 路由改动、
1 份新契约、1 个新契约测试文件

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 本 feature 的合规性 | 强制手段 |
|---|---|---|
| **I 契约先行** | 通过。**不新增 MQTT topic、不改 `wim_axle.v1`、不改表结构。** 新增的是 **M6 网关契约的扩展**（`m6-gateway.v0.4.yaml`），因为「桩号↔经纬度」是模块间新接口 | 契约须在 M6 与 M9 两侧都有测试 |
| **II 三条数据硬线** | 通过，且是**本 feature 最容易踩的一条**。写入：**无写入**。读：**只有 M6 读库**，且经 M3 的 `rpdao`；M9 **不读库**，只经 `/gw/` 只读代理取 M6 的结果 | `test_console.py` 既有 AST 断言 M9 无 `psycopg`；compose 不给 M9 `PG_DSN` |
| **III 证据优先级** | 通过。plan 与 research 中全部数字来自本次会话实测（往返 0.457 mm、包围盒 4.55×3.11 km、33 段、332 行），非引用 | 与本 plan 一并记录实测命令 |
| **IV 迁移不可变** | **不涉及**。无迁移、无 DDL 改动 | — |
| **V 反空转** | **本 feature 的头号风险面**，且**已经踩中过一次** —— 见下节 | 每条新检查配元测试 |
| **接入约束：五件套** | 通过。①compose 服务段已有，本次只改 Dockerfile 的 COPY②`/healthz`＋`/metrics` 已有③配置全外置（投影参数进 `crs.yaml`）④契约提交到 `contracts/`⑤契约测试可离线跑 | `run_contract_tests.sh` |
| **接入约束：新增页面必须登记进容器定义** | **必须做，且已有机器守卫**。`test_console.py` 的 `PAGES` 字典会红 | 既有元测试 |
| **未标定的阈值不得用于生产判定** | 通过。**本 feature 只换算，不判定。** 1 m 是**换算正确性**的判据，不是养护阈值 | 页面不得出现「合格/不合格」 |

### 无宪法违规

**Complexity Tracking 表为空。**

### 反空转（原则 V）的具体安排 —— 这里有一个真踩过的坑

★★★ **本 feature 的原始 SC-001 是「桩号→经纬度→桩号 往返闭合 ≤ 1 m」，
那是一条空转检查。** 正算与反算按构造互为逆运算 ——
**中央子午线取错一整带（差约 300 km）照样完美闭合**。
它量的是自己的逆运算，挡不住任何真实错误。已按用户在 2026-10-11 的裁决改写。

改成三条，且每条都配元测试：

| 检查 | 它在量什么 | 元测试（违规时必须红） |
|---|---|---|
| **SC-001** 与**独立估计**一致 | `point_at`（精确积分）在**非格点**桩号上的结果，与 `geometry_point` 两个相邻 20 m 格点的**线性插值**比，≤ 1 m | 把插值端点取错一格必须被认出 |
| **SC-001b** 往返闭合 | **只作必要条件**，不单独作判据 | 故意错 ±1 m 必须被认出 |
| **SC-002** 包围盒 | 33 段端点落点全在 25.16–25.21°N / 117.34–117.38°E | — |
| **SC-008** 对照 | ★ 把中央子午线**故意取错一整带**（117°E→114°E）时，**SC-002 必须失败** | 这条本身即元测试 |

★★ **为什么 SC-001 只能用线性插值当对照，不能直接拿 `geometry_point` 比：**
那张表**就是 `point_at` 自己写进去的** —— `geometry_solver.py` 委托
`adapters.geom` 求出每一行。在格点上比对 = **拿函数跟自己对**，**又是一条空转**。

只有在**非格点**上，「线性插值」与「精确积分」才是两个真正独立的估计。
二者之差有量级可算：R=255 m、20 m 弦的矢高
`255 − √(255² − 10²)` ≈ **0.196 m** —— 小于 1 m，检查会**通过但不空转**。

★ 另有一条**结构性**守卫（不属于 SC，属于"不许漂移"）：

| 守卫 | 元测试 |
|---|---|
| 搬进 M6 的 `geom.py` / `errors.py` 与 M2 原件**逐字节相同** | 改原件一个字节必须被认出 |

## Project Structure

### Documentation (this feature)

```text
specs/004-region-frame/
├── plan.md              # 本文件
├── research.md          # Phase 0：技术决定与理由
├── data-model.md        # Phase 1：换算的输入输出形状
├── quickstart.md        # Phase 1：可跑通的验证步骤
├── contracts/           # Phase 1：**只放指针**，正本在 scaffold/contracts/
│   └── README.md
├── checklists/
│   └── requirements.md  # 已存在
└── tasks.md             # Phase 2（/speckit-tasks 产出，不由本命令创建）
```

### Source Code (repository root)

```text
scaffold/
├── modules/M6-api/
│   ├── app.py                   # 加 3 条 /v1/geometry/…/geo 路由
│   ├── geo.py                   # 新增：高斯-克吕格正算 / 逆算（参数从 crs.yaml 读）
│   ├── m2geom/                  # 新增：从 M2 逐字节搬来的线元求值
│   │   ├── errors.py            #   COPY 自 modules/M2-ingest/adapters/errors.py
│   │   └── geom.py              #   COPY 自 modules/M2-ingest/adapters/geom.py
│   ├── Dockerfile               # 加：搬两文件 + 生成 __init__.py + COPY crs.yaml
│   └── requirements.txt         # 不动（投影手写，无新依赖）
├── modules/M9-console/
│   ├── app.py                   # 加 GET /geo
│   ├── geo.html                 # 新增：自包含页面（内联 JS，零依赖）
│   ├── index.html               # 加入口
│   ├── Dockerfile               # 加 COPY geo.html
│   └── config/crs.yaml          # 不动（M6 改为 COPY 读同一份，见决定 R4）
├── contracts/openapi/
│   └── m6-gateway.geo.v0.1.yaml # ★ 新增增量契约。**不就地改 v0.3**（宪法 I）
├── docker-compose.skeleton.yml  # 不动（M6 与 M9 均已有服务段）
└── tests/contract/
    ├── test_region_frame.py     # 新增：本 feature 的全部检查与元测试
    └── test_console.py          # 扩充：PAGES 加 geo.html
```

**Structure Decision**: **M6 加能力、M9 加页面**，不新建模块、不新建容器。

- **换算落 M6**：它是数据出口，是唯一既能读库又能对外服务的地方。
- **页面落 M9**：它是既有的页面宿主（已有 5 个页面），且自带只读代理 `/gw/`。
- **M8-apps 不动**：那是骨架，接口返回 501（已在 spec 的已澄清第 2 条记录）。
- ★ **`crs.yaml` 暂不搬家**：它现在住在 `modules/M9-console/config/`，
  但 M6 现在也要读它。**本次按既有先例处理** —— M6 的 Dockerfile 已经
  COPY 过 `modules/M9-console/config/modules.yaml`，同样再 COPY 一份 `crs.yaml`。
  **搬家的诱惑要忍住**：那是 003 已交付的产物，动它就得同步改 003 的文档与测试。
  见 research.md 决定 R4 —— 记录了这笔技术债与还债时机。

## Phase 0 / Phase 1 产出

见 [research.md](./research.md)、[data-model.md](./data-model.md)、
[contracts/](./contracts/)、[quickstart.md](./quickstart.md)。
