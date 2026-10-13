# Implementation Plan: 坐标基准跟着项目走

**Branch**: `005-project-crs-binding` | **Date**: 2026-10-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/005-project-crs-binding/spec.md`

## Summary

**要解决的事只有一件：让「用哪个坐标基准」从部署级变成主体级。**

现状是：`app.py` 里 `_geo_crs()` **空参**调用 `geo_mod.load_crs()`，而 `crs_path()` 返回 `os.getenv("RP_CRS_CONFIG", "crs.yaml")` —— 一个进程级的相对路径。**全平台一份基准。**

难能可贵的是，**这件事比预想的小得多**。五条链路里四条已经通用：

| 链路 | 现状 | 本 feature 要动吗 |
|---|---|---|
| 投影数学 | `forward(lat, lon, central_meridian_deg)` / `inverse(north, east, central_meridian_deg)` —— **中央子午线本来就是参数** | ❌ 零改动 |
| 读配置 | `load_crs(path)` —— **本来就收路径** | ⚠ 改成收一个 dict |
| 层级 | `design_project` 表 + `road_section.design_project_id` 已存在 | ⚠ 加列 |
| EPSG 权威 | `spatial_ref_sys` 8500 行 + PostGIS 3.4.3 | ❌ 只读它 |
| **绑定** | **任何业务表都没有 srid/epsg/crs/zone/meridian 列** | ✅ **这里就是缺口** |

⇒ **本 feature 是「绑定」问题，不是「建模型」问题。**

**技术路线（四条已裁决）**：

1. **两级都要有**（Q1）—— 项目级 + 路段级。
2. **两级必须一致，不等就报错**（Q1b）—— 路段级是**冗余校验**，不是第二个真相来源。
3. **存在数据库**（Q2）—— 不新建配置文件。
4. **`spatial_ref_sys` 只做登记与校验**（Q4）—— 换算仍走已验证的手写投影。
5. **`crs.yaml` 保留、改为从库导出的产物**（Q3）—— 五个消费者一个不改。

## Technical Context

**Language/Version**: Python 3.13（容器），本机 3.13/3.10 双版本；一个 JS 消费者（Node v24.18.0）

**Primary Dependencies**: FastAPI 0.115.6、httpx、`psycopg[binary,pool]==3.2.3`、PyYAML 6.0.2。**★ 无 pyproj、无 GDAL、无 rasterio —— 投影是手写的高斯-克吕格。**

**Storage**: PostgreSQL + PostGIS 3.4.3。
- 要加列的：`design_project`（现 1 行 / 11 列）、`road_section`（现 2 行 / 13 列）
- 只读的：`spatial_ref_sys`（8500 行，六个相关 SRID 已核对：4547/4548/4549/4526/4527/4528）
- **不变**：DDL 与 migrations 的一致性（宪法 IV）

**Testing**: `scaffold/run_contract_tests.sh`（**套件数不写在这里**；基线判据是 **失败 0 ｜ 跳过 0**）。★ 区域帧那一套刻意做成**不需要数据库、不需要容器、不需要网络** —— 这条性质必须保住。

**Target Platform**: Linux 容器，`docker compose`，六个链容器（pg/mqtt/ingest/api/console/grafana）

**Project Type**: 多模块单体仓库（`scaffold/modules/M*`），模块间只认契约

**Performance Goals**: 无新增性能目标。★ 唯一相关的是**不要引入每次换算都连库**（Q4 已否掉那条路）。

**Constraints**:
- ★ **宪法 II 硬线**：M6 **不得直连存储**（`test_dao_contract.py` §4 用 AST 断言 `app.py` 里没有 psycopg / ConnectionPool）。**读只经 M3（rpdao）。**
- ★ **宪法 II 的措辞是「结构上做不到」，不是「AST 恰好没查到」。** 已核实：那条 AST 断言**只扫 `app.py` 一个文件**，而 `requirements.txt` 里 psycopg 是装着的。**在 `geo.py` 里加连接会绕过字面、违反意图 —— 所以不许。**
- ★ FR-004：不许「改配置要重新构建镜像」。
- ★ 1 m 是判据地板，真实噪声底 0.69 mm。

**Scale/Scope**: 现在只有 **1 个真实项目**（「毕设」）+ **2 个路段**，其中：

```
路段 1「滨海大通道试验段」 design_project_id = NULL   线元 0 / 地面点 0 / 断面 2
路段 6「毕设」            design_project_id = 2      线元 33 / 断面 1
```

★★ **⇒ 平台今天只有一条有坐标的路段，而它属于唯一的那个项目。** 多项目这件事**没有任何真实数据在跑**。所以 SC-001/SC-002/SC-008/SC-009 必须在**造出来的第二个项目**上证明，spec 的 Assumptions 已经把这条写明了。

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| 原则 | 本 feature 怎么满足 | 机器怎么验 |
|---|---|---|
| **I. 契约先行** | 基准的字段名进 `contracts/openapi/m6-gateway.geo.*.yaml` 的 `schema:` 块；**应通过／应拒绝两侧都测**（一致 → 200；不一致 → 拒绝） | 契约测试 + 现有 `test_api_routes.py` 双向比对 |
| **II. 三条数据硬线** | ★ **`geo.py` 保持零数据库、纯函数**；基准由 `app.py` 经 `dao.ge`（rpdao）取到再**传进去**。M6 不新增 psycopg | `test_dao_contract.py` §4 继续绿；**新增一条 AO 断言 `geo.py` 无 psycopg**（见下） |
| **III. 证据优先级** | 全部数字是**实库实测**（8500 行、六 SRID、11/13 列、1 行项目）；与文档冲突时以实库为准 | `information_schema.columns` 实测记录在 research.md |
| **IV. 迁移不可变** | 加列走**新迁移**，不就地改 `*migrate*.sql`；`10_ddl_v0.6.sql` 同步更新 | `test_ddl_migration_parity.py`（`m1-装库一致`）必须继续绿 |
| **V. 反空转** | ★ SC-012 那条「文件与库一致」的检查**必须配元测试**：故意造不一致，断言必须变红 | 元测试系列，按本项目既有命名 `元测试：…必须被认出来（非摆设）` |
| **接入约束：配置全外置** | 基准不再来自部署级 `crs.yaml`，而来自库 —— **比原来更外置** | 无硬编码带号；`test_sim_track.py` 那条「带号是配置项，不是代码里的常量」继续绿 |
| **接入约束：容器定义同步** | 若 `crs.yaml` 变为生成物，**compose 与 Dockerfile 的 COPY 关系必须重新对齐**（004 踩过「容器里有路由、镜像里没文件」） | `test_console.py` 的容器断言 + 实际重建后探针 |
| **VI. 通用商业化优先**（1.1.0 新增，**2026-10-13 补录**） | ① **无硬编码平台级标识**：`SRID = 带号 + 4509` 这个规律**不进代码、不进 DDL 的 `check`**；带号/中央子午线一律来自库。② **国际标准标识为权威**：`crs_epsg` 是唯一权威字段，三个习惯记法（带号/中央子午线/带宽）为**可空注释**，**有则比行为、无则不报错**。③ **自证不算证据**：投影精度必须有**非同源**参照系 | ① `test_sim_track.py`「带号是配置项，不是代码里的常量」+ §16/§17 反例枚举；② §17a–§17f；③ **§19**（PostGIS 的 `ST_Transform` 独立对拍） |

**Gate 结论：通过，无原则需要破例。** 唯一需要新增的强制手段是「`geo.py` 无 psycopg」这条 AO 断言 —— 因为现有断言只覆盖 `app.py`，**而 005 是最容易在 `geo.py` 里破线的一次改动**。

> ★★ **2026-10-13 重跑（宪法升到 1.1.1 后）**：上表原本**没有原则 VI 这一行**。
> 原因不是「当时认为它不适用」，而是**它当时还不存在** —— 原则 VI 是 2026-10-12（1.1.0）加的，
> 而这张 Gate 是它之前写的。★ 重跑时按 1.1.0 的三条强制逐条对照，
> 结论仍是「通过，无需破例」，但**依据是事后实测的**：
> ① 由 §16/§17 与 `test_sim_track.py` 守着；② 由 §17a–§17f 守着；③ 由 §19 守着。
> ★★★ 而这次重跑暴露的真问题不在本 feature，在**流程**：宪法加了原则，
> 而模板的检查表位置只写着 `[Gates determined based on constitution file]` ——
> **一个不列原则的占位符**。所以新原则不会在任何 plan 里产生新行，
> 也不会有人发现少了。已修（模板 + 新增 `test_spec_kit_consistency.py`）。

## Constitution Check（Phase 1 设计后复核）

*GATE: Re-check after Phase 1 design.*

设计已经落到 research.md / data-model.md / contracts/ 上，逐条复核：

| 原则 | 设计后是否仍然满足 | 依据 |
|---|---|---|
| **I. 契约先行** | ✅ | 契约**没有就地改** —— 新出 `m6-gateway.geo.v0.2.yaml` + `契约变更工单-005.md`（宪法 I 要求「变更走工单，附版本号递增与兼容性声明」，工单里两条都有）。★ 字段名全部落在 `schema:` 块里，**机器可解析**；已用 PyYAML 验过：五条路径的 `required` 每一项都有类型声明 |
| **II. 三条数据硬线** | ✅ | `geo.py` 保持纯函数（data-model E7 明确「零改动」的只有 `forward`/`inverse` 的参数）；基准经 `dao.ge` 取。★ **新增一条断言**（`geo.py` 无 psycopg） |
| **III. 证据优先级** | ✅ | 契约 v0.2 的字段名**全部来自活体实测**（五条路径的键集逐个打印过）；`4545` 这个值也从「凭记忆」改成「查表验过 + 21/21 无反例」 |
| **IV. 迁移不可变** | ✅ | 加列走新迁移；`10_ddl_v0.6.sql` 同步（它是全新装真相）。`test_ddl_migration_parity.py` 必须继续绿 |
| **V. 反空转** | ✅★ | SC-012 的一致性检查**配元测试**（quickstart 第 8 步就是那个元测试的剧本：故意造不一致，**必须变红**）。★ **本设计过程里这条原则又抓到一个实例**：我用一个筛错的查询得到空结果，差点把它读成「4545 不存在」（research.md R10）—— **「查不到」这个结论必须带着查询一起报** |
| **接入约束：配置全外置** | ✅ | 基准从部署级环境变量**上移**到数据库，比原来更外置。★ 且不把 `SRID = 带号 + 4509` 硬化成代码常量（`test_sim_track.py` 的守卫逐字要求「带号是配置项，不是代码里的常量」） |
| **接入约束：容器定义同步** | ✅ | `crs.yaml` 仍是文件、仍是 COPY 进去的产物，compose 与 Dockerfile 的关系**不变** |
| **VI. 通用商业化优先** | ✅★ | ① 无硬编码：`SRID = 带号 + 4509` 只写在文档里做参考，**代码与 DDL 都没有它**；实测 21/21 无反例但**仍然不硬化**。★ 且 §16/§17 的反例枚举证明「别的带号算出来是错的」；② 权威字段是 `crs_epsg`，三个习惯记法可空、**缺失时不报错**（§17a–§17f 逐一测过）；③ ★★ **本 feature 期间拿到了非同源参照系**：PostGIS 的 `ST_Transform`。★ 这条不是「顺手加的」——**它把设计里一处本来会自证的地方补上了**（见下方第 4 条） |

### 设计后重跑 Gate 时新发现的一条（2026-10-13 补）

**4. 「投影是准的」原本是自证的。** 这条不在原设计里 —— 原设计的精度证据是
`forward` → `inverse` → 回到原点（004 的 ≤0.457 mm）。而**往返闭合只证明两个函数互为逆运算**，
不证明它们都对着同一个正确的投影：两个实现一起偏，往返照样闭合，而且闭合得一样漂亮。
⇒ 按原则 VI ③（自证不算证据），这条证据是**不合格**的。
现已补 §19：与 PostGIS 对拍，实测 north 9.246 µm / east 0.001 µm。
★ 这是原则 VI 在**本 feature 内部**抓到的第一个实例，也是它值得写进宪法的原因。

### 设计后的三处主动收紧（不是为了过门，是因为设计过程中发现了）

1. ~~**data-model I1 拆成两句**：中央子午线用**算式**（`= 带号 × 3`），EPSG 用**查表**。~~
   ★★★ **本条已于 2026-10-12 作废**（宪法原则 VI）：那个「算式」里的 `3` **就是**硬编码常量，
   **它自己就违反了这条收紧要守的那条守卫**。现改为 **I1 的权威字段是 `crs_epsg`**，
   三个习惯记法是可空注释，**有则比行为、无则不报错**。详见 data-model.md E3 / research.md R11 补记。
2. **Q1b 的强制点从读路径移到写入侧**（research.md R6）。★ 读路径拦会把「一条路段的坏数据」升级为「整个列表接口不可用」。
3. **契约 v0.2 只加字段、不加路径**。★ 004 犯过一次：在修「声明了但不存在」的缺陷时，又声明了一条没有实现的路径。

## Project Structure

### Documentation (this feature)

```text
specs/005-project-crs-binding/
├── plan.md              # 本文件
├── spec.md              # ✅ 已写（328 行，四条待澄清全定为已定）
├── research.md          # Phase 0 输出
├── data-model.md        # Phase 1 输出
├── quickstart.md        # Phase 1 输出
├── contracts/           # Phase 1 输出
└── tasks.md             # Phase 2 输出（speckit-tasks）
```

### Source Code (repository root)

```text
scaffold/
├── sql/
│   ├── 10_ddl_v0.6.sql                    # ⚠ 全新装真相：加基准列
│   └── 1x_migrate_005_crs_binding.sql     # ✅ 新增迁移（不可变）
├── modules/
│   ├── M3-rpdao/rpdao/
│   │   ├── repo.py                        # ★ GeRepository（L185）加读基准的方法
│   │   │                                  #   sections() 378 / section() 430 已是现成入口
│   │   └── catalog.py                     # DOMAINS（L39）若需登记新列
│   ├── M6-api/
│   │   ├── geo.py                         # ★ 只改「基准从哪来」：load_crs(path) → 收 dict
│   │   │                                  #   ★ 加列后仍必须零数据库、纯函数
│   │   ├── app.py                         # _geo_crs() 由空参改为按主体取
│   │   └── requirements.txt               # ⚠ 不动（psycopg 装着但不许 import）
│   └── M9-console/
│       ├── app.py                         # L96 CRS_CONFIG 的读法可能随之调整
│       └── config/crs.yaml                # ★ 改为生成物
├── simulator/crg/export_viewer_mesh.py    # ★ 不改读法（stdlib-only，正则定点匹配）
└── tests/
    ├── contract/test_region_frame.py      # ★ 扩展：两级一致性、来源级别、文件库一致
    ├── contract/test_dao_contract.py      # ★ 新增：geo.py 无 psycopg
    ├── contract/test_sim_track.py         # 保持绿
    ├── contract/test_ddl_migration_parity.py  # 必须继续绿
    ├── integration/sim_inline_script.mjs  # 不改读法
    └── e2e/quickstart_probe.py            # 保持绿（清单 frame == crs.yaml）
