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
import urllib.error
import urllib.parse
import urllib.request
from contextlib import asynccontextmanager
from typing import Any

import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

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
#: 只转发这些前缀。**这不是通用代理**：只有 GET，且必须落在 M6 的 API 命名空间内 ——
#: 目的是让页面与 M9 同源（不必给 M6 放开 CORS），而不是把 M9 变成任意转发器。
GATEWAY_ALLOWED_PREFIXES = ("v1/",)
GEOMETRY_PAGE = pathlib.Path(__file__).with_name("geometry.html")

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


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
