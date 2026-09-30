# Project Chrono 替代 TruckSim —— 可行性评估

| 项 | 内容 |
|---|---|
| 日期 | 2026-09-20 |
| 结论 | ✅ **建议采用 Chrono**，但**不是「替代 TruckSim」，而是「它本来就更合适」** |
| 影响的既有条目 | FR-018（spec.md L305）＋ `scaffold/` 内 **4 处**「供 TruckSim / FEM 消费」（实测：`app.py` 2、`repo.py` 1、`m6-gateway.v0.3.yaml` 1）＋ M8 应用层 |
| 是否需立工单 | **需要**（改 FR-018 的消费方声明 + M8 定位），但**不动 DDL** |

---

## 0. 结论先行

**TruckSim 和 Chrono 不是同一类东西，换掉它们不是「替代」而是「换赛道」。**

| | TruckSim | Project Chrono |
|---|---|---|
| 定位 | **商业车辆动力学专用工具**（整车操稳/制动/平顺性） | **开源多体物理引擎**（Chrono::Vehicle 是其一部分） |
| 授权 | 商业许可，按席位计费 | **BSD-3**，可商用、可改、可分发 |
| 路面 | 内置 ISO 8608 等标准路面谱，**面向车辆测试** | `ChTerrain` 抽象基类，**用户自己实现**「某点的地面高度/法向/摩擦系数」 |
| 车-路耦合 | 路面是**输入**，不参与力学 | 路面是**仿真的一部分**，可与车辆双向耦合 |
| 典型产出 | 车辆响应（侧向加速度、轮荷、制动距离） | 车辆响应**＋路面响应**（接触力、车辙、变形） |

**关键差异在最后一行。**

TruckSim 的问题是：**它把路面当边界条件**。你给它一条路面谱，它算车怎么动，
路面自己不动、不变形、不响应。而本项目叫「**车路之间**动力学关系」——
如果路面只是输入的几行数字，那研究的是「车对路的响应」，不是「车路耦合」。

Chrono 的 `ChTerrain` 恰好是**为这件事留的口子**：

