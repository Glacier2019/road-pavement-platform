# 路面性能数智化平台 · 2025Y095

G228 福州滨海大道试验段路面性能数智化平台。仓库分三个顶层目录，**各管一件事，不要混用**。

| 目录 | 是什么 | 什么时候动它 |
|---|---|---|
| **`scaffold/`** | **平台开发代码**（7 域 10 模块的骨架栈） | **日常开发只动这里** |
| **`output/`** | 交付物：报告、图件、以及**表结构 DDL 真源** | 出报告 / 出图 / 改表结构（走工单） |
| **`docpipe/`** | 文档与图件的**生成流水线**（脚本 + 素材） | 需要重出文档或图时才动 |

---

## `scaffold/` —— 开发代码

真正要持续开发的东西。已落地 M1 存储 / M2 接入 / M3 数据出口 / M6 统一出口，
覆盖 LO 域（WIM 轴载）一条端到端竖切。详见 [`scaffold/README.md`](scaffold/README.md)。

```bash
cd scaffold
export DOCKER_CONFIG=/data/cy/shujuku/.dockerhome   # 本机 Docker 配置目录
docker compose --env-file .env -f docker-compose.skeleton.yml up -d
```

| 子目录 | 内容 |
|---|---|
| `packages/rpdao/` | **M3 对象域数据访问层**——平台唯一接触存储的地方（契约③ 真源在其 README） |
| `services/ingest/` | M2 接入服务（MQTT → 契约校验 → 落库） |
| `services/api/` | M6 统一数据出口（只经 M3 取数） |
| `contracts/` | 四份契约：报文 / 表结构 / DAO / 服务接口 |
| `sql/` | 分区维护与种子脚本 |
| `tests/contract/` | 契约一致性测试（四项，改动后必跑） |
| `ops/grafana/` | 数据源与面板以代码提供，不靠手工点选 |

**改代码后请跑契约测试**（离线可跑，不需要容器）：

```bash
cd scaffold
uv run --with jsonschema --with pydantic tests/contract/test_wim_contract.py
uv run --with jsonschema --with pydantic tests/contract/test_simulator_contract.py
uv run --with "psycopg[binary,pool]==3.2.3" tests/contract/test_dao_contract.py
uv run --with fastapi==0.115.6 --with httpx --with "psycopg[binary,pool]==3.2.3" tests/contract/test_api_routes.py
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

```bash
export DOCKER_CONFIG=/data/cy/shujuku/.dockerhome          # 否则 docker build 失败
export UV_CACHE_DIR=/data/cy/shujuku/.uvcache
export UV_PYTHON_INSTALL_DIR=/data/cy/shujuku/.uvpython
```

`.dockerhome/`、`.uvcache/`、`.uvpython/`、`.pylibs/`、`.mplcache/`、`.qarenders/`
都是本机状态或可再生产物，已在 `.gitignore` 中排除。
