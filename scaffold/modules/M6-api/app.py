"""M6 统一数据出口（骨架版）——所有上层应用只经此服务取数/提交动作。

约定（对齐报告第十章 10.5 的"对象—函数—动作"三层）：
  · 对象查询：GET /v1/objects/{object_type}            ← 对应本体对象类型
  · 几何查询：GET /v1/geometry/...                     ← GE 域按路段取子树（工单 #4）
  · 指标查询：GET /v1/metrics/...
  · 动作提交：POST /v1/actions/{action_name}           ← 受治理事务（P3 落地，先占位 501）
契约真源：本服务的 /openapi.json（contracts/openapi/m6-gateway.v0.3.yaml 是它的手写摘要）

==================== M3 落地后的变化（2026-09-15）====================
本服务**不再 import psycopg、不再持有连接池、不再内联 SQL**，全部改为调用 M3（rpdao）。
意义：报告 6.1 那条「应用不直连存储」原先只是约定——骨架期本文件自己持有
ConnectionPool，架构上写着不许、代码上却只能这么写。现在它变成**结构上做不到**：
拿不到 cursor，就没有直连库的能力。
SQL、表名白名单、指标口径（求和/占比）现在都在 M3 内，改口径不用动出口服务。
===================================================================
"""
from __future__ import annotations

import json
import os
import pathlib
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query

import gaps

from rpdao import (
    ALL_TABLES,
    CROSS_TABLES,
    DOMAINS,
    TABLE_OWNER,
    ContractViolation,
    Dao,
    DaoError,
    NotFound,
    StorageUnavailable,
    UnknownDomain,
    UnknownTable,
    domain_of,
)

dao = Dao(os.getenv("PG_DSN"), app_name="rp-api")

# 对象类型白名单 = 7 大对象域**已建**的物理表（共 31 张），来自 M3 的域目录。
# 跨域支撑表（字典/质量日志/批次台账等 11 张）刻意不在此列：它们是治理设施，
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
    version="0.3.0",
    description="对象查询 / 几何查询 / 指标查询 / 动作提交四类接口。上层（Grafana、Copilot、课题组脚本）"
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


@app.get("/v1/catalog/tables", tags=["运维"],
         summary="逐张逻辑表盘点：行数 / 分区根表 / 是否已有数据")
def catalog_tables() -> dict[str, Any]:
    """**62 张逻辑表逐张数行数** —— 回答「平台上到底有哪些表、哪些有数据」。

    这个接口存在的理由，是一个很容易答错的问题：「数据库里那么多表，
    是不是每张都该在管理台上显示出来？」

    答案是否，而且理由分两层，缺一层都会把控制台做坏：

      ① **物理表 ≠ 逻辑表**。``wim_axle_record`` 是声明式分区表，物理上落成
         1 个父表 ＋ 39 个月分区 ＋ 1 个兜底分区。若照物理表逐张列，界面会多出
         40 行**恒空**且无法理解的名字（``wim_axle_record_p202511``）。
         分区是 PostgreSQL 的实现细节，不是平台的对象类型 —— 平台对外只有
         「WIM 过车记录」这一张，它按时间落进不同格子里。
      ② **有数据的和没数据的必须分开看**。没有数据不是「表坏了」，
         是「这条链路还没接」。把两者混在一张平表里，读的人分不清
         「本来就没有」与「应该有却没有」—— 而后者才是要修的东西。

    所以返回里同时给出：逐表行数、它属于哪个域、是否为分区根表（附分区数与
    分区键）、以及汇总计数。**判「有没有数据」用行数 > 0，不用非空值占比** ——
    后者会把「有 1 行但该行某列为空」误判成没数据，且口径随列而变，无法比较。

    本接口只读、只数数，不返回任何行内容；取行内容请走 /v1/objects/...。
    """
    try:
        rows = dao.table_census()
    except DaoError as exc:
        raise _http(exc) from exc

    # 域归属直接问 M3 的目录，**不在这里再维护一份映射** ——
    # 多一份映射就多一次「DDL 加了表、这里忘了加」的机会。
    items = []
    for r in rows:
        table = r["table_name"]
        items.append({
            "table": table,
            "domain": domain_of(table),          # 跨域支撑表为 null，是正常的
            "row_count": int(r["row_count"]),
            "has_data": int(r["row_count"]) > 0,
            "is_partitioned": r["is_partitioned"],
            "partition_key": r["partition_key"],
            "partition_count": r["partition_count"],
        })

    with_data = [i for i in items if i["has_data"]]
    partitioned = [i for i in items if i["is_partitioned"]]
    return {
        "logical_table_count": len(items),
        "with_data_count": len(with_data),
        "empty_count": len(items) - len(with_data),
        # 分区数单列：它**不是**表数的一部分，是同一张逻辑表的物理格子数
        "physical_partition_count": sum(i["partition_count"] for i in partitioned),
        "items": items,
    }


