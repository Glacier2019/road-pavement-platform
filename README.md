# 路面性能数智化平台 · 2025Y095

G228 福州滨海大道试验段路面性能数智化平台。

---

## 🚀 新人从这里开始（学生 / 新同学）

> **先读 [`output/学生上手指南-分模块开发手册.md`](output/学生上手指南-分模块开发手册.md)。**
> 那一份是为你写的：环境怎么搭、已有代码怎么读、你的模块要做什么、
> 具体怎么做、怎么测、交付什么——**照着做就能开工**，不需要先通读其他文档。

三条最快路径：

| 你想干什么 | 看哪 |
|---|---|
| **我要开始写自己的模块** | 手册 §0.3 选一条阅读路径 → §3 搭环境 → §5 找到你的模块 |
| **我先看看已有代码** | 手册 §2.2「六个必须读懂的参考实现」，直接点开读 |
| **我不熟规矩，怕返工** | 手册 §4（四份契约 / 五件套 / 门禁 / 三条纪律 / 反模式） |

```bash
git clone https://github.com/Glacier2019/road-pavement-platform.git
cd road-pavement-platform
```

**一分钟自检**（不需要 Docker、不需要数据库、不需要网络）：

```bash
cd scaffold && ./run_contract_tests.sh      # 预期：通过 9 ｜ 失败 0
```

**当前重心**：全项目唯一的关键缺口是 **M4 真数据门**（`truth_flag` 至今全为 false，
真值晋升从未发生过）。A 组的第一个动作不是写代码，是**拿到真实数据**。

---

仓库分三个顶层目录，**各管一件事，不要混用**。

| 目录 | 是什么 | 什么时候动它 |
|---|---|---|
| **`scaffold/`** | **平台开发代码**（7 域 10 模块的骨架栈） | **日常开发只动这里** |
| **`output/`** | 交付物：报告、图件、以及**表结构 DDL 真源** | 出报告 / 出图 / 改表结构（走工单） |
| **`docpipe/`** | 文档与图件的**生成流水线**（脚本 + 素材） | 需要重出文档或图时才动 |

---

## `scaffold/` —— 开发代码

真正要持续开发的东西。已落地 **M1 存储 / M2 接入 / M3 数据出口 / M6 统一出口**，
覆盖 LO 域（WIM 轴载）一条端到端竖切；**M4/M5/M7/M8/M9/M10 骨架已就位**，
业务层待各组填充。详见 [`scaffold/README.md`](scaffold/README.md)。

```bash
cd scaffold
cp .env.example .env
docker compose -f docker-compose.skeleton.yml up -d
```

> 需要非默认模块时显式指定 profile（如 `--profile m4 up -d governance`）。
> 默认只起 6 个基线服务——**门禁没开的模块不该跑起来**。

| 子目录 | 内容 |
|---|---|
| `packages/rpdao/` | **M3 对象域数据访问层**——平台唯一接触存储的地方，含**表级写权守卫**（契约③ 真源在其 README） |
| `services/ingest/` | M2 接入服务（MQTT → 契约校验 → 落库 / 拒收），**完整竖切参考实现** |
| `services/api/` | M6 统一数据出口（只经 M3 取数） |
| `services/governance/` | M4 数据治理·真数据门 ← **P1 关键路径** |
| `services/{fusion,semantic,apps,console,agent}/` | M5/M7/M8/M9/M10 骨架 |
| `contracts/` | 四份契约：报文 / 表结构 / DAO / 服务接口 |
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

## `output/` —— 交付物（含不可动的真源）

报告、图件（PDF/SVG/PNG/TIFF）、以及**表结构 DDL 真源**。

> ⚠ **`output/路面性能数据库-DDL-v0.2.sql` 是契约②的唯一真源**，
> `scaffold/docker-compose.skeleton.yml` 直接挂载它给 PostgreSQL 初始化。
> **动这个文件前先想清楚**：它一变，数据库实际结构就变；改它要走工单 + 评审。
> 也正因如此，本目录**保持在仓库根**，不要搬进 `docpipe/`（否则挂载路径要跟着改）。

---

## `docpipe/` —— 文档与图件流水线

出报告和画图用的一套脚本与素材，**不是平台代码**，开发时不需要碰。

| 子目录 | 内容 |
|---|---|
| `scripts/` | 28 个生成脚本（架构图 / 课题计划文档 / 报告 / 早期抽取工具） |
| `materials/` | 原始资料：标准规范、专利、学位论文、设计方案（约 149 MB，**不入库**） |
| `extracted/` | 从素材 PDF 抽出的文本层 |
| `bands/` | 制图流水线的中间裁切图（约 17 MB，**不入库**，可由 `crop_bands.py` 再生） |
| `input/` | 原始笔记与待确认项 |
| `audit/` | 会话审计产物 |

**脚本一律用绝对路径**（`/data/cy/shujuku/...`）定位输入输出，所以**从哪个目录执行都能跑**：

```bash
cd /data/cy/shujuku
python3 docpipe/scripts/gen_arch_figures.py     # 重出架构图（写入 output/）
python3 docpipe/scripts/qa_render.py            # 图件视觉 QA 栅格
```

主要脚本：

| 脚本 | 作用 |
|---|---|
| `gen_arch_figures.py` | 架构图 A–D（含图件契约：尺寸、字号、可复现性） |
| `gen_arch_diagram.py` | 早期架构图 |
| `qa_render.py` | 印刷尺寸栅格与裁切块，供视觉核对 |
| `gen_topicB_plan_docx.py` / `gen_topicA_plan_docx*.py` | 课题计划文档（docx） |
| `build_docx_report_v3_std.py` | 总设计报告（docx） |
| `extract_pdfs.py` | 批量抽取 `materials/` 下 PDF 文本到 `extracted/` |

> 历史脚本（`make_fig8.py`、`patch_fig8*.py`、`gen_er.py`、`ocr_road.py`、
> `vl_*.py` 等，2026-09-04~05）是早期一次性工具，保留备查，正常开发不必运行。

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
`pgdata/`、`miniodata/`、`grafanadata/` 都是本机状态或可再生产物，已在 `.gitignore` 中排除。
