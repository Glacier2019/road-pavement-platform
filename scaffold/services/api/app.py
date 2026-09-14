"""M6 统一数据出口（骨架版）——所有上层应用只经此服务取数/提交动作。

约定（对齐报告第十章 10.5 的"对象—函数—动作"三层）：
  · 对象查询：GET /v1/objects/{object_type}            ← 对应本体对象类型
  · 指标查询：GET /v1/metrics/...
  · 动作提交：POST /v1/actions/{action_name}           ← 受治理事务（P3 落地，先占位 501）
契约真源：本服务的 /openapi.json（contracts/openapi/m6-gateway.v0.1.yaml 是它的手写摘要）
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

PG_DSN = os.getenv("PG_DSN", "postgresql://rp:rp_change_me@localhost:55432/road_pavement")
pool = ConnectionPool(PG_DSN, min_size=1, max_size=6, open=False, timeout=15,
                      kwargs={"application_name": "rp-api"})

# 对象类型白名单（= 本体对象清单的雏形；越白名单即越界，返回 404）
OBJECT_TABLES: dict[str, str] = {
    "road_line": "road_line",
    "road_section": "road_section",
    "structure_layer": "structure_layer",
    "monitor_cross_section": "monitor_cross_section",
    "sensor_install": "sensor_install",
    "sensor_channel": "sensor_channel",
}

WIM_SQL = """
SELECT r.id, r.pass_time, r.lane_no, r.direction, r.axle_type_code, r.axle_num,
       r.speed_kmh, r.gross_weight_kg, r.overload_flag, r.overload_rate, r.esal,
       r.plate_no, r.quality_code, m.stake_text
FROM wim_axle_record r
LEFT JOIN monitor_cross_section m ON m.id = r.cross_section_id
WHERE (%(from_ts)s IS NULL OR r.pass_time >= %(from_ts)s)
  AND (%(to_ts)s   IS NULL OR r.pass_time <  %(to_ts)s)
  AND (%(stake)s   IS NULL OR m.stake_text = %(stake)s)
  AND (%(overload_only)s = false OR r.overload_flag = true)
ORDER BY r.pass_time DESC
LIMIT %(limit)s
"""

WIM_ONE_SQL = """
SELECT r.*, m.stake_text FROM wim_axle_record r
LEFT JOIN monitor_cross_section m ON m.id = r.cross_section_id
WHERE r.id = %(rid)s
"""

WIM_DETAIL_SQL = """
SELECT axle_seq, group_seq, axle_weight_kg, group_weight_kg, axle_dist_mm
FROM wim_axle_detail WHERE record_id = %(rid)s ORDER BY axle_seq
"""

WIM_DAILY_SQL = """
SELECT date_trunc('hour', pass_time)                            AS bucket,
       count(*)                                                 AS passages,
       count(*) FILTER (WHERE overload_flag)                     AS overloaded,
       round(sum(esal)::numeric, 2)                              AS esal_sum,
       round(avg(speed_kmh)::numeric, 1)                          AS avg_speed_kmh,
       round(max(gross_weight_kg)::numeric, 0)                    AS max_gross_kg
FROM wim_axle_record
WHERE pass_time >= %(from_ts)s AND pass_time < %(to_ts)s
  AND (%(stake)s IS NULL OR cross_section_id IN (
        SELECT id FROM monitor_cross_section WHERE stake_text = %(stake)s))
GROUP BY 1 ORDER BY 1
"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open()
    yield
    pool.close()


app = FastAPI(
    title="M6 统一数据出口（骨架版）",
    version="0.1.0",
    description="对象查询 / 指标查询 / 动作提交三类接口。上层（Grafana、Copilot、课题组脚本）"
                "一律通过本服务访问数据，不允许直连库。",
    lifespan=lifespan,
)


def q(sql: str, params: dict[str, Any], one: bool = False):
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone() if one else cur.fetchall()


@app.get("/healthz", tags=["运维"])
def healthz() -> dict[str, Any]:
    try:
        q("SELECT 1", {})
        return {"status": "ok", "postgres": True}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "postgres": False, "error": str(exc)[:200]}


# ----------------------------------------------------------------- 对象查询
# ⚠ 注册顺序 = 匹配顺序（Starlette 取第一个 FULL match）。静态路径必须排在参数化路径
#   /v1/objects/{object_type} 之前，否则 /v1/objects/wim_axle 会被它吃掉、永远返回 404。
#   新增对象查询端点时，一律加在下面这一段（list_objects 之前）。
#   契约：contracts/openapi/m6-gateway.v0.1.yaml；回归保护：tests/contract/test_api_routes.py
@app.get("/v1/objects/wim_axle", tags=["对象查询"],
         summary="WIM 过车记录（按时间/桩号/超载筛选）")
def list_wim(
    from_ts: datetime | None = None,
    to_ts: datetime | None = None,
    stake: str | None = Query(None, description="如 K4640+000"),
    overload_only: bool = False,
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    rows = q(WIM_SQL, {"from_ts": from_ts, "to_ts": to_ts, "stake": stake,
                       "overload_only": overload_only, "limit": limit})
    return {"object_type": "wim_axle_record", "count": len(rows), "items": rows}


@app.get("/v1/objects/wim_axle/{record_id}", tags=["对象查询"],
         summary="单条过车记录 + 轴明细（对象-链接展开）")
def get_wim(record_id: int) -> dict[str, Any]:
    row = q(WIM_ONE_SQL, {"rid": record_id}, one=True)
    if row is None:
        raise HTTPException(404, f"记录不存在：{record_id}")
    row["axles"] = q(WIM_DETAIL_SQL, {"rid": record_id})
    return row


# ↓ 参数化路由：必须排在所有 /v1/objects/<具体名> 之后
@app.get("/v1/objects/{object_type}", tags=["对象查询"],
         summary="按对象类型查档案对象")
def list_objects(object_type: str, limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    table = OBJECT_TABLES.get(object_type)
    if table is None:
        raise HTTPException(404, f"未登记的对象类型：{object_type}（白名单见 /openapi.json）")
    rows = q(f"SELECT * FROM {table} ORDER BY id LIMIT %(limit)s", {"limit": limit})
    return {"object_type": object_type, "count": len(rows), "items": rows}


# ----------------------------------------------------------------- 指标查询
@app.get("/v1/metrics/wim_hourly", tags=["指标查询"],
         summary="小时级过车量/超载数/ESAL/均速")
def wim_hourly(
    day: date | None = None,
    stake: str | None = None,
) -> dict[str, Any]:
    d = day or date.today()
    rows = q(WIM_DAILY_SQL, {
        "from_ts": datetime.combine(d, datetime.min.time()),
        "to_ts": datetime.combine(d, datetime.max.time()),
        "stake": stake,
    })
    total = sum(r["passages"] for r in rows)
    overloaded = sum(r["overloaded"] for r in rows)
    return {
        "date": d.isoformat(), "stake": stake,
        "passages": total, "overloaded": overloaded,
        "overload_ratio": round(overloaded / total, 4) if total else None,
        "esal_sum": float(sum(r["esal_sum"] or 0 for r in rows)),
        "buckets": rows,
    }


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
                    f"实现前不得由任何自动化流程调用")