> A terrain object in Chrono::Vehicle must provide methods to:
> - return the terrain height at the point directly below a specified location
> - return the terrain normal at the point directly below a specified location
> - return the terrain coefficient of friction at the point directly below a specified location
（[Terrain models](https://api.projectchrono.org/vehicle_terrain.html)）

而 `ChTerrain::FrictionFunctor` 更直接：

> provides an interface for specification of a position-dependent coefficient of friction.
> The user must implement a custom class derived from this base class…

**这正是本项目已经有的东西。** FR-018 落地的两个接口
（`/v1/geometry/sections/{id}/elements` 与 `/samples`）给出的就是
「按桩号区间的 κ(s)、坡度、超高」—— 把它接成 `ChTerrain` 的实现，
路面就从「谱」变成了「你自己库里那条真实路线」。

---

## 1. 与本仓库的契合点（逐条对现有能力）

| Chrono 需要 | 本仓库已有什么 | 状态 |
|---|---|---|
| 路面几何（高程/法向） | `alignment_element` 33 行 κ(s) + `profile_grade_point` 12 行坡度 | ✅ 已入库 |
| 逐桩采样 | `geometry_point` 332 行（US4 派生缓存，可重建） | ✅ 已入库 |
| 横断面（做三维路面用） | `cross_section_ground_point` **2215** 行 | ✅ 已入库 |
| 按区间取数 | **FR-018 的两个 M6 接口**（T033/T034 已落地） | ✅ 已实现 |
| 摩擦系数 | ❌ 无 | ⚠️ 待定 |
| 车辆/轮胎参数 | ❌ 无（不属于本平台范围） | ⚠️ 外购/文献 |

**前四项是现成的**，而且 FR-018 那两个接口**本来就是为这件事建的** ——
`app.py` 里两处 docstring 写的正是「供 TruckSim / FEM」。
换成 Chrono 后**接口一行都不用改**，只改注释里的名字。

---

## 2. ★ 必须说清楚的三条限制

### 2.1 半经验轮胎模型**只适合平路**

Chrono 文档原话：

> Handling tire models are designed for **flat road surfaces** and they normally use
> single point contact or four point contact (TMeasy). For ride tests on undulated
> roads or for obstacle crossing a special contact algorithm called "envelope" has been implemented.

即：Pacejka / TMeasy / Fiala 这些「操作稳定性」模型是为平路设计的；
走起伏路面要用 envelope 接触算法，**或者**换刚体轮胎 / FEA 轮胎。

**对本项目的含义**：做「车路耦合」时，如果路面是**变形的**（SCM/颗粒/FEA），
半经验轮胎**根本不能用**，必须换刚体或 FEA 轮胎。
这是一条硬约束，选型时不能忽略。

### 2.2 摩擦系数没有来源

`ChTerrain` 要求提供摩擦系数，而**本仓库没有这个数据**。三个选项：

| 方案 | 说明 | 评价 |
|---|---|---|
| 常数 | 全路段取 0.8 | 最简单，但「车路关系」里最敏感的参数被拍平了 |
| 分路段常数 | 按 `pavement_type` 给不同值 | 与现有表结构对得上，**推荐起步** |
| 实测/反算 | 由抗滑检测或 WIM 数据反算 | 最真，但依赖 M1 域的抗滑数据（**库里暂无**） |

★ 这一条与 `config/apps.yaml` 现有纪律**一致**：

> 待设计院输入。骨架期留 null——凭空生成结构参数只会得到
> 「看起来合理但无依据」的结果。

**摩擦系数同样不应凭空生成。** 建议 `calibrated: false` + 常数起步，
在配置里显式标注它不是标定值。

### 2.3 Chrono 不是「装上就能用」

它是 C++ 库，需要：编译环境、场景搭建、参数标定、结果验证。
**没有 GUI 一键出图**。相比 TruckSim 的成熟界面，学习和调试成本明显更高。
但它换来的是**可编程、可耦合、可复现** —— 对本项目（要接自己的库）是净收益。

---

## 3. 放在哪个模块

| 选项 | 评价 |
|---|---|
| **M8 应用层**（推荐） | 它的定位就是「FEM／承载力／养护决策／孪生」。车路动力学是**应用**，不是数据管道。`config/apps.yaml` 里已有 `fem:` 段，可并列加 `vehicle_road:` |
| 新模块 M11 | ❌ 不建议。会打破 10 模块的既定分组（A/B/C/D 组），且它与 M8 同属「应用决策」 |
| M5 融合层 | ❌ 不是它的职责（M5 出诊断三元组） |

**建议**：M8 下新增一个子能力，配置加在 `config/apps.yaml`：

```yaml
vehicle_road:
  engine: null            # 待定：projectchrono（评估中）
  tire_model: null        # 待定：Fiala / TMeasy / Pac89 / Pac02
  terrain_source: null    # 待定：接 M6 FR-018 接口
  friction: null          # 无实测来源，见评估 §2.2
  calibrated: false
```

★ 与既有 `fem:` 段**同一个写法**：留 null、标 `calibrated: false`、
写明「待定」。这样骨架期不撒谎，各组按契约填空。

---

## 4. 建议的推进路径（三步，先窄后宽）

| 步 | 内容 | 产出 | 风险 |
|---|---|---|---|
| **1. 打通**（最小） | 取毕设路段 → 生成 Chrono 路面（用现有 κ(s)/坡度）→ 单辆车匀速通过 → 出轮荷/侧向力 | 证明「库里的线形能驱动 Chrono」 | 低 |
| **2. 接真数据** | 用 `cross_section_ground_point`（2215 行）建**三维**路面；接 WIM 的轴载/车速 | 车-路耦合的真实输入 | 中（数据清洗） |
| **3. 做耦合** | 路面由刚体→可变形（SCM）；此时**轮胎必须换成刚体/FEA**（§2.1） | 真正的「车路之间」 | 高（标定 + 验证） |

**第 1 步的价值**：它**不需要任何新数据**，只用库里已有的 33+12+332 行。
做完就能回答「这条路对车的响应是什么样」，而且是**基于真实路线**而不是标准谱。

★ **第 1 步的可行性已验证**：见
[`验证-路线线形0.1m分辨率可生成性.md`](验证-路线线形0.1m分辨率可生成性.md) ——
33 段线形全部解析可积，0.1 m 采样全路线 **58,055 点**，最大残差 **6.92e-04 m**（亚毫米）。
该验证同时定位了**缓和曲线的曲率模型坑**（`Δaz = L/(2R)`、半径列无方向且只填一端，
误用方位角线性近似会产生 **2.31 m** 且**不随步长收敛**的误差），实施第 1 步前必须先读。

---

## 5. 顺带发现的一处过期文档

`specs/002-station-baseline-geometry/spec.md` 第 329 行仍写着：

```
| **FR-018 按区间查询** | ❌ **缺口** | 索引 idx_alignment_elem_station 已建，但查询接口未落地 |
```

而 T033/T034 **已经把它落地了**（`elements_by_station` / `samples_by_station` +
两个 M6 路由 + 契约 `桩号区间查询` 18/18 绿）。

**这是一处会说反话的文档** —— 它会让读的人以为还要做一遍。
建议随本评估一并更正（另外第 328 行的 FR-014 也需复核）。

---

## 6. 需要裁决

| # | 问题 | 建议 |
|---|---|---|
| **Q1** | 是否确定用 Chrono（而非保留 TruckSim 或两者并行）？ | ✅ 建议**只用 Chrono**。TruckSim 的「路面=输入」模型与「车路耦合」这个目标先天不合 |
| **Q2** | 放 M8 还是新模块？ | ✅ **M8**，加 `vehicle_road:` 配置段 |
| **Q3** | 摩擦系数来源？ | ✅ **分路段常数起步**（按 `pavement_type`），标 `calibrated: false`，不凭空造 |
| **Q4** | 要不要立即改 FR-018 的措辞（`TruckSim / FEM` → `Chrono / FEM`）？ | ✅ **要**，且**同时更正 spec.md L329 那处过期状态** —— 两件事同属「FR-018 的口径」，分两次改动会重复解释 |
| **Q5** | 先做哪一步？ | ✅ **第 1 步（打通）**。零新数据、可独立验证，且能立刻证伪「库里数据够不够用」 |

---

## 附：参考

- [Project Chrono 官网](https://projectchrono.org/)
- [轮胎模型](https://api.projectchrono.org/wheeled_tire.html) — 刚性 / 半经验（Pacejka 89、Pacejka 2002、TMeasy、Fiala）/ FEA
- [地形模型](https://api.projectchrono.org/vehicle_terrain.html) — Flat / Rigid / CRG / SCM / DEM / FEA / CRM
- [FAQ：BSD-3 授权](https://projectchrono.org/faq/)