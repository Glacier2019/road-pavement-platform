# M5 融合辨析引擎

> **状态：骨架**。机械层（容器／健康／指标／配置外置／契约文件／契约测试）已就位，
> **业务逻辑未实现**，接口返回 501 并标注 owner。契约先行，各组按契约填空。

## 一、我是谁

| 项 | 值 |
|---|---|
| 模块 | M5 |
| 主责组 | C 组（融合诊断） |
| 阶段 | P2（门禁：等 M4） |
| 服务目录 | `modules/M5-fusion/` |
| compose profile | `m5` |
| 宿主端口 | 8021（容器内统一 8000） |

## 二、我认哪份契约（只认契约，不认实现）

**消费**
* 契约③ DAO（唯一取数通道，禁止直连库）
* M4 真值晋升后的批次（`truth_flag = true`）

**产出**
* `contracts/fusion/diagnosis.v0.1.schema.json`
* 表 `diagnosis_result`

> 纪律：**文档不是契约，能跑的东西才是契约**（MODULE-ONBOARDING.md §1）。
> 契约变更走工单，禁止就地改、禁止口头改。

## 三、怎么跑

```bash
cd scaffold
cp .env.example .env      # 首次
# 默认只起原 6 服务；本模块用 profile 单独起，互不干扰
docker compose -f docker-compose.skeleton.yml --env-file .env --profile m5 up -d --build fusion
docker exec rp-fusion python3 -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/healthz').read().decode())"
```

契约测试（**离线，不需要 Docker**）：

```bash
uv run --with jsonschema --with pyyaml tests/contract/test_module_contracts.py
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

* 桩号锚定基准
* IRI → RQI 反演标定
* 荷载-响应温度修正
* 实验室真值 ↔ 现场互校

> 这四项**本质都是标定工作**，必须用真实数据。`config/fusion.yaml` 里系数刻意留 `null`——
> 填任何数字都是编的。

## 六、别踩的坑（本项目已经踩过的）

1. **不要拿合成数据当验证依据**。造数器只注入有限的违约类型，且其中一类
   （`axle_num` 不符）因造数器自身的 bug 从未真正产生过——拿它「验证通过」会得到假结论。
2. **未标定的阈值不得用于生产判定**。凡有阈值的配置项都标了 `calibrated: false`，
   门禁据此拒绝出结论（返回 503 而不是「通过」）。
3. **不要在双引号字符串里夹中文引号**（`"…"…"`）——Python 与 YAML 都会报错，本项目已踩两次。
4. **容器内监听端口统一 8000**，宿主端口由 compose 的 `ports:` 决定，两者别搞混。
