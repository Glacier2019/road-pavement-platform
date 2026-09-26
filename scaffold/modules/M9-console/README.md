# M9 平台管理台（集成面）

> **状态：中段**。集成面（容器／健康／指标／配置外置／契约文件／契约测试）
> 与三个页面已就位：首页、表盘点（`/tables`）、几何查询（`/geometry`）、设计导入（`/import`）。
> 其中 `/tables` 已接**空表缺口归因**（契约④）—— 每张空表给出空因、归属模块、建议动作。
> 其余模块的业务逻辑仍按契约返回 501 并标注 owner。

## 一、我是谁

| 项 | 值 |
|---|---|
| 模块 | M9 |
| 主责组 | D 组（应用决策） |
| 阶段 | P3·P4（天然排最后：先各模块验收，最后 M9 集成） |
| 服务目录 | `modules/M9-console/` |
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

## 五、页面与取数口径

| 页面 | 路径 | 取数 |
|---|---|---|
| 首页 | `/` | `/gw/v1/modules/status` |
| 表盘点 · 缺口归因 | `/tables` | `/gw/v1/catalog/tables` ＋ `/gw/v1/catalog/gaps` |
| 几何查询 | `/geometry` | `/gw/v1/geometry/*` |
| 设计导入 | `/import` | 写走 **自己的端点**，不借 `/gw`（`/gw` 是只读的） |

**页面一律经 `/gw` 取数，M9 永不直连数据库**（三条硬线之「读只经 M3 rpdao」）。
`/gw` 只转发 `v1/` 前缀下的 GET，且**只读** —— 一个无限制的转发器等于把 M9 变成任意 URL 抓手。

### 空表页的两处刻意的取舍

1. **次序不自作主张**。页面直接展示 `/v1/catalog/gaps` 给的次序，**不自己再排一遍** ——
   再排一遍就会出现「页面看到的」与「接口声称的」不一致，复核者拿 `priority_keys`
   复算时对不上，排序就不可证伪了。
2. **归因坏了不拖垮整页**。归因的依赖链比盘点长一跳（多经 M2）：
   取不到时降级为「只列空表、不显示空因」，但**必须把降级说出来**
   （页面上写着「这不是『这些表没有空因』」）。不说，读者就会把那 26 行
   理解成「这些表本来就没原因」。

> `/v1/modules/status` 会**实测**每个模块，把登记状态与实测结果并排显示。
> 刻意不缓存「上次健康」——集成面骗人就失去意义了。
> 同一条道理适用于缺口归因：**每次请求现取**，不缓存（见 `test_gap_attribution.py` 第 9 组）。

## 五·补、TODO（留给主责组）

* 模块登记表是否需要落库（若需要则走契约变更工单）
* 告警页：`/v1/alarms` 现由契约占位

## 六、别踩的坑（本项目已经踩过的）

1. **不要拿合成数据当验证依据**。造数器只注入有限的违约类型，且其中一类
   （`axle_num` 不符）因造数器自身的 bug 从未真正产生过——拿它「验证通过」会得到假结论。
2. **未标定的阈值不得用于生产判定**。凡有阈值的配置项都标了 `calibrated: false`，
   门禁据此拒绝出结论（返回 503 而不是「通过」）。
3. **不要在双引号字符串里夹中文引号**（`"…"…"`）——Python 与 YAML 都会报错，本项目已踩两次。
4. **容器内监听端口统一 8000**，宿主端口由 compose 的 `ports:` 决定，两者别搞混。