#: M2 的段能力出口。归因要用它的三件事：段实现了没、源收到没、段→哪些表。
#  ★ 经 HTTP 取，不 import —— M6 与 M2 之间只认契约（硬线 II）。
M2_BASE = os.getenv("M2_BASE", "http://ingest:8000").rstrip("/")
#: 取 M2 的超时。给得短一点：归因是给人看的，宁可报"取不到"也不要页面卡住。
GET_TIMEOUT_S = float(os.getenv("M2_TIMEOUT_S", "5"))


def _m2_capabilities() -> dict[str, Any]:
    """取 M2 的段能力。失败时**抛 503 并点名 M2**，不吞。

    为什么必须点名：M3 挂了是"所有数据都拿不到"，M2 挂了是"只有归因算不全"，
    两者的排查方向完全不同。混成一句"服务不可用"会让人查错方向。
    """
    import urllib.error
    import urllib.request

    url = f"{M2_BASE}/v1/design/capabilities"
    try:
        with urllib.request.urlopen(url, timeout=GET_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise HTTPException(
            503, f"取不到 M2 的段能力（{url}）：{exc} —— 缺口分类无法完成") from exc


def _module_phases() -> dict[str, str]:
    """模块登记表里的 phase。

    ⚠ 取自 M9 的登记文件 —— 它是**唯一真源**，这里不另抄一份到代码里。
    读不到就返回空：phase 只是次要字段，不该拖垮整个归因接口。
    """
    p = pathlib.Path(os.getenv("MODULES_YAML", "/app/modules.yaml"))
    if not p.is_file():
        return {}
    try:
        import yaml
        doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return {}
    return {m.get("module"): str(m.get("phase"))
            for m in (doc.get("modules") or [])
            if m.get("module") and m.get("phase") is not None}


@app.get("/v1/catalog/gaps", tags=["运维"],
          summary="逐张逻辑表的缺口归因：为什么空 / 归谁 / 先动哪条")
def catalog_gaps(
    empty_only: bool = Query(False, description="true = 只返回空表"),
) -> dict[str, Any]:
    """**空表缺口归因** —— 从"哪些表是空的"推进到"每条空链该谁接"。

    ## 为什么这件事必须跨模块拼装

    归因需要三类事实，分属两个模块，谁也不能替谁：
      · 表里有几行 / 表的归属模块  → M3 rpdao（唯一接触存储处）
      · 段实现了没 / 源收到没      → M2（解析器与导入台账都在它那边）

    让 M3 去读磁盘、或让 M2 去查库都能更快写完，但都会破坏"平台内唯一接触
    存储处"这条线 —— 而那条线正是四组学生能并行开发的前提。

    ## ★ 最容易判错、也最要紧的一处

    「源在不在」**不是看磁盘**，是看 M2 的 design_file 台账。导入入口是上传，
    文件落在临时目录里、请求结束即销毁 —— 扫磁盘会恒得 false，把"源已收到"
    错判成"源缺失"，而且**不报错**。

    本接口只读；不返回任何行内容，也不提供写操作。
    """
    try:
        census = dao.table_census()
    except DaoError as exc:
        raise _http(exc) from exc

    caps = _m2_capabilities()

    # 段 → 表：**由 M2 给**（唯一入口）。M6 不自己推"段名==表名"这条约定 ——
    # 它有已登记的例外（cross_section 改名、design_control 一段九表），
    # 把约定写死在 M6 就等于又多一份会漂的陈述。
    seg_tables: dict[str, tuple[str, ...]] = {}
    seg_facts: dict[str, dict[str, Any]] = {}
    for s in caps.get("segments") or []:
        seg = s.get("segment")
        if not seg:
            continue
        seg_tables[seg] = tuple(s.get("tables") or (seg,))
        seg_facts[seg] = {
            "suffix": s.get("suffix") or "",
            "kind": s.get("kind") or "",
            "implemented": bool(s.get("implemented")),
            "source_state": s.get("source_state") or "absent",
            "source_file": s.get("source_file"),
            "source_note": s.get("source_note"),
            "effective_suffix": s.get("effective_suffix"),
        }

    result = gaps.build_gaps(
        census=[{"table_name": r["table_name"],
                 "domain": domain_of(r["table_name"]),
                 "row_count": r["row_count"],
                 "is_partitioned": r["is_partitioned"],
                 "partition_count": r["partition_count"]} for r in census],
        owners=dict(TABLE_OWNER),
        seg_tables=seg_tables,
        seg_facts=seg_facts,
        phases=_module_phases(),
    )
    if empty_only:
        result["items"] = [i for i in result["items"] if not i["has_data"]]
    return result


# ----------------------------------------------------------------- 对象查询
# ⚠ 注册顺序 = 匹配顺序（Starlette 取第一个 FULL match）。静态路径必须排在参数化路径
#   /v1/objects/{object_type} 之前，否则 /v1/objects/wim_axle 会被它吃掉、永远返回 404。
#   新增对象查询端点时，一律加在下面这一段（list_objects 之前）。
#   契约：contracts/openapi/m6-gateway.v0.3.yaml；回归保护：tests/contract/test_api_routes.py
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


# --------------------------------------------------------------- 几何查询（GE 域）
# 工单 #4。为什么 GE 要单独一组路由，而不是用 /v1/objects/{object_type} 平铺取：
#   GE 的数据是以 road_section 为根的一棵树（桩号/交点/线元全锚在路段上）。
#   平铺取「所有交点」在只有一个路段时看着没问题，路段一多就**静默串台** ——
#   把 A 路的交点混进 B 路的线形，而两条路的桩号都从 0 开始，混了看不出来。
# 本层只做两件事：转发筛选参数、把 M3 的语义化异常翻成 HTTP。SQL 一律在 M3。
@app.get("/v1/geometry/sections", tags=["几何查询"],
         summary="路段列表（含路线/设计项目/分段属性 + 三类几何计数）")
def list_sections() -> dict[str, Any]:
    try:
        rows = dao.ge.sections()
    except DaoError as exc:
        raise _http(exc) from exc
    return {"count": len(rows), "items": rows}


@app.get("/v1/geometry/sections/{section_id}/stations", tags=["几何查询"],
         summary="桩号序列（可按区间与是否整桩筛）")
def list_stations(
    section_id: int,
    from_km: float | None = Query(None, ge=0, description="起（路段内里程，km，含）"),
    to_km: float | None = Query(None, ge=0, description="止（路段内里程，km，含）"),
    integer_only: bool = Query(False, description="只要整桩"),
    limit: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    try:
        rows = dao.ge.stations(section_id, from_km=from_km, to_km=to_km,
                               integer_only=integer_only, limit=limit)
    except DaoError as exc:
        raise _http(exc) from exc
    return {"section_id": section_id, "count": len(rows), "items": rows}


@app.get("/v1/geometry/sections/{section_id}/alignment", tags=["几何查询"],
         summary="平面线形：交点链 + 线形单元链（一次取回）")
def get_alignment(section_id: int) -> dict[str, Any]:
    try:
        return dao.ge.alignment(section_id)
    except DaoError as exc:
        raise _http(exc) from exc


@app.get("/v1/geometry/sections/{section_id}/summary", tags=["几何查询"],
         summary="单条路段的概要 + 几何完整度报告")
def get_section_summary(section_id: int) -> dict[str, Any]:
    """`section` 答"这是哪条路"，`completeness` 答"这份数据够不够用、为什么"。

    ⚠ 给不出"哪个导入批次导的"：批次台账 `data_import_batch` **没有指回路段的列**
    （实测确认），只能给到**文件级**来源。不假装能给。
    """
    try:
        return {"section": dao.ge.section(section_id),
                "completeness": dao.ge.completeness(section_id)}
    except DaoError as exc:
        raise _http(exc) from exc


@app.get("/v1/geometry/sections/{section_id}/widths", tags=["几何查询"],
         summary="路幅宽度表（`.WID` 的变化点，按桩号）")
def get_widths(section_id: int) -> dict[str, Any]:
    """⚠ 返回的是**变化点**，不是逐桩宽度。

    一行 = 一个宽度分组的起点（实测毕设只有 4 行，而 `.WID` 覆盖 0–5701.461 m）。
    要拿"某个桩号的宽度"，调用方得自己按桩号取最后一行 —— 那是业务语义，
    网关不替它决定。本层只做两件事：转发、把 M3 的异常翻成 HTTP。
    """
    try:
        rows = dao.ge.widths(section_id)
    except DaoError as exc:
        raise _http(exc) from exc
    return {"section_id": section_id, "count": len(rows), "items": rows}


@app.get("/v1/geometry/sections/{section_id}/superelevation", tags=["几何查询"],
         summary="超高过渡（`.SUP` 的逐桩横坡控制点）")
def get_superelevation(section_id: int) -> dict[str, Any]:
    """⚠ 返回的是**控制点**，不是逐桩横坡。

    控制点之间横坡**线性渐变**，所以要拿某桩号的横坡必须插值；直接取最后一行是错的。
    源文件的 **9999 = 忽略此数据**（落库为 null），语义是"渐变**穿过**该点继续走"，
    插值时要跳过 null 找两侧最近的非 null。详见 M3 `GeRepository.superelevation`
    与契约里的说明 —— 这两条都有实测支撑，不是推测。
    """
    try:
        rows = dao.ge.superelevation(section_id)
    except DaoError as exc:
        raise _http(exc) from exc
    return {"section_id": section_id, "count": len(rows), "items": rows}


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
