# 模块骨架总纲 —— 各组开工前先读这篇

> 适用范围：M4 / M5 / M7 / M8 / M9 / M10 的骨架已建好，业务逻辑待各组实现。
> 已有实现：M1 / M2 / M3 / M6（这 4 个模块是这次的模板来源）。

---

## 一、现在是什么状态

**机械层已就位，业务层全部留空。** 每个模块都能：

- 起容器（`docker compose --profile mX up -d`）
- 报健康（`/healthz` 逐项报依赖，依赖不健康时返回 503）
- 出指标（`/metrics` 返回 Prometheus 文本）
- 被集成面看到（M9 会实测它并如实报可达/不可达）
- 跑契约测试（离线，不需要 Docker）

**业务接口一律返回 501 并标注 owner**。这是刻意的：让「还没实现」在接口层面可见，
而不是返回一个空数组让调用方以为「没有异常数据」。

---

## 二、六个模块一览

| 模块 | 服务目录 | profile | 宿主端口 | 主责 | 阶段 | 特殊约束 |
|---|---|---|---|---|---|---|
| **M4** 真数据门 | `modules/M4-governance/` | `m4` | 8020 | A 组 | P1 ★关键路径 | 阈值必须真实数据标定 |
| **M5** 融合辨析 | `modules/M5-fusion/` | `m5` | 8021 | C 组 | P2 | 门禁：等 M4 |
| **M7** 语义中枢 | `modules/M7-semantic/` | `m7` | 8022 | **待指派** | P4 | 映射须人工确认 |
| **M8** 应用层 | `modules/M8-apps/` | `m8` | 8023 | D 组 | P3 | 动作须人工确认 |
| **M9** 管理台 | `modules/M9-console/` | `m9` | 8024 | D 组 | P3·P4 | 天然排最后 |
| **M10** Agent | `modules/M10-agent/` | `m10` | 8025 | D 组 | 二期 | 仅冻结接口形状 |

---

## 三、各组怎么开工

```bash
cd scaffold
cp .env.example .env                     # 首次

# 1) 只起自己那个模块（原 6 服务自动带起，互不干扰）
docker compose -f docker-compose.skeleton.yml --env-file .env --profile m4 up -d --build

# 2) 看健康
docker exec rp-governance python3 -c \
  "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/healthz').read().decode())"

# 3) 改业务逻辑 → 跑契约测试
./run_contract_tests.sh m4               # 只跑自己那组
./run_contract_tests.sh                  # 跑全部（提交前必跑）
```

**为什么用 profile**：这 6 个骨架如果默认启动，会污染「6 服务全 healthy」这条已验证的基线
（而且骨架的 `/healthz` 在依赖未就位时会如实返回 degraded）。用 profile 后各组只启自己的，
这就是「可拔插」在开发期的具体样子。

---

## 四、实现业务逻辑时的三条纪律

### 1. 未标定的东西不许出结论

凡是有阈值／系数的配置项，都标了 `calibrated: false`。
**门禁据此拒绝出结论（返回 503，不是「通过」）。**

这不是形式主义——本项目已经踩过一次：造数器声称注入 3 类违约，
其中 `axle_num` 那类是**自我抵消**的（把 `axle_num` 加一后又补了一个轴明细，
两者重新相等），所以它从未真正产生过。如果拿这套数据「验证通过」，
会得到「三类违约都拦住了」的假结论。

> **一个永远不会失败的检查，比没有检查更糟。**

### 2. 不要拿合成数据当验证依据

造数器只覆盖有限的违约类型。真实数据的违约形态会多一个数量级。
它曾有一个极隐蔽的 bug（**已修**，见 §6）：`axle_num` 那类违约被**以错误的理由**拒收，
于是「被拒条数」的断言照样全绿，覆盖的却是别的分支。
各组实现完逻辑后，先把「需要什么真实输入才能标定」写进 README，
等真实数据到了再标定——**不要为了让测试绿而编阈值**。

### 3. 契约先行，改契约走工单

各组只认契约，不认别组的代码。四份契约：

| # | 契约 | 真源 | 维护人 |
|---|---|---|---|
| ① | 报文格式 | `contracts/topics.yaml`、`contracts/messages/*.schema.json` | B 组 |
| ② | 表结构 | `scaffold/sql/10_ddl_v0.3.sql` | A 组 |
| ③ | DAO 接口 | `scaffold/modules/M3-rpdao/` | A 组 |
| ④ | 服务接口 | `contracts/openapi/*.yaml` | D 组 |

本次新增的模块产出契约（同样是契约，同样走工单）：

