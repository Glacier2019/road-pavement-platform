# 路面性能数智化平台 · 2025Y095

G228 福州滨海大道试验段路面性能数智化平台。

> **本仓库只装代码与契约**（约 0.5 MB / 83 个文件）。
> 交付物性质的 `output/`（报告、图件、课题计划文档）与 `docpipe/`（文档图件
> 生成流水线、标准规范素材）**有意不进来**——体积大、含未定稿材料与受版权
> 保护的素材，且开发时用不到。需要时向导师索取。

---

## 🚀 新人从这里开始（学生 / 新同学）

> **先读 [`学生上手指南-分模块开发手册.md`](学生上手指南-分模块开发手册.md)。**
> 那一份是为你写的：环境怎么搭、已有代码怎么读、你的模块要做什么、
> 具体怎么做、怎么测、交付什么——**照着做就能开工**，不需要先通读其他文档。

三条最快路径：

| 你想干什么 | 看哪 |
|---|---|
| **我要开始写自己的模块** | 手册 §0.3 选一条阅读路径 → §3 搭环境 → §5 找到你的模块 |
| **我先看看已有代码** | 手册 §2.2「六个必须读懂的参考实现」，直接点开读 |
| **我不熟规矩，怕返工** | 手册 §4（四份契约 / 五件套 / 门禁 / 三条纪律 / 反模式） |

```bash
git clone git@github.com:Glacier2019/road-pavement-platform.git
# 或网络受限时用浅克隆，只取最新一版：
git clone --depth 1 git@github.com:Glacier2019/road-pavement-platform.git
cd road-pavement-platform
```

**一分钟自检**（不需要 Docker、不需要数据库、不需要网络）：

```bash
cd scaffold && ./run_contract_tests.sh      # 预期：通过 9 ｜ 失败 0
```

**当前重心**：全项目唯一的关键缺口是 **M4 真数据门**（`truth_flag` 至今全为 false，
真值晋升从未发生过）。A 组的第一个动作不是写代码，是**拿到真实数据**。

---

## 仓库构成

| 路径 | 是什么 |
|---|---|
| **`scaffold/`** | **平台开发代码**——7 域 10 模块的骨架栈。**日常开发只动这里** |
| `学生上手指南-分模块开发手册.md` | 面向学生的上手指南（**先读它**） |
| `分组分模块任务书-P1起.md` | 任务分配总表：四个组、十个模块、谁做什么 |

---

## `scaffold/` —— 开发代码

已落地 **M1 存储 / M2 接入 / M3 数据出口 / M6 统一出口**，覆盖 LO 域（WIM 轴载）
一条端到端竖切；**M4/M5/M7/M8/M9/M10 骨架已就位**，业务层待各组填充。

```bash
cd scaffold
cp .env.example .env
docker compose -f docker-compose.skeleton.yml up -d
```

> 需要非默认模块时显式指定 profile（如 `--profile m4 up -d governance`）。
> 默认只起 6 个基线服务——**门禁没开的模块不该跑起来**。

| 子目录 | 内容 |
|---|---|
| `modules/M3-rpdao/` | **M3 对象域数据访问层**——平台唯一接触存储的地方，含**表级写权守卫**（契约③ 真源在其 README） |
| `modules/M2-ingest/` | M2 接入服务（MQTT → 契约校验 → 落库 / 拒收），**完整竖切参考实现** |
| `modules/M6-api/` | M6 统一数据出口（只经 M3 取数） |
| `modules/M4-governance/` | M4 数据治理·真数据门 ← **P1 关键路径** |
| `services/{fusion,semantic,apps,console,agent}/` | M5/M7/M8/M9/M10 骨架 |
| `contracts/` | 四份契约：报文 / 表结构 / DAO / 服务接口 |
| `sql/` | **契约② 真源**：`10_ddl_v0.2.sql`（32 表，compose 挂载它建库）＋ 数据字典 ＋ 分区 / 种子脚本 |
| `tests/contract/` | **九个契约测试**，改动后必跑 |
| `simulator/` | WIM 造数器（真设备到场前驱动整条链路） |
| `ops/grafana/` | 数据源与面板以代码提供，不靠手工点选 |

**改代码后请跑契约测试**（离线可跑，不需要容器）：

```bash
cd scaffold
./run_contract_tests.sh          # 全部九个
./run_contract_tests.sh m4       # 只跑 M4 相关的
./run_contract_tests.sh --list   # 列出全部
```

---

## 本机环境注意事项

> ⚠️ **下面这几条只适用于项目开发机**（路径都是 `/data/cy/shujuku/...`）。
> **学生自己的电脑上不需要**，直接 `docker compose ... up -d` 即可。
> 如果你在自己的机器上照抄这些变量，反而会因为路径不存在而跑不起来。

项目开发机上（Docker 配置与缓存都放在仓库内，避免污染 HOME）：

```bash
export DOCKER_CONFIG=/data/cy/shujuku/.dockerhome          # 否则 docker build 失败
export UV_CACHE_DIR=/data/cy/shujuku/.uvcache
export UV_PYTHON_INSTALL_DIR=/data/cy/shujuku/.uvpython
```

`scaffold/.env` 也是**每台机器一份**（从 `.env.example` 复制，已被 git 忽略）——
本机常用端口可能被占用，仓库统一避让成了 `55432 / 18883 / 3001 / 8001`。

`.dockerhome/`、`.uvcache/`、`.uvpython/`、`.pylibs/`、`.mplcache/`、`.qarenders/`、
`pgdata/`、`miniodata/`、`grafanadata/`、`output/`、`docpipe/` 都是本机状态或
可再生产物，已在 `.gitignore` 中排除。
