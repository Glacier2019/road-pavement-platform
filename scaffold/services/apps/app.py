"""M8 应用层（FEM 力学孪生 / 承载力评估 / 养护决策 / 孪生可视化）—— 骨架

本模块认哪份契约
----------------
* **消费** M5 的诊断三元组（**只消费，不重算**）
* **消费** 契约③ DAO —— 唯一取数通道
* **产出** `contracts/apps/maintenance_plan.v0.1.schema.json`（养护决策建议）

关于「一个服务还是四个容器」
----------------------------
报告 §6.1 把 M8 列为**四条容器化条目**：
    #12 力学孪生 FEM ｜ #13 孪生可视化 CesiumJS ｜ #14 承载力评估 sklearn ｜ #15 养护决策 OR-Tools
本骨架**先用一个服务承载四个路由前缀**，理由是骨架期四者共享同一套机械层，
拆成四个容器只会让"接入五件套"重复四遍。D 组按需拆分即可——拆分不改变契约。
（#13 CesiumJS 是前端，最终必然独立成容器。）

依赖门禁
--------
本模块依赖 M5 的输出。M5 依赖 M4。**M4 未上线 ⇒ 本模块无可信输入 ⇒ 全部接口返回 503。**

业务层 TODO（D 组）
-------------------
* FEM：需设计院提供结构参数与本构模型（骨架期无法凭空生成）
* 承载力评估：需真实检测数据训练/标定
* 养护决策：需真实养护历史与造价约束才能定目标函数
* **动作必须人工确认后方可落库**（报告 §6.4）
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
LOG = logging.getLogger("m8-apps")

PG_DSN = os.getenv("PG_DSN", "postgresql://rp:rp_change_me@localhost:55432/road_pavement")
PROCESSOR = os.getenv("PROCESSOR", "m8-apps-skeleton")

MODULE_CONTRACTS = {
    "consumes": [
        "M5 诊断三元组（diagnosis_result）",
        "契约③ DAO（scaffold/packages/rpdao/）—— 唯一取数通道",
    ],
    "produces": ["contracts/apps/maintenance_plan.v0.1.schema.json"],
    "sub_containers": ["FEM 力学孪生", "孪生可视化 CesiumJS", "承载力评估", "养护决策"],
}


class State:
    def __init__(self) -> None:
        self.runs = 0
        self.blocked = 0
        self.last_error: str | None = None


STATE = State()
pool: ConnectionPool | None = None

# 本模块的诊断输入来自 M5 的产出；M5 只消费 truth_flag=true 的数据。
DIAGNOSIS_COUNT_SQL = "SELECT count(*) FROM diagnosis_result"


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
    title="M8 应用层（骨架）",
    version="0.1.0-skeleton",
    lifespan=lifespan,
)


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    pg_ok, pg_msg, diag = False, "", 0
    if pool is not None:
        try:
            with pool.connection(timeout=3) as conn, conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            pg_ok = True
            with pool.connection(timeout=3) as conn, conn.cursor() as cur:
                cur.execute(DIAGNOSIS_COUNT_SQL)
                diag = int(cur.fetchone()[0])
        except Exception as exc:  # noqa: BLE001
            pg_msg = str(exc)[:200]
    else:
        pg_msg = "连接池未初始化"

    body = {
        "service": "m8-apps",
        "status": "ok" if pg_ok else "degraded",
        "dependencies": {"postgres": {"ok": pg_ok, "detail": pg_msg}},
        "gate": {
            "diagnosis_rows": diag,
            "open": diag > 0,
            "detail": "" if diag else "无 M5 诊断结果（M4→M5 链未通），应用接口将返回 503",
        },
        "contracts": MODULE_CONTRACTS,
    }
    return JSONResponse(body, status_code=200 if pg_ok else 503)


@app.get("/metrics")
def metrics() -> str:
    lines = [
        "# TYPE rp_apps_runs_total counter",
        f"rp_apps_runs_total {STATE.runs}",
        "# TYPE rp_apps_blocked_total counter",
        f"rp_apps_blocked_total {STATE.blocked}",
        "# TYPE rp_apps_pg_up gauge",
        f"rp_apps_pg_up {1 if pool is not None else 0}",
    ]
    return "\n".join(lines) + "\n"


@app.get("/metrics", include_in_schema=False)
def metrics_plain() -> PlainTextResponse:  # pragma: no cover
    return PlainTextResponse(metrics())


def _gate() -> JSONResponse | None:
    """统一门禁：无诊断输入则拒绝出结论。"""
    if pool is None:
        return JSONResponse({"detail": "数据库不可用"}, status_code=503)
    try:
        with pool.connection(timeout=3) as conn, conn.cursor() as cur:
            cur.execute(DIAGNOSIS_COUNT_SQL)
            n = int(cur.fetchone()[0])
    except Exception:  # noqa: BLE001
        n = 0
    if n == 0:
        STATE.blocked += 1
        return JSONResponse(
            {"detail": "门禁未开：无 M5 诊断结果",
             "gate": "M4 真数据门 → M5 融合辨析 链路未通",
             "hint": "本模块依赖 M5 输出，请先完成 P1(M4) 与 P2(M5)"},
            status_code=503,
        )
    return None


def _todo(what: str, owner: str = "D 组（应用决策）") -> JSONResponse:
    return JSONResponse(
        {"detail": "未实现", "what": what, "owner": owner,
         "note": "动作类接口必须人工确认后才落库；契约见 MODULE_CONTRACTS"},
        status_code=501,
    )


@app.post("/v1/fem/run")
def fem_run() -> JSONResponse:
    """力学孪生正演。TODO(D 组)：需设计院提供结构参数与本构模型。"""
    blocked = _gate()
    return blocked if blocked else _todo("FEM 力学孪生正演")


@app.post("/v1/capacity/assess")
def capacity_assess() -> JSONResponse:
    """承载力评估。TODO(D 组)：需真实检测数据标定。"""
    blocked = _gate()
    return blocked if blocked else _todo("承载力评估")


@app.post("/v1/maintenance/plan")
def maintenance_plan() -> JSONResponse:
    """养护决策。TODO(D 组)：需真实养护历史与造价约束定目标函数；产出须人工确认。"""
    blocked = _gate()
    return blocked if blocked else _todo("养护决策（OR-Tools），产出须人工确认后落库")


@app.get("/v1/twin/view")
def twin_view() -> JSONResponse:
    """孪生可视化数据端点（CesiumJS 前端消费）。TODO(D 组)。"""
    blocked = _gate()
    return blocked if blocked else _todo("孪生可视化数据端点")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
