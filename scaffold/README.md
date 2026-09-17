# 路面性能数智化平台 · 骨架栈（Walking Skeleton）

> 归属：福建省交通运输科技计划项目 **2025Y095** 研究内容 3（路面性能数据库底座）
> 对齐：报告 §6.1 容器化服务清单、§10.4「最小 MVP 四步」、研究生任务 Word 第九章（本体化与决策闭环增补交付物）
> 一句话目标：**让一条 WIM 过车报文穿过 6 个容器跑通全链**——此后每个模块都往这条链上长。

---

## 1. 为什么第一步是"一条竖切"，而不是"先写业务代码"

总体架构最容易死在一句话上："各模块都写好了，但装不到一起。"

所以第一步不是分头写业务逻辑，而是先造一条**最细的、能跑通的竖切**（walking skeleton）：
选一个最简单的事件，让它从设备侧一路走到界面，中间每个环节都用**最终会用的那套技术**。
竖切跑通的价值不在功能多少，而在于：

| 竖切解决了什么 | 具体表现 |
|---|---|
| 契约先于实现被验证 | MQTT 主题、报文 schema、表结构、API 四者当场对不上就能发现 |
| 环境与编排不再是"最后再说" | docker compose 一次起全栈，0 基础学生也能复现 |
| 每个人都拿到同一条基线 | 之后谁写模块，都照着这条链的接口接，不需要读别人的代码 |
| 失败模式提前暴露 | 设备未注册、分区不存在、QoS 丢包、时区错位——骨架期撞一次，比结题前撞一次便宜 |

> 结论口径：**P1 的第一周不产出业务成果，产出"全链可跑 + 四份契约冻结"**。

---

## 2. 竖切为什么选 LO 域（WIM 轴载）

| 候选 | 结论 | 理由 |
|---|---|---|
| **LO 交通荷载（WIM 过车事件）** | ✅ 选它 | 秒级、报文结构简单、分区表 DDL 已实测、不依赖 IoTDB、不占 GPU、可造数 |
| RE 结构响应（应变/振动） | 放 P2 | 高频连续流，要 IoTDB；采样率与降采样策略未定 |
| WE 环境气象 | 放 P2 | 分钟级，链路同 LO，无新增技术点，先做它没有教学增量 |
| SU 路面表面（路面图像） | 放 P3 | 走 MinIO + 视觉模型，链路长且依赖 GPU |

竖切链路（6 个容器）：

```
simulator ──MQTT──▶ EMQX ──▶ ingest(M2) ──▶ PostgreSQL 分区表
   (造数)           (1)         (2)校验+落库        (3) wim_axle_record
                                                        │
                              Grafana(6) ◀── api(M6) ◀──┘
                              面板           统一出口
```

---

## 3. 目录与"四份契约"

```
scaffold/
├─ SKELETON-GUIDE.md               # ★ 各模块开发总纲：开工前先读这篇
├─ run_contract_tests.sh           # ★ 一键跑全部契约测试（离线，--list 看清单）
├─ docker-compose.skeleton.yml     # 一套编排起全栈（端口避让本机已占用端口）
├─ .env.example                    # 复制为 .env；密码不入库、不进交付物
├─ contracts/                      # ★ 模块接入的唯一依据（四份契约 + 模块产出契约）
│   ├─ topics.yaml                 #   契约一：MQTT 主题 + 载荷信封规范
│   ├─ messages/wim_axle.v1.schema.json   # 契约一细则：报文 JSON Schema
│   ├─ openapi/m6-gateway.v0.3.yaml       # 契约四：服务接口（真源为 /openapi.json）
│   ├─ governance/ fusion/ semantic/ apps/ console/ agent/
│   │                              #   M4/M5/M7/M8/M9/M10 的**产出契约**（草案，待各组定稿）
│   └─ （契约二：表结构 = scaffold/sql/10_ddl_v0.3.sql，挂载给 PG 初始化）
├─ sql/
│   ├─ 20_partitions.sql           # 分区维护函数 + 建到 2027-12 + 兜底分区
│   └─ 90_seed_skeleton.sql        # 种子：路线/路段/结构层/断面/设备/通道/字典
├─ modules/                       # ★ 每模块一目录：学生只在各自目录扩展，最后整合
│   ├─ M2-ingest/                 # M2 接入服务（app.py/models.py/violations.py；契约①代码侧）
│   ├─ M3-rpdao/rpdao/            # M3 对象域数据访问层（平台唯一接触存储处，契约③）
│   │   ├─ catalog.py             #   7 域 ↔ 物理表目录（含自检）
│   │   ├─ repo.py                #   各域仓储（LO 域有专属语义化查询）
│   │   ├─ pool.py                #   连接池与执行原语（唯一 import psycopg 处）
│   │   └─ write.py               #   写权守卫（表级写权 + write_txn）
│   ├─ M4-governance/              # M4 数据治理·真数据门（骨架，待 A 组实现）★P1 关键路径
│   ├─ M5-fusion/                  # M5 融合辨析引擎（骨架，待 C 组实现；⛔ 门禁：等 M4）
│   ├─ M6-api/                     # M6 统一数据出口（含动作层占位 501；只经 M3 取数）
│   ├─ M7-semantic/                # M7 语义中枢（骨架，责任人待指派）
│   ├─ M8-apps/                    # M8 应用层（骨架，待 D 组实现）
│   ├─ M9-console/                 # M9 平台管理台·集成面（骨架）
│   └─ M10-agent/                  # M10 Agent 执行引擎（骨架，二期）
├─ simulator/wim_simulator.py      # 造数器（可注入"故意违约报文"）
├─ ops/grafana/provisioning/       # 数据源与面板以代码提供，不靠手工点选
└─ tests/contract/                 # 契约一致性测试（Schema ↔ Pydantic 裁决必须一致）
```

