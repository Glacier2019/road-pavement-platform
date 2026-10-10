"""M9 平台管理台（集成面）—— 骨架

本模块的定位：**集成面**，不是业务模块。
报告 §6.4：「每模块验收＝契约测试＋假数据 demo，可在独立目录/分支开发，**最后 M9 集成**。」
所以 M9 天然排在最后，它的价值在于把各模块的**真实状态**汇总成一个可看的界面。

本模块认哪份契约
----------------
* **消费** 各模块的 `/healthz` 与 `/metrics`（五件套第 2 件）
* **消费** 各模块的 `MODULE_CONTRACTS` 声明（模块自述"我认哪份契约"）
* **产出** `contracts/console/module_registry.v0.1.schema.json`（模块登记表）

它刻意不做的事
--------------
* 不缓存"上次健康"的结论——健康状态必须是**当次实测**，否则集成面会骗人
* 不对失败的模块返回"正常"——不可达就如实报 unreachable

业务层 TODO（D 组）
-------------------
* 前端界面（本骨架只提供服务端集成面）
* 模块登记表落库（是否需要建表待定，若需要则走契约变更工单）
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from typing import Any

import httpx
import yaml
from fastapi import FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOG = logging.getLogger("m9-console")

# 模块登记表走配置外置：M9 是**读**各模块，自己不需要先建表
REGISTRY_PATH = os.getenv("MODULE_REGISTRY", "config/modules.yaml")
PROBE_TIMEOUT = float(os.getenv("PROBE_TIMEOUT_S", "3"))

# 几何浏览页取数用的 M6 地址。**只在服务端**（配置全外置，五件套第 3 件）：
# 页面自身不写死主机端口，所以 M9 部署到哪台机器、从哪个地址打开都能用。
GATEWAY_BASE = os.getenv("GATEWAY_BASE", "http://localhost:8001").rstrip("/")
GATEWAY_TIMEOUT_S = float(os.getenv("GATEWAY_TIMEOUT_S", "10"))

# 设计导入页要**写**，所以它**不走 /gw** —— 这不是遗漏，是契约划的界：
#   · `/gw` 的契约是「**有边界的只读转发，不是通用代理**」（见 /gw 的实现与 M9 的
#     契约测试：`POST /gw/... → 405`）。导入是写，塞进 /gw 就把那条界破了。
#   · M6 那边倒是有 `POST /v1/actions/{action_name}`，但它的契约写明那是
#     **受治理动作**：参数校验 → 权限校验 → 副作用 → 写回 → 审计留痕，
#     且**默认在人工确认后才执行**，计划实现的是
#     create_maintenance_ticket / dispatch_alert / confirm_event /
#     mark_data_quality_issue / request_design_change —— 是"要审批的事"。
#     数据接入不是那一类：它不需要人工确认，也不需要审计留痕那一套。
#   · 所以导入直连 M2。M9 的硬线是「**不得直连数据库**」（读只经 M3 rpdao），
#     不是"不得连别的服务" —— 这一条仍然是 HTTP，M9 依旧不碰库。
INGEST_BASE = os.getenv("INGEST_BASE", "http://localhost:8010").rstrip("/")
INGEST_TIMEOUT_S = float(os.getenv("INGEST_TIMEOUT_S", "120"))
#: 只转发这些前缀。**这不是通用代理**：只有 GET，且必须落在 M6 的 API 命名空间内 ——
#: 目的是让页面与 M9 同源（不必给 M6 放开 CORS），而不是把 M9 变成任意转发器。
GATEWAY_ALLOWED_PREFIXES = ("v1/",)

#: 仿真产物的**交付目录**。固定路径，由 compose 以**只读**方式挂进来。
#:
#: ★ 为什么是挂载而不是打进镜像：产物是**可再生的大文件**（网格 1.4 MB、
#:   视频 39 MB），打进镜像会让镜像每改一次产物就重发一次，而它们全都
#:   由 `scaffold/simulator/crg/` 下的脚本从库中数据再生。
#: ★ 为什么必须 `:ro`：宪法原则 II 的口径是"结构上做不到"优于"我们约定不
#:   这么做"。只读挂载让容器**在结构上无法写**交付目录，可复现性因此不靠纪律。
#: ★ 这是 `console:` 服务的**第一个 volumes** —— 它改变了 M9 的暴露面。
SIM_ARTIFACTS_DIR = pathlib.Path(os.getenv("SIM_ARTIFACTS_DIR", "/data/sim"))
#: 清单文件名。清单即**白名单** —— 目录里存在但未登记的文件取不到。
SIM_MANIFEST_NAME = "manifest.json"
#: 坐标基准配置。★ 局部帧**不需要**它；读它是为了把"带号是推断值"这件事
#: 显示出来（FR-017）。它不参与任何几何计算。
#: ★ 相对路径一律**按模块所在目录**解析，不按进程 CWD。
#:   原先写的是 `pathlib.Path("config/crs.yaml")` —— 那意味着"只有从对的目录
#:   启动才读得到"，而读不到时的表现是 `{"_error": ...}`：接口照样 200，
#:   页面上带号那一栏静静地空着。这类失败不会报错，只会少显示东西。
def _resolve_crs_config(raw: str) -> pathlib.Path:
    p = pathlib.Path(raw)
    return p if p.is_absolute() else pathlib.Path(__file__).resolve().parent / p


CRS_CONFIG = _resolve_crs_config(os.getenv("CRS_CONFIG", "config/crs.yaml"))

TABLES_PAGE = pathlib.Path(__file__).with_name("tables.html")
GEOMETRY_PAGE = pathlib.Path(__file__).with_name("geometry.html")
IMPORT_PAGE = pathlib.Path(__file__).with_name("import.html")
SIM_PAGE = pathlib.Path(__file__).with_name("sim.html")
INDEX_PAGE = pathlib.Path(__file__).with_name("index.html")

MODULE_CONTRACTS = {
    "consumes": ["各模块 GET /healthz（五件套第 2 件）"],
    "produces": ["contracts/console/module_registry.v0.1.schema.json"],
}


class State:
    def __init__(self) -> None:
        self.probes = 0
        self.last_error: str | None = None


STATE = State()


def load_registry() -> dict[str, Any]:
    from pathlib import Path

    p = Path(REGISTRY_PATH)
    if not p.exists():
        LOG.warning("模块登记表不存在：%s", p)
        return {"version": None, "modules": []}
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {"version": None, "modules": []}


REGISTRY: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    global REGISTRY
    REGISTRY = load_registry()
    LOG.info("已加载模块登记表：%d 个模块", len(REGISTRY.get("modules") or []))
    yield


app = FastAPI(
    title="M9 平台管理台 · 集成面（骨架）",
    version="0.1.0-skeleton",
    lifespan=lifespan,
)


def probe_tcp(url: str) -> dict[str, Any]:
    """TCP 连通性探测，给没有 HTTP /healthz 的基础设施模块用（如 M1 的 PG/MinIO）。

    用 HTTP 去测数据库会得到一个"连接被对端关闭"的误导性错误——
    那是协议不匹配，不是服务不健康。所以基础设施必须走 TCP。
    """
    import socket
    from urllib.parse import urlparse

    u = urlparse(url if "//" in url else f"//{url}", scheme="tcp")
    host, port = u.hostname, u.port
    if not host or not port:
        return {"reachable": False, "error": f"无法解析 tcp 目标：{url}"}
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            return {"reachable": True, "status_code": None, "body": f"tcp {host}:{port} 可连通"}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "status_code": None, "error": str(exc)[:200]}


def probe_one(url: str) -> dict[str, Any]:
    """实测一个模块的 /healthz。**不做缓存、不做兜底**——不可达就是不可达。"""
    try:
        with urllib.request.urlopen(url, timeout=PROBE_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return {"reachable": True, "status_code": resp.status,
                    "body": json.loads(raw) if raw.strip().startswith("{") else raw[:400]}
    except urllib.error.HTTPError as exc:
        # 模块自身报 503（依赖不健康）也算"可达但降级"，要如实区分
        raw = exc.read().decode("utf-8", "replace")
        try:
            body: Any = json.loads(raw)
        except Exception:  # noqa: BLE001
            body = raw[:400]
        return {"reachable": True, "status_code": exc.code, "body": body}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "status_code": None, "error": str(exc)[:200]}


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    mods = REGISTRY.get("modules") or []
    return {
        "service": "m9-console",
        "status": "ok",
        "dependencies": {
            "module_registry": {
                "ok": bool(mods),
                "detail": "" if mods else "登记表为空（骨架期预期：只有已接线的模块才登记）",
                "count": len(mods),
            }
        },
        "contracts": MODULE_CONTRACTS,
    }


@app.get("/metrics")
def metrics() -> str:
    return "\n".join([
        "# TYPE rp_console_probes_total counter",
        f"rp_console_probes_total {STATE.probes}",
        "# TYPE rp_console_modules_registered gauge",
        f"rp_console_modules_registered {len(REGISTRY.get('modules') or [])}",
    ]) + "\n"


@app.get("/metrics", include_in_schema=False)
def metrics_plain() -> PlainTextResponse:  # pragma: no cover
    return PlainTextResponse(metrics())


@app.get("/v1/modules")
def list_modules() -> dict[str, Any]:
    """模块清单（登记表原样透出，不含实时状态）。"""
    return {"version": REGISTRY.get("version"), "modules": REGISTRY.get("modules") or []}


@app.get("/v1/modules/status")
def modules_status() -> dict[str, Any]:
    """**实测**各模块健康状态。

    这是 M9 最有价值的接口：把"哪些模块真的通了、哪些还是壳"一次看清。
    骨架期预期：已登记的模块里，已落地的返回 ok，骨架返回 degraded/501。
    """
    mods = REGISTRY.get("modules") or []
    if not mods:
        return JSONResponse(
            {"detail": "登记表为空，无模块可探测",
             "hint": "config/modules.yaml 里登记模块的 healthz 地址即可"},
            status_code=503,
        )
    STATE.probes += 1
    out = []
    for m in mods:
        url = (m.get("healthz") or "").strip()
        kind = (m.get("probe_kind") or "http").lower()
        if not url:
            res: dict[str, Any] = {"reachable": False, "error": "未配置 healthz 地址"}
        elif kind == "tcp":
            res = probe_tcp(url)
        else:
            res = probe_one(url)
        out.append({
            "module": m.get("module"),
            "name": m.get("name"),
            "owner": m.get("owner"),
            "phase": m.get("phase"),
            "impl_status": m.get("status"),        # 登记的实现状态
            "probe": res,                          # 当次实测结果
        })
    ok_n = sum(1 for r in out if r["probe"].get("reachable"))
    return {"total": len(out), "reachable": ok_n, "unreachable": len(out) - ok_n, "items": out}


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index_page() -> HTMLResponse:
    """管理台首页（自包含 HTML：内联 JS + CSS，无构建步骤、无新依赖）。

    与 ``/geometry`` 不同，本页只打 **M9 自己的**接口（``/v1/modules/status``），
    不经 ``/gw/`` 转发 —— 它要显示的就是"哪些模块真的通了"，而这件事由 M9 实测，
    没有第二个数据源。同样**不接触数据库**。
    """
    if not INDEX_PAGE.exists():
        raise HTTPException(500, f"页面文件缺失：{INDEX_PAGE.name}")
    return HTMLResponse(INDEX_PAGE.read_text(encoding="utf-8"))


@app.get("/tables", response_class=HTMLResponse, include_in_schema=False)
def tables_page() -> HTMLResponse:
    """数据表盘点页（自包含 HTML：内联 JS + CSS，无构建步骤、无新依赖）。

    回答那个很容易答错的问题：「库里的表是不是每张都该显示出来？」
    · **不是**。``wim_axle_record`` 是分区表，物理上 1 父 + 39 子，逐张列出来
      屏幕上会多出几十行恒空的、看不懂的名字。
    · 该显示的是 **62 张逻辑表**，并**按有没有数据分开**：空表不是错误，
      是链路还没接——但混在一起就分不清「本来就没有」和「应该有却没有」。

    取数与 /geometry 同一条链：本页 → ``/gw`` → M6 → M3 rpdao。
    M9 依旧没有任何 SQL、也没有 PG_DSN。
    """
    if not TABLES_PAGE.exists():
        raise HTTPException(500, f"页面文件缺失：{TABLES_PAGE.name}")
    return HTMLResponse(TABLES_PAGE.read_text(encoding="utf-8"))


@app.get("/geometry", response_class=HTMLResponse, include_in_schema=False)
def geometry_page() -> HTMLResponse:
    """GE 道路几何浏览页（自包含 HTML：内联 JS + CSS，无构建步骤、无新依赖）。

    **本页不接触数据库**：它经 `/gw/` 转发到 M6（契约④），M6 再经 M3 rpdao（契约③）
    取数。三条数据硬线里的"读只经 rpdao"因此仍然成立 —— M9 只是网络中转，
    手里没有任何 SQL，也没有 PG_DSN（compose 里刻意不给它）。
    """
    if not GEOMETRY_PAGE.exists():
        raise HTTPException(500, f"页面文件缺失：{GEOMETRY_PAGE.name}")
    return HTMLResponse(GEOMETRY_PAGE.read_text(encoding="utf-8"))


@app.get("/sim", response_class=HTMLResponse, include_in_schema=False)
def sim_page() -> HTMLResponse:
    """局部帧三维起伏查看页（自包含 HTML：内联 JS + CSS，无构建步骤、无新依赖）。

    ★ **本页不加载任何外部资源。** 三维渲染是**内联手写 WebGL**，不是 CDN 上的
    three.js / CesiumJS。这不是偏好，是两条既有约束夹出来的结果：
      · M9 的契约测试已断言页面里不得有 `https?://` 的外部 script/link
        （既有测试，不是本页新增的规矩）；
      · 宪法「接入约束」要求自包含、无构建步骤、无新依赖。
    路面高程是**规则网格**，索引缓冲是纯算术产物 —— 手写恰好不难。

    ★ **本页不判定。** 它显示，不下结论。宪法「未标定的阈值不得用于生产判定」：
    颜色刻度是**显示刻度**，不是判定阈值。页面上不出现"合格/不合格"字样。
    """
    if not SIM_PAGE.exists():
        raise HTTPException(500, f"页面文件缺失：{SIM_PAGE.name}")
    return HTMLResponse(SIM_PAGE.read_text(encoding="utf-8"))


def _load_manifest() -> dict:
    """读交付目录的清单。**清单即白名单。**

    为什么不是 `StaticFiles` 挂目录：那样暴露面随目录内容增长，谁往目录里丢个
    文件就多一个可取的东西。白名单把「新增资产必须登记」从一句纪律变成
    **结构上做不到** —— 与宪法原则 II 的口径一致。
    """
    path = SIM_ARTIFACTS_DIR / SIM_MANIFEST_NAME
    if not path.is_file():
        raise HTTPException(
            503,
            detail=(
                f"交付目录里没有清单：{path}。"
                f"请先跑 scaffold/simulator/crg/export_viewer_mesh.py 生成产物"
                f"（环境变量 SIM_ARTIFACTS_DIR 当前为 {SIM_ARTIFACTS_DIR}）。"
            ),
        )
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(503, detail=f"清单不是合法 JSON：{exc}") from exc


@app.get("/v1/sim/manifest", include_in_schema=False)
def sim_manifest() -> JSONResponse:
    """页面启动时读一次：清单（含网格的 nu/nv/dtype 等解码所需元信息）+ 坐标基准。

    ★ 走 M9 **自己**的接口，不经 `/gw` —— 交付目录是 M9 的资产，
    没有第二个数据源。M9 依旧不碰数据库。
    """
    manifest = _load_manifest()
    crs: dict[str, Any] = {}
    if CRS_CONFIG.is_file():
        try:
            crs = yaml.safe_load(CRS_CONFIG.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            # 基准配置读坏了不该让整页打不开 —— 如实报出来，页面照常显示几何
            crs = {"_error": f"crs.yaml 解析失败：{exc}"}
    else:
        crs = {"_error": f"基准配置文件不存在：{CRS_CONFIG}"}
    return JSONResponse({"manifest": manifest, "crs": crs})


@app.get("/artifacts/{name}", include_in_schema=False)
def artifact_file(name: str, request: Request) -> Response:
    """按**清单白名单**取交付目录里的文件。

    ★ 只接受清单里 `present: true` 的 `name`。目录里存在但没登记的文件取不到，
      路径穿越（`../`、`%2e%2e%2f`）也取不到 —— 因为 `name` 要**先**在清单里匹配上，
      匹配不上就 404，根本不进入路径拼接。
    ★ 用 Starlette 的 `FileResponse`：它原生支持 `Range`，返回 206。
      不返回 206 的实现**视频照样能播，只是拖不动进度条** —— 而"能播"会让
      粗看的人以为它对了。所以这一条在契约测试里单独验。
    """
    manifest = _load_manifest()
    hit = None
    for item in manifest.get("artifacts", []):
        if item.get("name") == name and item.get("present"):
            hit = item
            break
    if hit is None:
        raise HTTPException(
            404,
            detail=(
                f"清单里没有登记（或标记为未产出）的产物：{name}。"
                "交付目录只按清单提供文件 —— 未登记的文件取不到。"
            ),
        )
    path = SIM_ARTIFACTS_DIR / name
    if not path.is_file():
        # 清单说在、磁盘上没有：这是**清单与磁盘不一致**，不是普通的 404。
        # 如实报 503 并说明，否则会被当成"文件没生成"而查错方向。
        raise HTTPException(
            503,
            detail=f"清单登记了 {name}（present=true），但交付目录里没有这个文件：{path}",
        )
    return _serve_file_with_range(
        path, hit.get("mime") or "application/octet-stream", request.headers.get("range")
    )


def _serve_file_with_range(
    path: pathlib.Path, media_type: str, range_header: str | None
) -> Response:
    """带 `Range` 支持的文件响应。**故意不用 FileResponse 的 Range。**

    ★ 为什么自己写：`FileResponse` 的 Range 支持是 **starlette ≥ 0.39 才有的**，
      而契约测试跑在宿主机（starlette 0.38.6）、交付物跑在容器（0.41.3）——
      两个环境行为不同。`requirements.txt` 只钉了 fastapi，**starlette 是浮动
      解析的**，所以"契约测试通过"在 Range 这一条上会失去意义。
      自己写一遍，两边行为一致，那条契约才真的可验。
      （不是不信库，是不信**两个不同的库**。）

    ★ 为什么 Range 重要：不返回 206 的实现**视频照样能播，只是拖不动进度条**。
      "能播"会让粗看的人以为它对了 —— 所以契约测试单独验这一条。
    """
    size = path.stat().st_size
    total_hdr = {"accept-ranges": "bytes", "content-type": media_type}

    def _full() -> Response:
        # ★ 整文件路径也自己出，不走 FileResponse：0.38.6 的 FileResponse
        #   连 `accept-ranges` 都不设，浏览器因此**不会给你进度条**。
        #   理由与上面同一条 —— 两个环境行为必须一致。
        return Response(
            content=path.read_bytes(),
            status_code=200,
            headers={**total_hdr, "content-length": str(size)},
        )

    m = re.fullmatch(r"bytes=(\d*)-(\d*)", (range_header or "").strip())
    if not m:
        # 无 Range、多段区间、畸形区间：**回退成整文件 200**（合法，且不假装成 206）
        return _full()
    lo_s, hi_s = m.group(1), m.group(2)
    if lo_s == "" and hi_s == "":
        return _full()
    if lo_s == "":                       # bytes=-N：末 N 字节
        start = max(0, size - int(hi_s))
        end = size - 1
    else:
        start = int(lo_s)
        end = int(hi_s) if hi_s else size - 1
    if start >= size or end < start:
        return Response(
            status_code=416, headers={**total_hdr, "content-range": f"bytes */{size}"}
        )
    end = min(end, size - 1)
    with path.open("rb") as fh:
        fh.seek(start)
        chunk = fh.read(end - start + 1)
    return Response(
        content=chunk,
        status_code=206,
        headers={
            **total_hdr,
            "content-range": f"bytes {start}-{end}/{size}",
            "content-length": str(len(chunk)),
        },
    )


@app.get("/gw/{path:path}", include_in_schema=False)
def gateway_get(path: str, request: Request) -> JSONResponse:
    """只读转发到 M6。存在的唯一理由是**同源**：省掉给 M6 放开 CORS。

    只允许 GET 且限 `v1/` 前缀 —— 一个无限制的转发器等于把 M9 变成任意 URL 抓手，
    那不是在集成，是在开洞。转发失败一律 502 并说明上游是谁（不静默、不假装 200）。
    """
    if not any(path.startswith(p) for p in GATEWAY_ALLOWED_PREFIXES):
        raise HTTPException(
            404, f"只转发 {'/'.join('/' + p for p in GATEWAY_ALLOWED_PREFIXES)} 下的只读接口：{path}")
    url = f"{GATEWAY_BASE}/{path}"
    if request.url.query:
        url += f"?{request.url.query}"
    try:
        with urllib.request.urlopen(url, timeout=GATEWAY_TIMEOUT_S) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 上游的语义化状态码（404/422…）**原样透传**：页面要能区分"路段不存在"与"代理坏了"
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise HTTPException(exc.code, detail=f"上游 M6 返回 {exc.code}：{detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HTTPException(
            502, detail=f"取不到上游数据：M6（{GATEWAY_BASE}）不可达 —— {exc}") from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(502, detail=f"上游返回的不是 JSON：{exc}") from exc
    return JSONResponse(payload)


@app.get("/import", response_class=HTMLResponse, include_in_schema=False)
def import_page() -> HTMLResponse:
    """设计数据导入页。取数入口是 M2（见 INGEST_BASE 上面那段为什么不经 /gw）。"""
    if not IMPORT_PAGE.exists():
        raise HTTPException(500, f"页面文件缺失：{IMPORT_PAGE}")
    return HTMLResponse(IMPORT_PAGE.read_text(encoding="utf-8"))


@app.post("/v1/design/import", include_in_schema=False)
async def design_import_forward(
    files: list[UploadFile] = File(...),
    section_id: int = Form(...),
    dry_run: bool = Form(False),
) -> JSONResponse:
    """把上传的设计文件转给 M2。**这一步只做搬运与错误翻译，不做解析。**

    为什么要有这一层：页面与 M9 同源，省掉给 M2 放开 CORS —— 与 /gw 同一个理由。
    但**语义完全不同**：/gw 是只读转发，这里是写，所以它单独一条路由、单独一个
    上游地址（INGEST_BASE），不复用 /gw 的前缀白名单。

    错误翻译的原则与 /gw 一致：上游的语义化状态码**原样透传**
    （400 = 你传的文件有问题，页面要能显示原因），只有"够不着上游"才是 502。
    """
    if not files:
        raise HTTPException(400, "没有收到任何文件")
    # 逐个读出来再转发。**这一层不做后缀/重名/体量判断** —— 那是 M2 的职责，
    # 判两遍就会出现"两层规则慢慢不一致"。这里只做 M9 该做的两件事：
    # 搬运，以及把"够不着上游"翻译成 502。
    payload: list[tuple[str, bytes]] = []
    for f in files:
        data = await f.read()
        if not data:
            raise HTTPException(400, f"文件是空的（0 字节）：{f.filename}")
        payload.append((pathlib.Path(f.filename or "upload").name, data))
    try:
        async with httpx.AsyncClient(timeout=INGEST_TIMEOUT_S) as client:
            resp = await client.post(
                f"{INGEST_BASE}/v1/design/import",
                files=[("files", (name, data)) for name, data in payload],
                data={"section_id": str(section_id),
                      "dry_run": "true" if dry_run else "false"},
            )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
        raise HTTPException(
            502, detail=f"够不着 M2 接入服务（{INGEST_BASE}）—— {type(exc).__name__}: {exc}") from exc

    try:
        payload = resp.json()
    except ValueError as exc:
        raise HTTPException(502, detail=f"上游返回的不是 JSON：{resp.text[:300]}") from exc

    if resp.status_code >= 400:
        # 原样透传状态码与 detail —— 页面据此区分"文件不对"（400）与"上游坏了"（502）
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        raise HTTPException(resp.status_code, detail=str(detail)[:600])
    return JSONResponse(payload)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