| 模块 | 契约文件 |
|---|---|
| M4 | `contracts/governance/quality_rule.v0.1.schema.json`、`promotion.v0.1.schema.json` |
| M5 | `contracts/fusion/diagnosis.v0.1.schema.json` |
| M7 | `contracts/semantic/mapping_set.v0.1.schema.json` |
| M8 | `contracts/apps/maintenance_plan.v0.1.schema.json` |
| M9 | `contracts/console/module_registry.v0.1.schema.json` |
| M10 | `contracts/agent/action_ticket.v0.1.schema.json` |

> ⚠️ **M7 的 `mapping_set` 表不在冻结的 DDL 里**。要建表必须先走契约变更工单
> （MODULE-ONBOARDING.md §5）。这是有意留下的例子：新表不是不能加，而是不能就地加。

---

## 五、要新建一个模块怎么办

照抄任一骨架（建议抄 `modules/M4-governance/`，它最完整），六件事：

1. `modules/M<n>-<name>/` 下建 `app.py`、`Dockerfile`、`requirements.txt`、`config/`、`README.md`（M 编号对应新模块）
2. compose 里加服务段，**带自己的 profile**
3. 契约提交到 `contracts/<模块>/`
4. `tests/contract/` 里加测试，并在 `run_contract_tests.sh` 的 `TESTS` 数组登记
5. `modules/M9-console/config/modules.yaml` 里加一条登记（M9 就能看到它）
6. 跑 `./run_contract_tests.sh` 全绿再提交

---

## 六、已修的坑：一个「以错误理由被拒」的隐蔽 bug（值得当案例读）

**症状**：造数器的 `axle_num` 分支从未真正触发过质量门的那条校验。

**原始代码**：

```python
if kind == "axle_num":             # 想造「轴数与明细长度不符」
    payload["axle_num"] = n + 1
    payload["axles"].append({... "weight_kg": 5_000.0 ...})   # ← 两个改动互相抵消
```

两个改动互相抵消，`axle_num` 与 `len(axles)` 重新相等。更麻烦的是：那条多出来的
5000 kg 轴不在总重里，于是报文被**「总重偏差」**规则拦下——**事件被拒了，但拒因是错的**。

**为什么测试没发现**：原断言只数「被拒了几条」。50/50 被拒 → 通过 ✓，
覆盖的却是另一条分支。库里的实据：26 条违约里，被记为总重偏差的有 9 条其实是轴数不符
（差值恰为 `5000.0` 即指纹），且排障的人会去调总重校验——**方向全错**。

**三处修复**（缺一不可）：

1. `simulator/wim_simulator.py`：只改 `axle_num`，不动 `axles`；并加注入自检
   `_assert_injected()`，确认「想注入的违约确实注入了，且**只**注入这一种」。
2. `tests/contract/test_simulator_contract.py`：断言从「被拒」下沉到**「拒因正确」**，
   逐类核对 300 条样本的拒因分布。
3. `modules/M2-ingest/violations.py`（新增）：接入服务原先把所有违约记成同一个
   `issue_code='contract_violation'`，3 类违约在日志里无法区分。改为精确分类，
   码与 M4 的规则集共用命名空间；契约测试 `test_violation_codes.py` 盯着跨层命名一致。
   实测端到端 1:1 吻合：发 5 超速/6 总重/4 轴数 → 库里精确落成
   5 `range.speed_kmh`／6 `cross.gross_vs_axle_sum`／4 `cross.axle_num_vs_axles`。

**反向验证**：把 bug 塞回去 → 新断言报「`axle_num` 违约从未产生」「只产生 2/3 类违约」；
恢复后全绿。（旧断言在同一个 bug 下显示「50/50 被拒收 → 通过 ✓」。）

> **教训**：断言必须落到「为什么」上，不能停在「有没有」。一个以错误理由通过的检查，
> 比没有检查更糟——它给出的是虚假的覆盖信心。

---

## 七、验证记录（本次骨架的实测结果）

| 项 | 结果 |
|---|---|
| 语法编译 | 9 个 app.py 全部通过 |
| 契约测试 | 6 个测试全绿（含 M4 规则/晋升、M5–M10 模块契约） |
| M4 实测 | `/healthz` 200（PG 连通、11 条规则加载）；门禁正确关闭，校验/晋升返回 503 |
| M9 实测 | 探测 10 个登记模块，可达 6 / 不可达 4，如实区分（含 TCP 探测基础设施） |
| compose | 默认仍是原 6 服务；`--profile mX` 才多起对应模块 |
| 反向验证 | 故意删掉契约的必填字段 → 运行器正确报失败（5 通过 / 1 失败），恢复后全绿 |
| 原 6 服务基线 | 未受影响，全 healthy；端到端 SQL 与 HTTP 两侧均为 `passages=194 / overloaded=38` |

---

## 八、契约③写入收口：现在**写**也走 DAO 了（2026-09-15 起）

### 8.1 收口前的问题

`api`（M6）早就改为经 `rpdao` 取数、自身不持有连接池；但**写完全没收口**：