> **M4–M10 的状态**：机械层（容器／健康／指标／配置外置／契约／契约测试）已就位，
> **业务逻辑全部留空**，接口返回 501 并标注 owner。各组只写业务逻辑，不搭环境、不定接口。
> 6 个模块各带独立 compose profile，只启自己那个，互不干扰。
> 详见 `SKELETON-GUIDE.md`。

**四份契约 = 四个真源**，各自唯一（与报告里的 ①②③④ 对应）：

1. **报文格式**：`contracts/topics.yaml` + `messages/*.schema.json`　← 契约①（承诺方 M2）
2. **表结构**：`DDL-v0.1.sql` + `数据字典`（设计冻结，改它要走工单）　← 契约②（承诺方 M1）
3. **数据出口**：`modules/M3-rpdao/rpdao/README.md` + `catalog.py`　← 契约③（承诺方 M3）
4. **服务接口**：各服务的 `/openapi.json`（`contracts/openapi/*.yaml` 是人工摘要，仅供评审）　← 契约④（承诺方 M6）

以上是**模块间**的契约。各模块还各自**产出一份对外契约**（M4 的规则/晋升、M5 的诊断三元组、
M7 的映射集、M8 的养护建议、M9 的模块登记、M10 的动作工单），同样是契约，
同样走工单变更，同样有契约测试覆盖。

---

## 4. 起栈与自验（照抄即可）

```bash
cd /data/cy/shujuku/scaffold

# 0) 准备环境变量（两个密码必须改）
cp .env.example .env && vi .env

# 1) 起最小依赖（先只起库和 broker，快且省资源）
docker compose --env-file .env -f docker-compose.skeleton.yml up -d pg mqtt
docker compose --env-file .env -f docker-compose.skeleton.yml ps

# 2) 看表是不是真的建起来了（首次启动会自动执行 DDL + 分区 + 种子）
docker exec -it rp-pg psql -U rp -d road_pavement -c "\dt wim_*"
docker exec -it rp-pg psql -U rp -d road_pavement -c "SELECT serial_no, cross_section_id FROM sensor_install;"

# 3) 契约测试（不依赖容器，随时可跑）
uv run --with jsonschema --with pydantic tests/contract/test_wim_contract.py
uv run --with jsonschema --with pydantic tests/contract/test_simulator_contract.py
uv run --with fastapi==0.115.6 --with httpx --with "psycopg[binary,pool]==3.2.3" \
    tests/contract/test_api_routes.py

# 4) 起接入服务与出口服务
docker compose --env-file .env -f docker-compose.skeleton.yml up -d ingest api
curl -s localhost:8010/healthz | python -m json.tool      # pg/mqtt 都应为 true
curl -s localhost:8001/v1/objects/road_section | python -m json.tool

# 5) 造数：发 20 条过车报文（含 10% 故意违约）
uv run --with paho-mqtt simulator/wim_simulator.py --host localhost --port 18883 \
    --count 20 --interval 0.5 --invalid-rate 0.1

# 6) 验证闭环
curl -s "localhost:8001/v1/metrics/wim_hourly" | python -m json.tool
docker exec -it rp-pg psql -U rp -d road_pavement \
  -c "SELECT count(*) AS 过车数, sum(esal) AS esal合计 FROM wim_axle_record;" \
  -c "SELECT issue_code, count(*) FROM data_quality_log GROUP BY 1;" \
  -c "SELECT batch_no, raw_count, valid_count, truth_flag FROM data_import_batch;"
```

Grafana：<http://localhost:3001>（admin/admin），面板「骨架栈 · WIM 轴载链路」已自动装载。

**端口避让说明**：本机已有服务占用 5432 / 8000 / 3000，故骨架栈映射为
55432（PG）、18883（MQTT）、18083（EMQX 控制台）、9002/9003（MinIO）、3001（Grafana）、
8010（ingest）、8001（api）。改端口只动 `.env`。

