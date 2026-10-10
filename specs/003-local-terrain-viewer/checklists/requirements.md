# Specification Quality Checklist: 局部帧三维起伏查看器

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-10-10
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- 本 spec 的全部数字均来自**实测**，非推断或引用：
  CRG u 0.000~5805.400 m / v −4.2500~4.2500 m / 0.1 m 步长 / 86 个 v 通道 /
  高程 54.0641~83.8141 m；线形 33 单元（9 直线 + 16 缓和 + 8 圆曲线，
  半径 255~500 m）；起终点高差 29.75 m；平面外接矩形 4521×3120 m；
  轮荷 CSV 21 列且**无 x/y/heading**。
- **本 spec 不触碰任何已冻结的契约**：不新增 MQTT topic，不改
  `wim_axle.v1`，不改既有表结构。因此**不需要走工单**，
  这一点与 002 不同（002 的 FR-001 动 `station_sequence` 外键方向）。
- **范围刻意的排除**：区域帧（路网 / 病害分布 / DEM / 影像底图）
  **不在本 spec 内**。理由是它的每一行设计都依赖带号，而带号目前是**推断值**。
  把它写进来等于在假定的地基上盖房子。
- 未标 [NEEDS CLARIFICATION]：FR-016~018 要求的坐标基准配置，
  其默认值（39 带 / 117°E / CGCS2000）来自本次会话的三条独立证据
  （Y 东偏 34.6~37.7 km 且无带号前缀；39 带落点为闽中山区而 40 带落点在
  台湾海峡；投影变形 14.75~17.51 mm/km 满足规范 25 mm/km 上限）。
  **该值以 `assumed: true` 记录**，确认后改为配置即可，不阻塞规划。
- **US1 可独立交付**：不依赖仿真、不依赖带号、不依赖任何外部服务，
  是三个 P1/P2 故事里唯一单独就能产生价值的。
