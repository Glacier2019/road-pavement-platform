# M9 平台管理台（集成面）

> **状态：骨架**。机械层（容器／健康／指标／配置外置／契约文件／契约测试）已就位，
> **业务逻辑未实现**，接口返回 501 并标注 owner。契约先行，各组按契约填空。

## 一、我是谁

| 项 | 值 |
|---|---|
| 模块 | M9 |
| 主责组 | D 组（应用决策） |
| 阶段 | P3·P4（天然排最后：先各模块验收，最后 M9 集成） |
| 服务目录 | `services/console/` |
| compose profile | `m9` |
| 宿主端口 | 8024（容器内统一 8000） |

## 二、我认哪份契约（只认契约，不认实现）

**消费**
* 各模块 `GET /healthz`（五件套第 2 件）
* 各模块的契约自述

**产出**
* `contracts/console/module_registry.v0.1.schema.json`

> 纪律：**文档不是契约，能跑的东西才是契约**（MODULE-ONBOARDING.md §1）。
> 契约变更走工单，禁止就地改、禁止口头改。

## 三、怎么跑

```bash
cd scaffold
cp .env.example .env      # 首次
# 默认只起原 6 服务；本模块用 profile 单独起，互不干扰
docker compose -f docker-compose.skeleton.yml --env-file .env --profile m9 up -d --build console
docker exec rp-console python3 -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/healthz').read().decode())"
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

* 前端界面（本骨架只提供服务端集成面）
* 模块登记表是否需要落库（若需要则走契约变更工单）

> `/v1/modules/status` 会**实测**每个模块，把登记状态与实测结果并排显示。
> 刻意不缓存「上次健康」——集成面骗人就失去意义了。

## 六、别踩的坑（本项目已经踩过的）

1. **不要拿合成数据当验证依据**。造数器只注入有限的违约类型，且其中一类
   （`axle_num` 不符）因造数器自身的 bug 从未真正产生过——拿它「验证通过」会得到假结论。
2. **未标定的阈值不得用于生产判定**。凡有阈值的配置项都标了 `calibrated: false`，
   门禁据此拒绝出结论（返回 503 而不是「通过」）。
3. **不要在双引号字符串里夹中文引号**（`"…"…"`）——Python 与 YAML 都会报错，本项目已踩两次。
4. **容器内监听端口统一 8000**，宿主端口由 compose 的 `ports:` 决定，两者别搞混。