| 模块 | 收口前 |
|---|---|
| `ingest`（M2） | ❌ **自带连接池 ＋ 4 条裸 SQL** 直连写 `wim_axle_record` / `wim_axle_detail` / `data_import_batch` / `data_quality_log` |
| `rpdao`（M3） | ❌ 有读方法，**一个写方法都没有** |

后果：「写入只经 M2」这条硬线**只写在文档里**。任何模块 `import psycopg` 就能绕过契约
写库，而且**不会有任何报错**——这是"模块可拔插"最大的漏洞。

### 8.2 现在怎么用

```python
from rpdao.write import WriteDao

dao = WriteDao(PG_DSN, app_name="rp-ingest")
dao.open()

# 单表写：必须声明**你是谁**
dao.insert("data_quality_log", [{...}], writer="M2")

# 多表写：**必须**用 write_txn（见下方坑 #1）
with dao.write_txn(writer="M2") as tx:
    rid = tx.insert_returning("wim_axle_record", {...})
    tx.insert("wim_axle_detail", [...], on_conflict=("record_id", "axle_seq"))
# 出块即提交；块内抛异常则整体回滚

# 需要"列 = 列 + 增量"这类语义时（通用 upsert 表达不了）
dao.execute_write("data_import_batch", UPSERT_BATCH, {...}, writer="M2")
```

写权对照表在 `modules/M3-rpdao/catalog.py` 的 `TABLE_OWNER`（37 张可写 / 5 张只读）。
**不在表里的表一律拒写**——这是刻意的：默认允许会让每张新表都自动多一个无人看守的写入口。
（本行的数字由 `tests/contract/test_write_guard.py` 断言，改了 `TABLE_OWNER` 不改这里，测试会红。）

### 8.3 三条纪律

1. **默认拒绝**：只读表（字典/档案）任何人都写不进去。
2. **越权报错**：`writer="M5"` 去写 `wim_axle_record` 直接抛 `WriteGuardError`，不是静默成功。
3. **空写入报错**：0 行的写入不得当作成功返回。

---

## 九、收口/迁移里最隐蔽的一类坑：**旧语义没搬全**

契约③收口时抓到三个错，**没有一个是"新代码写错了"**，全是"原来有、搬的时候丢了"：

| # | 丢了什么 | 会怎样 | 怎么防 |
|---|---|---|---|
| 1 | **事务边界** | 把「主记录＋轴组明细」拆成两次 `dao.insert()`，两次各取一条池连接。进程在中间挂掉 → 留下**没有轴组明细的过车记录**。而下游看它**完全合法、不报错** | 多表写一律 `write_txn`；契约测试静态断言锁死 |
| 2 | **累加语义** | 批次计数原本是 `raw_count = raw_count + EXCLUDED.raw_count`（累加），通用 upsert 是 `SET col = EXCLUDED.col`（**替换**）→ 覆盖掉上一轮累计值 | 通用接口表达不了就走 `execute_write`，**不要为了套新接口而改语义** |
| 3 | **真实约束** | 给 `wim_axle_record` 声明去重键 `(device_code, pass_time)`——`device_code` **根本不是该表的列**，且该表唯一索引只有 `(id, pass_time)`（id 自增）。生成的 `ON CONFLICT` 让 PG 报 `no unique or exclusion constraint matching`，**运行时才炸** | 契约测试第 6b 组解析 DDL，断言每个去重键都有唯一索引背书 |

> **共同点**：三个错都能通过语法编译、都能让单元测试全绿。
> 它们只在**真跑**或**数据出错**时才暴露——第 1 个甚至不会报错，只是数据慢慢变脏。
>
> 所以做收口/迁移这类工作时，每一步都要问一句：
> **"原来的行为里，有哪些是接口签名看不出来的？"**
> ——事务边界、累加/替换语义、去重键对应的真实唯一约束、错误时的分支走向……
> 这些不在函数签名里，只在原代码里。**改之前必须把原代码逐条读完，不能只看签名。**

### 9.1 反向验证：怎么写"不会永远通过"的检查

新增的写权守卫测试（`tests/contract/test_write_guard.py`，50 断言）刻意对每条规则做**双向**验证：

- **应通过**：登记过的表 ＋ 正确的写入方 → 不抛异常
- **应拒绝**：只读表 / 越权 / 未登记表 / 空写入 → **必须**抛对应异常

只测"允许的能写进去"是**不够的**——那只能证明放行逻辑存在，不能证明拦截逻辑存在。
最后一组是**元测试**：确认这些断言不是恒真的空断言（若守卫被改成"永远放行"，
第 2 组必须失败；被改成"永远拒绝"，第 1 组必须失败）。

> 这就是本项目那条教训的正向用法：**一个永远不会失败的检查，比没有检查更糟**。