```

**Structure Decision**: 沿用仓库现有多模块布局，**不新建模块、不新建服务**。改动集中在四层：DDL/迁移（加列）→ rpdao（读基准）→ M6-api（取基准并传入纯函数）→ 契约测试（两侧判据 + 元测试）。`crs.yaml` 由「手写配置」变为「库的导出产物」，**五个消费者的读法一个都不改**。

## Complexity Tracking

> 本 feature **无原则违例**，不需要破例。此节留空，但要记一条**新增的强制手段**：

| 新增的检查 | 为什么必须有 | 没有它会怎样 |
|---|---|---|
| `geo.py` 里不得出现 psycopg（AST/AO 断言） | 现有 §4 只扫 `app.py`。**005 恰恰是「给几何域加数据来源」的一次改动，最容易顺手在 `geo.py` 里连库。** 宪法 II 要的是「结构上做不到」，不是「AST 恰好没查到」 | 一条只写在文档里的硬线；`geo.py` 会悄悄获得连库能力，而所有测试全绿 |
| 「文件与库一致」的一致性命中检查 | Q3 取 (a)：文件成为库的产物。**光有生成步骤不够** —— 004 已经踩过「改了配置但结果没动」 | 文件悄悄过期，而每个消费者都读到旧值、全部测试通过 |
