# Specification Quality Checklist: 空表缺口盘点与接入优先级

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-09-25
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

全部通过。无遗留待澄清项。

**关键事实已实测复核**（不是从文档抄的，见宪法原则 III）：

- 62 逻辑表 / 36 有数据 / 26 空 / 39 月分区 —— 与实库逐张 count 一致。
- 五个土方段（earthwork_transfer / borrow_pit / spoil_pit / earthwork_haul_stat /
  earthwork_fill_stat）的适配器**确在** `weidi/__init__.py` 的 `IMPLEMENTED` 里，
  `.tsftxt` 源文件**确在** `docpipe/materials/纬地工程项目文件/` 里，
  而实库这五张表**确为 0 行**。SC-002 就是钉这一条的。
- `design_control` 不是独立物理表：它是**段名**，数据落进 `design_control_text`
  等 9 张表。核对时一度以为"表不存在＝bug"，实为已登记的"段名 ≠ 表名"例外 ——
  这正是把 Edge Case「同一段名对应多张表」写进规格的原因。

**一处需在实现阶段留意的措辞**：FR-007 写了"/gw 读取"，但规格应当尽量少提实现。
保留它是因为硬线（M9 不得直连库）**本身就是需求**，不是实现选择 ——
它决定了这个特性能不能被别的组独立复现。
