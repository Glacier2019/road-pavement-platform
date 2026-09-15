"""M5 融合辨析引擎 —— 骨架（机械层已就位，业务逻辑留 TODO）

本模块认哪份契约
----------------
* **消费** 契约③ DAO（`scaffold/packages/rpdao/`）—— 只经 DAO 取数，**禁止直连数据库**
* **消费** M4 的质量门输出（**只处理已晋升为真值的数据**）
* **产出** `contracts/fusion/diagnosis.v0.1.schema.json`（诊断三元组）

启动门禁（不得绕过）
--------------------
报告 §6.4：「下游不启动，直到上游的两个契约测试都绿。」
本模块的额外门禁是 **M4 未上线前不宜启动**——融合辨析依赖质量门筛出的可信数据。
骨架期 `data_import_batch.truth_flag` 全为 `false`，即**当前没有任何一批数据够格进入本模块**。
所以本模块的接口在真值缺失时返回 503 并说明原因，而不是拿未晋升的数据算出"诊断结论"。

业务层 TODO（C 组）
-------------------
* 桩号锚定基准
* IRI → RQI 反演标定
* 荷载-响应温度修正
* 实验室真值 ↔ 现场互校
以上四项**本质都是标定工作**，必须用真实数据，不能用合成数据先"跑通"就算完成。
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
LOG = logging.getLogger("m5-fusion")

PG_DSN = os.getenv("PG_DSN", "postgresql://rp:rp_change_me@localhost:55432/road_pavement")
PROCESSOR = os.getenv("PROCESSOR", "m5-fusion-skeleton")

MODULE_CONTRACTS = {
    "consumes": [
        "契约③ DAO（scaffold/packages/rpdao/）—— 唯一取数通道",
        "M4 真值晋升后的批次（truth_flag = true）",
    ],
    "produces": [
        "contracts/fusion/diagnosis.v0.1.schema.json",
        "表 diagnosis_result（契约② DDL v0.1）",
    ],
}


class State:
    def __init__(self) -> None:
        self.fused = 0
        self.blocked = 0          # 因无真值数据而被门禁挡下的次数
        self.last_error: str | None = None


STATE = State()
pool: ConnectionPool | None = None


# 真值门禁查询：本模块只认 truth_flag = true 的批次。
# 刻意不提供"读全部"的开关——一旦有旁路，门禁就形同虚设。
TRUTH_BATCH_SQL = """
SELECT batch_no, raw_rows, valid_rows
FROM data_import_batch
WHERE truth_flag IS TRUE
ORDER BY batch_no DESC
LIMIT %s
"""


def trusted_batches(limit: int = 10) -> list[dict[str, Any]]:
    if pool is None:
        return []
    with pool.connection(timeout=3) as conn, conn.cursor() as cur:
        cur.execute(TRUTH_BATCH_SQL, (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


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
    title="M5 融合辨析引擎（骨架）",
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

    try:
        trusted = trusted_batches(50)
    except Exception as exc:  # noqa: BLE001
        trusted = []
        pg_msg = pg_msg or str(exc)[:200]

    body = {
        "service": "m5-fusion",
        "status": "ok" if pg_ok else "degraded",
        "dependencies": {"postgres": {"ok": pg_ok, "detail": pg_msg}},
        "gate": {
            # 门禁状态是本模块健康度的一部分：没有可信数据 = 无法工作，不是"空闲"
            "trusted_batches": len(trusted),
            "open": bool(trusted),
            "detail": "" if trusted
            else "无 truth_flag=true 的批次（M4 真数据门未上线），融合接口将返回 503",
        },
        "contracts": MODULE_CONTRACTS,
    }
    return JSONResponse(body, status_code=200 if pg_ok else 503)


@app.get("/metrics")
def metrics() -> str:
    lines = [
        "# TYPE rp_fusion_diagnoses_total counter",
        f"rp_fusion_diagnoses_total {STATE.fused}",
        "# TYPE rp_fusion_blocked_total counter",
        f"rp_fusion_blocked_total {STATE.blocked}",
        "# TYPE rp_fusion_pg_up gauge",
        f"rp_fusion_pg_up {1 if pool is not None else 0}",
    ]
    return "\n".join(lines) + "\n"


@app.get("/metrics", include_in_schema=False)
def metrics_plain() -> PlainTextResponse:  # pragma: no cover
    return PlainTextResponse(metrics())


def _todo(what: str, owner: str = "C 组（融合诊断）") -> JSONResponse:
    return JSONResponse(
        {"detail": "未实现", "what": what, "owner": owner,
         "note": "业务逻辑需真实数据标定后实现；契约见 MODULE_CONTRACTS"},
        status_code=501,
    )


@app.get("/v1/trusted-batches")
def list_trusted_batches(limit: int = 10) -> dict[str, Any]:
    """列出够格进入本模块的批次（truth_flag = true）。

    这是**门禁的可观测面**：骨架期这里必然是空列表，因为晋升从未发生过。
    """
    if pool is None:
        return JSONResponse({"detail": "数据库不可用"}, status_code=503)
    items = trusted_batches(limit)
    return {"count": len(items), "items": items,
            "note": "空列表＝M4 真数据门尚未上线，属骨架期预期状态"}


@app.post("/v1/fuse")
def fuse() -> JSONResponse:
    """对一个可信批次跑融合辨析，产出诊断三元组。

    TODO(C 组)：桩号锚定 → IRI/RQI 反演 → 温度修正 → 输出 diagnosis_result。
    真值缺失时返回 503——**不得拿未晋升的数据出诊断结论**。
    """
    try:
        trusted = trusted_batches(1)
    except Exception:  # noqa: BLE001
        trusted = []
    if not trusted:
        STATE.blocked += 1
        return JSONResponse(
            {"detail": "门禁未开：无 truth_flag=true 的批次",
             "gate": "M4 真数据门未上线",
             "hint": "本模块依赖质量门筛出的可信数据，请先完成 P1 的 M4"},
            status_code=503,
        )
    return _todo("融合辨析（桩号锚定 / 反演 / 温度修正）")


@app.get("/v1/diagnoses")
def list_diagnoses() -> JSONResponse:
    """诊断结果列表。TODO(C 组)。"""
    return _todo("诊断三元组查询")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
