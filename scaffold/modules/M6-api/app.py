"""M6 统一数据出口（骨架版）——所有上层应用只经此服务取数/提交动作。

约定（对齐报告第十章 10.5 的"对象—函数—动作"三层）：
  · 对象查询：GET /v1/objects/{object_type}            ← 对应本体对象类型
  · 指标查询：GET /v1/metrics/...
  · 动作提交：POST /v1/actions/{action_name}           ← 受治理事务（P3 落地，先占位 501）
契约真源：本服务的 /openapi.json（contracts/openapi/m6-gateway.v0.2.yaml 是它的手写摘要）

==================== M3 落地后的变化（2026-09-15）====================
本服务**不再 import psycopg、不再持有连接池、不再内联 SQL**，全部改为调用 M3（rpdao）。
意义：报告 6.1 那条「应用不直连存储」原先只是约定——骨架期本文件自己持有
ConnectionPool，架构上写着不许、代码上却只能这么写。现在它变成**结构上做不到**：
拿不到 cursor，就没有直连库的能力。
SQL、表名白名单、指标口径（求和/占比）现在都在 M3 内，改口径不用动出口服务。
===================================================================
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query

from rpdao import (
    ALL_TABLES,
    CROSS_TABLES,
    DOMAINS,
    ContractViolation,
    Dao,
    DaoError,
    NotFound,
    StorageUnavailable,
    UnknownDomain,
    UnknownTable,
)

dao = Dao(os.getenv("PG_DSN"), app_name="rp-api")

# 对象类型白名单 = 7 大对象域**已建**的物理表（共 21 张），来自 M3 的域目录。
# 跨域支撑表（字典/质量日志/批次台账等 9 张）刻意不在此列：它们是治理设施，
# 不是本体对象，不应经"对象查询"暴露。越白名单即越界 → 404。
OBJECT_TYPES: dict[str, str] = {
    table: code for code, d in DOMAINS.items() for table in d.tables
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    dao.open()
    yield
    dao.close()


app = FastAPI(
    title="M6 统一数据出口（骨架版）",
    version="0.2.0",
    description="对象查询 / 指标查询 / 动作提交三类接口。上层（Grafana、Copilot、课题组脚本）"
                "一律通过本服务访问数据，不允许直连库。数据访问全部经 M3（rpdao）。",
    lifespan=lifespan,
)


def _http(exc: DaoError) -> HTTPException:
    """把 M3 的语义化异常翻成 HTTP。DAO 不依赖 FastAPI，这层翻译是 M6 的职责。"""
    if isinstance(exc, NotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, (UnknownTable, UnknownDomain)):
        return HTTPException(404, str(exc))
    if isinstance(exc, ContractViolation):
        return HTTPException(400, str(exc))
    if isinstance(exc, StorageUnavailable):
        return HTTPException(503, f"存储不可用：{exc}")
    return HTTPException(500, str(exc))


@app.get("/healthz", tags=["运维"])
def healthz() -> dict[str, Any]:
    ok = dao.ping()
    return {"status": "ok" if ok else "degraded", "postgres": ok, **dao.pool_stats()}


@app.get("/v1/catalog", tags=["运维"],
         summary="对象域目录（M3 契约③ 在线视图）")
def catalog() -> dict[str, Any]:
    """把 M3 的域目录直接暴露出来：前端/课题组据此知道有哪些对象类型可取。"""
    return {
        "domains": [
            {
                "code": d.code, "name": d.name, "storage": d.storage,
                "tables": list(d.tables),
                "logical_only": list(d.logical),
            }
            for d in DOMAINS.values()
        ],
        "cross_tables": list(CROSS_TABLES),
        "object_types": sorted(OBJECT_TYPES),
    }


# ----------------------------------------------------------------- 对象查询
# ⚠ 注册顺序 = 匹配顺序（Starlette 取第一个 FULL match）。静态路径必须排在参数化路径
#   /v1/objects/{object_type} 之前，否则 /v1/objects/wim_axle 会被它吃掉、永远返回 404。
#   新增对象查询端点时，一律加在下面这一段（list_objects 之前）。
#   契约：contracts/openapi/m6-gateway.v0.2.yaml；回归保护：tests/contract/test_api_routes.py
@app.get("/v1/objects/wim_axle", tags=["对象查询"],
         summary="WIM 过车记录（按时间/桩号/超载筛选）")
def list_wim(
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    station: str | None = Query(None, description="如 K4640+000"),
    overload_only: bool = False,
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    rows = dao.lo.passages(from_ts=from_ts, to_ts=to_ts, station=station,
                           overload_only=overload_only, limit=limit)
    return {"object_type": "wim_axle_record", "count": len(rows), "items": rows}


@app.get("/v1/objects/wim_axle/{record_id}", tags=["对象查询"],
         summary="单条过车记录 + 轴明细（对象-链接展开）")
def get_wim(record_id: int) -> dict[str, Any]:
    try:
        return dao.lo.passage(record_id)
    except DaoError as exc:
        raise _http(exc) from exc


# ↓ 参数化路由：必须排在所有 /v1/objects/<具体名> 之后
@app.get("/v1/objects/{object_type}", tags=["对象查询"],
         summary="按对象类型查档案对象")
def list_objects(object_type: str, limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    code = OBJECT_TYPES.get(object_type)
    if code is None:
        raise HTTPException(404, f"未登记的对象类型：{object_type}（白名单见 /v1/catalog）")
    try:
        rows = dao.domain(code).list_objects(object_type, limit=limit)
    except DaoError as exc:
        raise _http(exc) from exc
    return {"object_type": object_type, "domain": code, "count": len(rows), "items": rows}


# ----------------------------------------------------------------- 指标查询
@app.get("/v1/metrics/wim_hourly", tags=["指标查询"],
         summary="小时级过车量/超载数/ESAL/均速")
def wim_hourly(day: date | None = None, station: str | None = None) -> dict[str, Any]:
    try:
        return dao.lo.daily_summary(day or date.today(), station=station)
    except DaoError as exc:
        raise _http(exc) from exc


# ----------------------------------------------------------------- 动作层（占位）
@app.post("/v1/actions/{action_name}", tags=["动作提交"], status_code=501,
          summary="受治理动作提交（骨架期占位）")
def submit_action(action_name: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """P3 落地。动作 = 校验 + 权限 + 副作用 + 写回 + 审计，必须人工确认。

    计划实现（报告 10.4 ③）：create_maintenance_ticket / dispatch_alert /
    confirm_event / mark_data_quality_issue / request_design_change
    """
    raise HTTPException(
        501, detail=f"动作层尚未实现（{action_name}）；按报告 10.4 ③ 于 P3 落地，"
                    f"需先有 M4 治理门与写回审计。"
    )
