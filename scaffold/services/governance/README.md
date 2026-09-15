# M4 数据治理 · 真数据门

> **状态：骨架**。机械层（容器／健康／指标／配置外置／契约文件／契约测试）已就位，
> **业务逻辑未实现**，接口返回 501 并标注 owner。契约先行，各组按契约填空。

## 一、我是谁

| 项 | 值 |
|---|---|
| 模块 | M4 |
| 主责组 | A 组（数据底座） |
| 阶段 | P1（**P1 的关键路径，唯一缺口**） |
| 服务目录 | `services/governance/` |
| compose profile | `m4` |
| 宿主端口 | 8020（容器内统一 8000） |

## 二、我认哪份契约（只认契约，不认实现）

**消费**
* 契约① 接入侧批次与质量日志（`data_import_batch` / `data_quality_log`）

**产出**
* `contracts/governance/quality_rule.v0.1.schema.json`
* `contracts/governance/promotion.v0.1.schema.json`

> 纪律：**文档不是契约，能跑的东西才是契约**（MODULE-ONBOARDING.md §1）。
> 契约变更走工单，禁止就地改、禁止口头改。

## 三、怎么跑

```bash
cd scaffold
cp .env.example .env      # 首次
# 默认只起原 6 服务；本模块用 profile 单独起，互不干扰
docker compose -f docker-compose.skeleton.yml --env-file .env --profile m4 up -d --build governance
docker exec rp-governance python3 -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/healthz').read().decode())"
```

契约测试（**离线，不需要 Docker**）：

```bash
uv run --with jsonschema --with pyyaml tests/contract/test_governance_contract.py
```

## 四、接入五件套自检（MODULE-ONBOARDING.md §2）

| # | 交付物 | 本模块 | 判据 |
|---|---|---|---|
| 1 | 容器定义 | ✅ `Dockerfile` | 有 Dockerfile 且在 compose 有服务段（含 healthcheck） |
| 2 | 健康与指标 | ✅ `/healthz` `/metrics` | healthz 逐项报依赖；metrics 出 Prometheus 文本 |
| 3 | 配置外置 | ✅ `config/` | 代码里零硬编码 |
| 4 | 契约文件 | ✅ `contracts/` | schema 已提交并在此登记 |
| 5 | 契约测试 | ✅ `tests/contract/` | 离线可跑，覆盖应通过/应拒绝两侧 |

## 五、TODO（留给主责组）

* **真值晋升**：`data_import_batch.truth_flag` false → true（这是 P1 的验收条件，至今从未发生过）
* 用真实数据标定 `config/quality_rules.yaml` 的 11 条规则（现 2 条已实现、9 条待标定）
* 批次质量报告（P1 交付物）
* 规则标定完成后把顶层 `calibrated` 改为 true，门禁才开

## 六、别踩的坑（本项目已经踩过的）

1. **不要拿合成数据当验证依据**。造数器只注入有限的违约类型，且其中一类
   （`axle_num` 不符）因造数器自身的 bug 从未真正产生过——拿它「验证通过」会得到假结论。
2. **未标定的阈值不得用于生产判定**。凡有阈值的配置项都标了 `calibrated: false`，
   门禁据此拒绝出结论（返回 503 而不是「通过」）。
3. **不要在双引号字符串里夹中文引号**（`"…"…"`）——Python 与 YAML 都会报错，本项目已踩两次。
4. **容器内监听端口统一 8000**，宿主端口由 compose 的 `ports:` 决定，两者别搞混。
