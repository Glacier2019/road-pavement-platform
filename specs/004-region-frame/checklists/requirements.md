# Specification Quality Checklist: 区域帧 —— 桩号与真实地理坐标的互算与呈现

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-10-11
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed
- [ ] **No implementation details** —— ★ **部分不满足，故意的，在此说明**：
      spec 里出现了 `EPSG:4548`、`M6 API`、`CesiumJS`、`crs.yaml`。
      前两个是**领域事实**不是实现选择（EPSG 编码是坐标基准的名字，
      宪法 Principle II 决定了几何从哪出）；后两个只在「待澄清」里
      **作为待决问题**出现，不是在规定做法。
      ★ 不勾这一条，是因为勾上就等于说自己做到了，而没做到。

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain —— 三个待澄清问题
      都**不影响现在开工**，故未标 [NEEDS CLARIFICATION]，而是集中列在
      「待澄清」一节，供 plan 阶段决策。
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic —— SC-001~007 全部是
      误差、范围、比例、报错率，没有一条依赖具体框架。
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification —— 见上，同样**部分不满足**

## Notes

### 本 spec 的每一个数字都是本次实测，不是引用

| 数字 | 怎么来的 |
|---|---|
| 25.16789–25.20878°N / 117.34292–117.37376°E | 33 段线形 **66 个端点**逐个逆算 |
| 包围盒 4.552 × 3.106 km | 上一条的纬度/经度跨度换算 |
| 往返偏差 0.457 mm | 自写的正算+逆算，5 个点跨越 116.5–120.34°E |
| 东偏 +34.55 km | 逆算经度与 CM 117°E 之差换算；`crs.yaml` evidence 独立写的是 +34.6 km |
| `disease_record=0` / `media_file=0` | `select count(*)` |
| `monitor_cross_section` 经纬度 0/3 行 | `count(*) filter (where lat is not null)` |
| 瓦片预算 z=13 需 2×3、z=16 需 7×10 | 按 256 px 瓦片重算 |

★ **一处自纠**：瓦片预算第一次算成「10 km 需 148041 块」——
把"每像素多少米"当成了"每瓦片多少米"，漏了 ×256。
一个数字离谱到刺眼，就该回去看公式，而不是把它写进文档。

### ★★★ 本 spec 最重要的一条范围判断

`specs/003-…/research.md` 把区域帧记为「路网、病害分布、DEM、影像底图」。
本次查实库后发现：**`disease_record` 0 行、`media_file` 0 行**。

给两张空表写界面，做出来的是一个**永远空的图层** ——
它看起来像 bug，实际是没数据，而且**没有任何东西会报错**。
所以本 spec 把这两项**显式排除**，各附理由，等有数据再各开一个 spec。
这不是缩范围偷懒，是拒绝在一开始就注定做不出东西的地方盖房子。

### 本 spec 不动任何契约

不新增 MQTT topic、不改 `wim_axle.v1`、不改既有表结构。
换算能力是**新增的一个读取侧能力**，从 `crs.yaml` 与 `alignment_element` 取数。
因此**不需要走契约工单**。

但有一条要注意：FR-005 要求带号**从 `crs.yaml` 读**，不得写死 ——
这与 003 的 FR-016~018 是同一条纪律，且有机器检查
（`tests/contract/test_sim_track.py` 的 US5 组断言 `filled != assumed` 这个**关系**）。

### 未标 [NEEDS CLARIFICATION] 的判断

「待澄清」里的三个问题（底图用什么、前端选型、1 m 精度是否合理）
**都不影响 US1 开工** —— 而 US1 是本 spec 的全部前提，
且它**不需要任何前端**。把这三个问题标成 [NEEDS CLARIFICATION]
会让整份 spec 卡住，而它们实际只卡住 US2/US3。
