"""M10 Agent 执行引擎 —— 骨架（**二期**，本阶段不排期）

定位
----
报告 §七 把 M10 归 P4/P5（二期）。本骨架先建，目的是让**接口形状先冻结**，
各模块可以按此形状预留钩子；但**本阶段不排期、不投入人力**。

本模块认哪份契约
----------------
* **消费** 契约③ DAO + M6 OpenAPI（Agent 只能调对外接口，**不得直连库**）
* **产出** `contracts/agent/action_ticket.v0.1.schema.json`（动作工单）

必须守住的边界（否则这个模块会变成风险源）
------------------------------------------
1. **Agent 不得直连数据库**——对外只经 M6（MODULE-ONBOARDING §3 硬线 1）
2. **动作必须人工确认**——本模块产出的是**工单**，不是执行结果
3. **写入只经 M2**（硬线 2）——Agent 若要写数据，必须走 M2，禁止旁路 INSERT
4. 本模块**没有**绕过上述三条的配置开关

业务层 TODO（二期）
-------------------
* Agent 编排与工具调用
* 动作工单的审批流
* 与 M8 动作层（M6 的 501 占位）对接
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOG = logging.getLogger("m10-agent")

# 硬线：本模块唯一的对外通道是 M6。刻意做成常量，不给"直连库"的配置口子。
M6_BASE_URL = os.getenv("M6_BASE_URL", "http://api:8000")
PROCESSOR = os.getenv("PROCESSOR", "m10-agent-skeleton")

MODULE_CONTRACTS = {
    "consumes": ["M6 OpenAPI（唯一对外通道，禁止直连数据库）"],
    "produces": ["contracts/agent/action_ticket.v0.1.schema.json"],
    "phase": "二期（P4/P5），本阶段不排期",
}


class State:
    def __init__(self) -> None:
        self.tickets = 0
        self.last_error: str | None = None


STATE = State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    LOG.warning("M10 为二期模块，当前仅为接口骨架，无业务实现")
    yield


app = FastAPI(
    title="M10 Agent 执行引擎（骨架 · 二期）",
    version="0.1.0-skeleton",
    lifespan=lifespan,
)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    return {
        "service": "m10-agent",
        "status": "ok",
        "phase": "二期（P4/P5）—— 本阶段不排期，仅冻结接口形状",
        "dependencies": {
            "m6_gateway": {
                "ok": True,
                "detail": f"对外通道配置为 {M6_BASE_URL}（本骨架不实际调用）",
            }
        },
        "contracts": MODULE_CONTRACTS,
    }


@app.get("/metrics")
def metrics() -> str:
    return "\n".join([
        "# TYPE rp_agent_tickets_total counter",
        f"rp_agent_tickets_total {STATE.tickets}",
    ]) + "\n"


@app.get("/metrics", include_in_schema=False)
def metrics_plain() -> PlainTextResponse:  # pragma: no cover
    return PlainTextResponse(metrics())


@app.post("/v1/tickets")
def create_ticket() -> JSONResponse:
    """产出一个动作工单（**不是**执行动作）。

    TODO(二期)：工单需人工确认；确认后才由 M6 动作层执行。
    """
    return JSONResponse(
        {"detail": "未实现", "phase": "二期（P4/P5）",
         "what": "动作工单创建",
         "boundary": "Agent 不得直连库；写入只经 M2；动作必须人工确认",
         "owner": "D 组（二期）"},
        status_code=501,
    )


@app.get("/v1/tickets")
def list_tickets() -> JSONResponse:
    """工单列表。TODO(二期)。"""
    return JSONResponse(
        {"detail": "未实现", "phase": "二期（P4/P5）", "owner": "D 组（二期）"},
        status_code=501,
    )


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
