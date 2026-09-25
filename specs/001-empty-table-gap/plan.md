# Implementation Plan: 空表缺口盘点与接入优先级

**Branch**: `001-empty-table-gap` | **Date**: 2026-09-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-empty-table-gap/spec.md`

## Summary

给 26 张空表做**归因**（为什么空）＋ **归属**（该谁接）＋ **排序**（先动哪条），
并把结果经 M6 出口暴露到 M9 表盘点页。

技术路线的核心判断：**归因所需的三类事实分散在三处**，必须各归其位、不跨界取：

| 事实 | 唯一持有者 | 为什么不能换人 |
|---|---|---|
| 表里有几行 | **M3**（唯一接触存储的模块） | 宪法原则 II「读只经 M3」 |
| 表的归属模块 | **M3** `TABLE_OWNER` | 已在 M3，抄一份就会漂 |
| 适配器实现了哪些段 | **M2**（解析器在它肚子里） | M3 不许 import M2（宪法原则 II 第 3 条） |
| 源文件在不在磁盘上 | **M2**（只有它有导入目录） | 同上；M3/M6 都不该碰文件系统 |

**因此归因不能由一个模块独立算出** —— 它必须是一次**跨模块拼装**：
M3 出「表 → 行数 ＋ 归属模块」，M2 出「段 → 实现了没 ＋ 源在不在」，
M6 把两边按「段 ↔ 表」的映射拼起来，算出空因。

★ 这个结构是**被硬线逼出来的**，不是设计偏好。若让 M3 直接读磁盘、或让 M6 直接查库，
都能更快写完，但都会破坏"平台内唯一接触存储处"这条线 —— 而那条线正是本平台
能让四组学生互不踩脚的前提。

## Technical Context

**Language/Version**: Python 3.13（容器内），项目既有约定，不引入新版本
**Primary Dependencies**: FastAPI（M2/M6/M9 既有）、psycopg3（仅 M3 持有）、pyyaml（读模块登记表）
**Storage**: PostgreSQL 16（读取一律经 M3 `rpdao`）；**本特性不改任何表结构** —— 无 DDL、无 migration
**Testing**: 项目自研契约测试框架（`scaffold/tests/contract/*.py`，可离线跑）＋ 总运行器 `run_contract_tests.sh`
**Target Platform**: Linux 容器（docker compose 骨架栈，7 个服务）
**Project Type**: 多模块服务化平台（M1–M10），本特性落在 M2 / M3 / M6 / M9 四个既有模块内
**Performance Goals**: `/tables` 页 3 秒内可读（SC-006）。归因需一次 `UNION ALL` 盘点
（62 表，实测亚秒级）＋ 一次文件系统 `stat` 扫描（小目录）
**Constraints**: M9 必须无 PG_DSN、无 psycopg；`/gw` 仍限 `v1/` 前缀且只许 GET；
**确定性**：同一批输入必须给出同一次序（排序需稳定，同分有决胜规则）
**Scale/Scope**: 62 张逻辑表 / 26 张空表 / 5 类空因 / 1 个新端点 / 1 个页面改版

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 本特性如何满足 | 判定 |
|---|---|---|
| **I 契约先行** | 新增 `/v1/catalog/gaps` 必须**同时**登记进 `contracts/openapi/m6-gateway.v0.3.yaml`，否则 `m6-路由` 测试报「实现缺契约」；空因枚举作为契约的一部分冻结 | ✅ |
| **II 三条数据硬线** | 行数只经 M3；归属取自 M3 `TABLE_OWNER`；段/源信息只经 M2；M6 拼装但**不持连接池**；M9 经 `/gw` 取数、容器无 DSN | ✅ |
| **III 证据优先级** | 空因一律以**实库 `count(*)`** 为准；文档说"已接入"不作数（FR-009）。实现中若文档与实库冲突，修文档 | ✅ |
| **IV 迁移不可变** | **零 schema 变更**，不新增迁移。若实现中发现需要加表/列，须退回重新设计（见下方"若需要改 schema"） | ✅ |
| **V 反空转** | 每条空因判定配元测试；SC-002 是可证伪的硬断言（五个土方段**不得**被判成"适配器未实现"）；`unknown` 不得静默吞掉（FR-010） | ✅ |

**Gate 结论：通过。** 无需要豁免的条款。

### 若实现中发现需要改 schema

按宪法原则 IV，**不在本特性内加迁移**。应停止并回到 `/speckit-specify` ——
因为"需要新表才能记录缺口"意味着需求本身变了（缺口应当是**算出来的**，不是存下来的）。
存下来还会引入新的失败模式：存的那份会与实库漂移。

## Project Structure

### Documentation (this feature)

```text
specs/001-empty-table-gap/
├── plan.md              # 本文件
├── spec.md              # 规格
├── research.md          # Phase 0 输出
├── data-model.md        # Phase 1 输出
├── quickstart.md        # Phase 1 输出
├── contracts/           # Phase 1 输出
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

`(?)` 标记＝**待 Phase 0 研究确认**的落点；`＋` 标记＝本特性新增。

```text
scaffold/
├── modules/
│   ├── M2-ingest/
│   │   ├── app.py                        ← ＋ GET /v1/design/capabilities
│   │   │                                    （段→实现状态、段→源文件是否存在）
│   │   └── adapters/weidi/__init__.py    （只读：IMPLEMENTED / CAPABILITIES / _PARSERS）
│   ├── M3-rpdao/rpdao/
│   │   ├── pool.py                       ← ＋ Dao.table_census() 扩展：带出 owner
│   │   └── catalog.py                    （只读：TABLE_OWNER 作为归属真源）
│   ├── M6-api/app.py                     ← ＋ GET /v1/catalog/gaps
│   │                                        （拼装 M3 表事实 ＋ M2 段事实 → 空因）
│   └── M9-console/
│       ├── tables.html                   ← 改版：空表区加 空因/归属/建议动作
│       ├── app.py                        （路由已存在，可能无需改）
│       └── Dockerfile                    （新增静态资源须 COPY，见宪法"接入约束"）
├── contracts/openapi/
│   └── m6-gateway.v0.3.yaml              ← ＋ /v1/catalog/gaps 契约登记
└── tests/contract/
    ├── test_dao_contract.py              ← ＋ 归属字段组
    ├── test_api_routes.py                （既有：会强制契约同步）
    ├── test_console.py                   ← ＋ 空因展示组
    └── test_gap_attribution.py           ← ＋ 新建：空因分类的应通过/应拒绝 + 元测试
```

**Structure Decision**: 沿用既有 M1–M10 模块划分，**不新增模块**。
本特性是"把已有事实接起来"，不是新能力 —— 新增模块会让四个组多一次对接成本，
而收益只是少改几个文件。

## Phase 0 与 Phase 1 的产出

- `research.md` —— 解决三个 `(?)`：①段↔表映射的真源放哪 ②M2 的源文件检查接口形态
  ③排序判据的精确权重
- `data-model.md` —— 空因枚举、缺口条目的字段与不变量
- `contracts/` —— `/v1/catalog/gaps` 与 `/v1/design/capabilities` 的响应契约
- `quickstart.md` —— 可复跑的验收脚本（含 SC-002 的硬断言）
