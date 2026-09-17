"""M7 语义中枢 · 接入 Copilot —— 骨架（机械层已就位，业务逻辑留 TODO）

本模块认哪份契约
----------------
* **消费** 真实业务表格（人工上传/课题组提供）
* **产出** `contracts/semantic/mapping_set.v0.1.schema.json`（映射集）
* **产出** 表 `mapping_set` —— ✅ 该表已于 2026-09-15 落入 **DDL v0.3**（H 段 H1，
  契约变更工单 #1）。在此之前它只存在于 JSON Schema 里，没有物理表。
  按纪律，新建表必须走**契约变更工单**（MODULE-ONBOARDING.md §5），不得就地改 DDL。
  本骨架因此**只定义 JSON 契约，不建表**；落库留到工单批准之后。

职责边界（务必看清，别做重了）
------------------------------
本模块要的是「**不对应**」两件事的能力：
  1. **表头不对应**：真实表格的列名 ≠ DDL 字段名（别名、缩写、含单位、双语…）
  2. **桩号不对应**：真实表格的里程表达 ≠ 库内桩号（K12+345 / 12345 / 12.345km…）

流程（报告 §7 定的四步，不得跳步）：
    探查 → 生成规则 → **人工确认** → 持久化
第 3 步「人工确认」是硬要求：映射错了会污染全库，不允许自动落库。

业务层 TODO（待指派责任人）
----------------------------
* 表头识别：别名/单位/中英混合的匹配策略
* 桩号归一化：多种里程表达 → 规范桩号
* `mapping_set` 表结构定稿 + 契约变更工单
以上都要**真实样表**才能做；骨架期无法预先猜出真实表头的变异形态。
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from psycopg_pool import ConnectionPool

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOG = logging.getLogger("m7-semantic")

PG_DSN = os.getenv("PG_DSN", "postgresql://rp:rp_change_me@localhost:55432/road_pavement")
PROCESSOR = os.getenv("PROCESSOR", "m7-semantic-skeleton")

MODULE_CONTRACTS = {
    "consumes": ["真实业务表格（课题组提供，非合成）"],
    "produces": ["contracts/semantic/mapping_set.v0.1.schema.json"],
    "pending": [
        "表 mapping_set 未在冻结 DDL 中——需契约变更工单批准后才可落库",
    ],
}


class State:
    def __init__(self) -> None:
        self.probes = 0
        self.confirmed = 0
        self.last_error: str | None = None


STATE = State()
pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    try:
        pool = ConnectionPool(PG_DSN, min_size=1, max_size=4, open=True, timeout=5)
        LOG.info("已连接 PostgreSQL")
    except Exception as exc:  # noqa: BLE001
        LOG.error("连接 PostgreSQL 失败：%s", exc)
        pool = None
    yield
    if pool is not None:
        pool.close()


app = FastAPI(
    title="M7 语义中枢 · 接入 Copilot（骨架）",
    version="0.1.0-skeleton",
    lifespan=lifespan,
)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    pg_ok, pg_msg = False, ""
    if pool is not None:
        try:
            with pool.connection(timeout=3) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            pg_ok = True
        except Exception as exc:  # noqa: BLE001
            pg_msg = str(exc)[:200]
    else:
        pg_msg = "连接池未初始化"

    # mapping_set 表是否已由工单批准建好——这是本模块的前置条件之一
    has_table = False
    if pg_ok:
        try:
            with pool.connection(timeout=3) as conn, conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.mapping_set') IS NOT NULL")
                has_table = bool(cur.fetchone()[0])
        except Exception:  # noqa: BLE001
            has_table = False

    body = {
        "service": "m7-semantic",
        "status": "ok" if pg_ok else "degraded",
        "dependencies": {
            "postgres": {"ok": pg_ok, "detail": pg_msg},
            "mapping_set_table": {
                "ok": has_table,
                "detail": "" if has_table
                else "表不存在（需契约变更工单批准建表，见 MODULE_CONTRACTS.pending）",
            },
        },
        "contracts": MODULE_CONTRACTS,
    }
    return JSONResponse(body, status_code=200 if pg_ok else 503)


@app.get("/metrics")
def metrics() -> str:
    lines = [
        "# TYPE rp_semantic_probes_total counter",
        f"rp_semantic_probes_total {STATE.probes}",
        "# TYPE rp_semantic_confirmed_total counter",
        f"rp_semantic_confirmed_total {STATE.confirmed}",
        "# TYPE rp_semantic_pg_up gauge",
        f"rp_semantic_pg_up {1 if pool is not None else 0}",
    ]
    return "\n".join(lines) + "\n"


@app.get("/metrics", include_in_schema=False)
def metrics_plain() -> PlainTextResponse:  # pragma: no cover
    return PlainTextResponse(metrics())


def _todo(what: str, owner: str = "待指派（M7 尚无责任人）") -> JSONResponse:
    return JSONResponse(
        {"detail": "未实现", "what": what, "owner": owner,
         "note": "需真实样表；契约见 MODULE_CONTRACTS"},
        status_code=501,
    )


@app.post("/v1/mappings/probe")
def probe() -> JSONResponse:
    """第 1 步：探查——读真实表格的表头与桩号，产出**候选**映射（不落库）。

    TODO(责任人)：表头别名/单位/中英匹配；桩号表达归一化。
    """
    return _todo("探查真实表格，产出候选映射（表头 + 桩号）")


@app.get("/v1/mappings")
def list_mappings() -> JSONResponse:
    """列出已有的映射集（含未确认的候选）。TODO(责任人)。"""
    return _todo("映射集查询")


@app.post("/v1/mappings/{mapping_id}/confirm")
def confirm(mapping_id: str) -> JSONResponse:
    """第 3 步：人工确认后持久化。

    TODO(责任人)：确认人必填、留审计；**确认前不得被 M2/M4 引用**（映射错了会污染全库）。
    """
    return _todo(f"人工确认映射集 {mapping_id} 并落库")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
