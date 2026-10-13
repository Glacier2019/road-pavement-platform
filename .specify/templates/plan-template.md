# Implementation Plan: [FEATURE]

**Branch**: `[###-feature-name]` | **Date**: [DATE] | **Spec**: [link]

**Input**: Feature specification from `/specs/[###-feature-name]/spec.md`

**Note**: This template is filled in by the `/speckit-plan` command; its definition describes the execution workflow.

## Summary

[Extract from feature spec: primary requirement + technical approach from research]

## Technical Context

<!--
  ACTION REQUIRED: Replace the content in this section with the technical details
  for the project. The structure here is presented in advisory capacity to guide
  the iteration process.
-->

**Language/Version**: [e.g., Python 3.11, Swift 5.9, Rust 1.75 or NEEDS CLARIFICATION]

**Primary Dependencies**: [e.g., FastAPI, UIKit, LLVM or NEEDS CLARIFICATION]

**Storage**: [if applicable, e.g., PostgreSQL, CoreData, files or N/A]

**Testing**: [e.g., pytest, XCTest, cargo test or NEEDS CLARIFICATION]

**Target Platform**: [e.g., Linux server, iOS 15+, WASM or NEEDS CLARIFICATION]

**Project Type**: [e.g., library/cli/web-service/mobile-app/compiler/desktop-app or NEEDS CLARIFICATION]

**Performance Goals**: [domain-specific, e.g., 1000 req/s, 10k lines/sec, 60 fps or NEEDS CLARIFICATION]

**Constraints**: [domain-specific, e.g., <200ms p95, <100MB memory, offline-capable or NEEDS CLARIFICATION]

**Scale/Scope**: [domain-specific, e.g., 10k users, 1M LOC, 50 screens or NEEDS CLARIFICATION]

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

<!--
  ACTION REQUIRED: 逐条填写。**六条原则一行都不能删** —— 不适用的写「不适用」并说明理由，
  不要删行。删行会让「这条原则被考虑过了」这件事消失，而它不会报错。
  ★ 本表的行由 `test_spec_kit_consistency.py` 对着宪法核验：
    宪法加一条原则而这里没加行 → 套件变红。
  ★ 「机器怎么验」那一列**不许空着**：宪法原则 V 与 VI① 都要求
    「声明了没断言 = 没声明」。填不出断言时，就在那一格里写明为什么不需要。
-->

| 原则 | 本 feature 怎么满足 | 机器怎么验 |
|---|---|---|
| **I. 契约先行** | [契约变更走工单 + 版本号递增 + 兼容性声明；两侧都测] | [哪条契约测试] |
| **II. 三条数据硬线** | [写入只经 M2 / 读只经 M3 / 跨模块只认契约；目标是「结构上做不到」] | [哪条断言] |
| **III. 证据优先级** | [实库 > DDL 文件 > 数据字典/catalog > 图件；冲突时以实库为准] | [实测记录在哪] |
| **IV. 迁移不可变** | [加列走新迁移，不就地改 `*migrate*.sql`；DDL 同步] | [`test_ddl_migration_parity.py`] |
| **V. 反空转** | [每条新增检查都配元测试：故意造失败，断言必须变红] | [元测试名] |
| **VI. 通用商业化优先** | [① 无硬编码平台级标识，每个「这是配置项」的声明都配断言 ② 国际标准标识（EPSG/SRID/ISO）为权威，本地记法可空且缺失不报错 ③ 精度类判据必须有**非同源**参照系：自证不算证据 ④ **互相印证不算证据**：两份记录一致不等于已核对，须说明产出路径为何不同，否则补一条对外部标尺（实库/源文件/DDL）的检查] | [哪条断言 / 哪个外部标尺] |
| **接入约束：配置全外置** | [带号、阈值、路径等一律来自配置或库，不进代码常量] | [那类守卫] |
| **接入约束：容器定义同步** | [compose 与 Dockerfile 的 COPY 关系对齐；手维护的文件清单要配覆盖断言] | [容器断言 + 重建后探针] |

**Gate 结论**：[通过 / 有破例（逐条写进下面的 Complexity Tracking）]

## Project Structure

### Documentation (this feature)

```text
specs/[###-feature]/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)
<!--
  ACTION REQUIRED: Replace the placeholder tree below with the concrete layout
  for this feature. Delete unused options and expand the chosen structure with
  real paths (e.g., apps/admin, packages/something). The delivered plan must
  not include Option labels.
-->

```text
# [REMOVE IF UNUSED] Option 1: Single project (DEFAULT)
src/
├── models/
├── services/
├── cli/
└── lib/

tests/
├── contract/
├── integration/
└── unit/

# [REMOVE IF UNUSED] Option 2: Web application (when "frontend" + "backend" detected)
backend/
├── src/
│   ├── models/
│   ├── services/
│   └── api/
└── tests/

frontend/
├── src/
│   ├── components/
│   ├── pages/
│   └── services/
└── tests/

# [REMOVE IF UNUSED] Option 3: Mobile + API (when "iOS/Android" detected)
api/
└── [same as backend above]

ios/ or android/
└── [platform-specific structure: feature modules, UI flows, platform tests]
```

**Structure Decision**: [Document the selected structure and reference the real
directories captured above]

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| [e.g., 4th project] | [current need] | [why 3 projects insufficient] |
| [e.g., Repository pattern] | [specific problem] | [why direct DB access insufficient] |