---

## 5. 骨架周验收标准（DoD）

> **2026-09-15 实跑结论：7 条全部达成。** 逐条证据、跑出的 3 个缺陷与修法见
> 验收报告（`骨架栈实跑验收报告-20260915.md`，属交付物，随 `output/` 移出仓库）。其中第 1 条是在修掉
> 「`minio/minio:latest` 已失效导致 `up -d` 整体失败」与「ingest/api/grafana 无 healthcheck」
> 两个缺陷之后才达成的 —— **原来按字面无论怎么跑都不可能满足**。

一条竖切"跑通"的判据是下面 **7 条全绿**，缺一条不算：

1. `docker compose up -d` 一次起全栈，`ps` 全为 healthy；
2. PG 里 `wim_axle_record` 及其**当月分区**存在，且分区名符合 `wim_axle_record_pYYYYMM`；
3. 造数器发出的**正常报文 100% 落库**（`count(*)` 与发出条数一致，误差 0）；
4. 故意违约报文**不落主表**，且在 `data_quality_log` 里能看到对应 `issue_code`；
5. `data_import_batch` 里能看到批次计数（`raw_count` / `valid_count` / `truth_flag=false`）；
6. `GET /v1/metrics/wim_hourly` 返回的过车数与库里一致；Grafana 面板能画出曲线；
7. 契约测试三项全绿：`tests/contract/test_wim_contract.py`（报文）、`test_simulator_contract.py`（造数器）、`test_api_routes.py`（HTTP 路由，含"参数化路由不得遮蔽具体路由"的回归）。

> 第 3、4 条是最容易被忽略、又最值钱的两条：它把"数据接入"变成了**可证伪**的事情。
> 第 7 条里的路由测试是补上的一次真实教训：`/v1/objects/{object_type}` 若写在
> `/v1/objects/wim_axle` 之前，后者会被静默吃掉、永远 404 —— 语法编译与报文契约测试
> 都查不出来，只有发一次真实 HTTP 请求才暴露。

---

## 6. 分工与日程（0 基础，2 周）

| 天 | 事 | 主责 | 产出 |
|---|---|---|---|
| D1–D2 | 装 Docker + 起栈 + 看表；读 DDL 前 5 张表 | 全员 | 每人本机能 `psql` 看到 25 张表 |
| D3 | 冻结 `topics.yaml`；用 MQTTX 手工发一条报文 | B | 主题命名评审通过 |
| D4 | 接通 ingest，看到 `inserted` 计数增长 | A/B | `/healthz` 全绿 |
| D5 | 违约报文进入 `data_quality_log` | A | 拒收路径有据可查 |
| D6–D7 | 出口 API + Grafana 面板 | C/D | 面板可演示 |
| D8 | 契约测试 + 验收 7 条 | D | 验收单签字 |
| D9–D10 | 复盘：把 25 表按七域过一遍，标注各自的"对象类型" | 全员 | 《本体对象类型清单 v0.1》初稿 |

之后进入 P1：各人按 §7 顺序把自己的域接上来。

---

## 7. 从骨架到七域：模块接入顺序

```
M1 基础设施  ──┐
M2 接入服务  ──┤   ← 已含：M1 / M2 / M3 / M6（LO 域竖切跑通）
M3 数据访问  ──┼──▶ M4 质量治理 ──▶ M5 融合 ──▶ M8 决策 ──▶ M9 应用
M6 统一出口  ──┘        ↑              ↑
M7 本体/语义 ───────────┴──────────────┘（对象类型清单、链接定义、函数契约）

已完成（2026-09-15）：M1 存储/编排 · M2 接入 · M3 对象域数据出口 · M6 查询出口
下一个应做：M3 写入路径收口 → M4 质量治理门（M4/M5 都依赖 M3 这个统一接入面）
```

接入顺序的原则：**下游不启动，直到上游的两个契约测试绿**。
各域接入时的最小动作，见 `MODULE-ONBOARDING.md`。

---

## 8. 本骨架**不做**什么（边界）

- **数据出口已收口到 M3**：`modules/M6-api`（M6）不再 import psycopg、不再持有连接池，
  由 `tests/contract/test_dao_contract.py` 用 AST 断言钉死；**但 M2 的写入路径仍直连库**
  （它是"接入/落库"这个动作本身），写入收口是后续议题；
- 不做治理：质量门、真值标记（truth_flag 晋升）留给 M4；
- 不做本体：对象-链接图、SHACL 校验留给 M7（本骨架的 API 已为其预留形态）；
- 不做动作闭环：`/v1/actions/*` 一律 501，P3 才落地；
- 不动表结构：种子脚本只插数据，不新增字段——**设计冻结**；
- 不引第三方平台框架：只用 PostgreSQL / EMQX / MinIO / Grafana / FastAPI（+ pySHACL、MCP 属后续）。
