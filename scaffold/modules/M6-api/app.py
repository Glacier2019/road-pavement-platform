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

# ★★★ `NoResponseModel` 是本文件**最重要的一行配置**，别动它。
#   没有它，`-> dict[str, Any]` 会让 FastAPI 把返回交给 pydantic，于是每个
#   `numeric` 列都以**字符串**出去（实测：10/20 条路由中招）。见 jsonnum.py。
from jsonnum import NoResponseModel

from rpdao import (
    ALL_TABLES,
    CROSS_TABLES,
    DOMAINS,
    TABLE_OWNER,
    ContractViolation,
    CrsBindingError,
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

# ★★★★★ 必须写在这里，**不能**写成 `FastAPI(route_class=...)`。
#   `FastAPI.__init__` **没有** `route_class` 这个形参（实测
#   `"route_class" in inspect.signature(FastAPI.__init__).parameters` → False），
#   多传的 kwarg 会被 Starlette 当 `**extra` **静默吞掉**：
#       FastAPI(route_class=NoResponseModel)   → {"v":"117.000000"}  字符串 ✗
#       app.router.route_class = NoResponseModel → {"v":117.0}        数字 ✓
#   ★ 我第一版就是这么写错的，而且**看起来像修好了** —— 是逐条实测探测才抓出来。
#   ⇒ 覆盖全部路由 + 以后新增的每一条。根因与代价见 jsonnum.py。
app.router.route_class = NoResponseModel


def _crs_message(exc: CrsBindingError) -> str:
    """坐标基准不可用 → 一句能让人动手改的话。

    ★★ SC-009 要求：两级不一致时，错误信息 MUST 说出**两级各自是什么**。
      那句话由 M3 侧生成（`CrsBindingError.two_levels_note`），这里只**原样带出去**。

    ★ 刻意读**结构化的字段**，而不是去解析 `str(exc)`：
      解析文本会在措辞一改时静默失配，而失配的表现只是"提示少了半句"，
      不报错、不红、没人会发现。**能拿到字段就别去猜字符串。**

    ★★ 这里**不再自己加前缀**。加过一个「坐标基准不可用：」，结果与 M3 侧
      自带的「没有配坐标基准：」叠成 `…不可用：…没有配…：…` 两层冒号。
      而且上面那句 docstring 本来就写着"只原样带出去" —— 前缀与它自相矛盾。
      M3 那句话是完整的，可以直接交给用户。
    """
    note = getattr(exc, "two_levels_note", "")
    return f"{exc}（{note}）" if note else f"{exc}"


def _http(exc: DaoError) -> HTTPException:
    """把 M3 的语义化异常翻成 HTTP。DAO 不依赖 FastAPI，这层翻译是 M6 的职责。"""
    if isinstance(exc, NotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, (UnknownTable, UnknownDomain)):
        return HTTPException(404, str(exc))
    # ★★ 这一条**必须排在 ContractViolation 之前**。
    #    CrsBindingError 是 ContractViolation 的子类；顺序反了，它会掉进
    #    下面那个 400 分支 —— 于是"本实例手上的坐标基准不足以回答"
    #    被报成"你请求得不对"。**码值方向反了，而且不会报错。**
    #    为什么是 503 而不是 400：数据是平台自己录错的（两级不等 / SRID 查不到），
    #    客户端没有任何东西需要改。让客户端去改，它改不了。
    if isinstance(exc, CrsBindingError):
        return HTTPException(503, _crs_message(exc))
    if isinstance(exc, ContractViolation):
        return HTTPException(400, str(exc))
    if isinstance(exc, StorageUnavailable):
        return HTTPException(503, f"存储不可用：{exc}")
    return HTTPException(500, str(exc))


def _num(v: Any) -> Any:
    """`numeric` 列 → JSON 数字；`None` 原样返回。

    ★★ 这个函数存在的理由是**一个实测出来的缺陷**，不是风格：
      `crs_central_meridian_deg` 是 `numeric(9,6)`，psycopg 取出来是 `Decimal`，
      而 FastAPI 把 `Decimal` 序列化成**字符串**。契约写的是 `type: number`。
      实测（重建容器前后逐字对比）：`central_meridian_deg`
      由 `117` 变成 `"117.000000"`。
      ★★ 而契约测试**全绿** —— 因为 §13 只比**字段名**，不比类型。
      于是一个"看着是数字"的字符串会一路走到客户端才炸，
      炸的样子取决于客户端：JS 里 `"117.000000" * 3` 侥幸对，
      `+ 3` 就变成拼接；Python 里直接 TypeError。

    ⚠ 只用在**numeric 列**上。`smallint` / `integer` 本来就是 int，
      对它们调 `float()` 会把 `39` 变成 `39.0` ——
      那是在修一个类型的同时**弄坏另一个类型**。
    """
    return None if v is None else float(v)


# DAO 行里那 12 个带前缀的列名，逐字写出来。
# ★ 写死而不是"按前缀裁一刀"：DAO 的行形状归 M3，响应字段名归 M6 的契约。
#   用字符串裁剪把两者绑在一起，等于让**一次 DAO 侧的改名静默改掉 API 字段名**。
_CRS_ROW_KEYS = ("section_crs_epsg", "section_crs_zone_number",
                 "section_crs_central_meridian_deg", "section_crs_zone_width_deg",
                 "section_crs_assumed", "section_crs_evidence",
                 "project_crs_epsg", "project_crs_zone_number",
                 "project_crs_central_meridian_deg", "project_crs_zone_width_deg",
                 "project_crs_assumed", "project_crs_evidence")


def _crs_fields(eff: dict[str, Any] | None) -> dict[str, Any]:
    """把 `effective_crs()` 的结果投影成契约 v0.2 的 5 个 `crs_*` 字段。

    ★ `eff is None` ⇒ 五项全 `null`。这不是"没算"，是**有类型的「没有」**：
      「这个路段没有可用的坐标基准」。契约对 `/sections` 明说
      「这四项都 NOT required：本接口只如实报出库里有什么，不替客户端判必填」。
    """
    if eff is None:
        return {"crs_source": None, "crs_assumed": None, "crs_zone_number": None,
                "crs_central_meridian_deg": None, "crs_epsg": None}
    return {
        # ★ `source` 是**溯源**（这个值在哪一级被写下的），不是优先级。
        #   键名叫 `crs_source` 而**不是** `source`：本接口里已经有一个 `source`，
        #   它说的是"桩号是存的还是算的"。两者含义无关，同名会互相顶掉。
        "crs_source": eff["source"],
        "crs_assumed": eff["assumed"],
        "crs_zone_number": eff["zone_number"],
        "crs_central_meridian_deg": _num(eff["central_meridian_deg"]),
        "crs_epsg": eff["epsg"],
    }


def _crs_provenance(eff: dict[str, Any]) -> dict[str, Any]:
    """**单主体**路径（`/sections/{id}/geo` 等四条）上的两个基准字段。

    ★★ 只有两个，不是偷懒：契约 `m6-gateway.geo.v0.2.yaml` 在这四条路径上
      **只声明了 `crs_source` 与 `crs_assumed`**。
      多带 `crs_zone_number` / `crs_epsg` 出来，就是**未声明的字段** ——
      §13 的第②条判据会立刻红。
      ★ 单主体路径上带号与中央子午线**已经在响应里**（`zone_number` /
        `central_meridian_deg`，004 就有的字段）。再加一组 `crs_` 前缀的
        同义字段，会造出两个会各自漂的副本 —— 而副本不一致时没有判据看得见。
    """
    return {"crs_source": eff["source"], "crs_assumed": eff["assumed"]}


def _section_view(row: dict[str, Any]) -> dict[str, Any]:
    """DAO 的 38 列 → 契约声明的 31 个字段。**12 个 `*_crs_*` 不进响应。**

    ★★ 为什么必须显式投影，而不是"让 DAO 行原样过去"：
      `SECTIONS_SQL` 现在带出 12 个 `section_crs_*` / `project_crs_*`，
      而 `/sections` 原本是**直接把 DAO 行当响应**返回的。
      实测（重建容器后）：每一项多出 **12 个契约里没有的字段**，
      名字还是 M3 的内部列名。
      ⇒ 一次 DB 侧的加列，就变成一次**未声明的 API 变更**。
    """
    out = {k: v for k, v in row.items() if k not in _CRS_ROW_KEYS}
    try:
        eff = dao.ge.effective_crs(row)
    except CrsBindingError:
        # ★★ 一行配错**不许把整张表打成 503**。
        #   路网上千条时，一条录错会让"列出所有路段"整个不可用 ——
        #   而那恰恰是运维最需要它的时刻。这一行如实报 null，
        #   具体原因去 `/sections/{id}/geo` 看（那条带原话）。
        eff = None
    out.update(_crs_fields(eff))
    return out


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
        # ★★★ 2026-10-13（D-011 的收尾）：这里原先直接要 `limit` 行，
        #   而仓储的 SQL 是 `SELECT * FROM 表 ORDER BY id LIMIT n` ——
        #   **拿满 n 行时它不告诉你后面还有没有**。实测量到的后果：
        #   `cross_section_ground_point` 有 2215 行、`station_sequence` 与
        #   `earthwork_section` 各 332 行，接口一律回 `count: 100`，
        #   而响应里**没有任何字段**说明这是被截断的。
        #
        # ★ 修法是**多要一行来探测**：要到 limit+1 行 ⇒ 确实还有更多。
        #   为什么不去改仓储的签名：`list_objects` 还有别的消费者，
        #   而 tests/ 下有两个**假仓储**实现它 —— 改签名要连它们一起改，
        #   那是「为了一个字段动一条公共接口」。探测放在这里，
        #   **零接口改动**，而且判定是**精确**的（不是「拿满就猜」）。
        rows = dao.domain(code).list_objects(object_type, limit=limit + 1)
    except DaoError as exc:
        raise _http(exc) from exc
    # ★ 探测用的那一行**不进响应** —— 它只回答「有没有更多」。
    truncated = len(rows) > limit
    rows = rows[:limit]
    return {"object_type": object_type, "domain": code,
            "count": len(rows), "truncated": truncated, "items": rows}


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
    # ★ 逐行投影，不是 `return rows`。DAO 的行形状会随 DB 加列而变，
    #   而响应字段名是**契约**，不能跟着 DB 一起动。见 `_section_view`。
    return {"count": len(rows), "items": [_section_view(r) for r in rows]}


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


@app.get("/v1/geometry/sections/{section_id}/elements", tags=["几何查询"],
         summary="按桩号区间取线形单元（与区间**相交**的，供 Chrono / FEM）")
def get_elements_by_station(
    section_id: int,
    from_km: float = Query(..., ge=0, description="区间起点（km），含"),
    to_km: float = Query(..., ge=0, description="区间终点（km），含"),
) -> dict[str, Any]:
    """FR-018：把线形单元按里程切片给出去。

    ★ 返回的是**与区间相交**的单元，不是「完全落在区间内」的。
      一个 200 m 长的单元跨过 50 m 的查询窗时**必须**被返回 —— 否则采样会
      整段丢失长单元，而返回的行数看着还挺正常。判断口径写在每一行的
      `span_m` 上：它是**落在本区间内**的长度，被截断时与 `length_m` 不等。
      调用方拿 `span_m` 做积分才对；拿 `length_m` 会把里程轴算错。

    ⚠ `from_km > to_km` 返回 422（不是 500，也不是空列表）：那是**请求写错了**，
      不是「这个区间没有数据」。两者返一样的响应，调用方就分不出来。
    """
    if from_km > to_km:
        raise HTTPException(
            422, detail=f"区间反了：from_km={from_km} > to_km={to_km}")
    try:
        rows = dao.ge.elements_by_station(section_id, from_km, to_km)
    except DaoError as exc:
        raise _http(exc) from exc
    return {"section_id": section_id, "from_km": from_km, "to_km": to_km,
            "count": len(rows), "items": rows}


@app.get("/v1/geometry/sections/{section_id}/samples", tags=["几何查询"],
         summary="按桩号区间取某个逐桩段的行（供 Chrono / FEM）")
def get_samples_by_station(
    section_id: int,
    segment: str = Query(..., description="段名，取值见 /v1/catalog 的段清单"),
    from_km: float = Query(..., ge=0),
    to_km: float = Query(..., ge=0),
) -> dict[str, Any]:
    """逐桩采样（`.STA`/`.DMX`/`.WID`/`.SUP`/`.HDM` 等）。

    ⚠ 未知 `segment` 返回 **422 并列出可用值**，不返回空列表 ——
      空列表会被读成「这段在区间内没有数据」（数据缺口），
      而实际是「段名写错了」（调用错误）。这两件事在下游的处置完全不同：
      前者要去找源文件，后者要改代码。
    """
    if from_km > to_km:
        raise HTTPException(
            422, detail=f"区间反了：from_km={from_km} > to_km={to_km}")
    try:
        rows = dao.ge.samples_by_station(section_id, segment, from_km, to_km)
    except ValueError as exc:
        # 段名非法 —— 是入参问题，不是服务端问题
        raise HTTPException(422, detail=str(exc)) from exc
    except DaoError as exc:
        raise _http(exc) from exc
    return {"section_id": section_id, "segment": segment,
            "from_km": from_km, "to_km": to_km, "count": len(rows), "items": rows}

# ------------------------------------------------- 区域帧：桩号 ↔ 经纬度（004）
import geo as geo_mod   # noqa: E402  （与 app.py 同目录，容器里由 WORKDIR /app 提供）


def _geo_crs(section_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """取该路段**生效**的坐标基准。取不到 → 503，**不猜默认带号**。

    返回 `(geographic_frame, 生效值)`。★ 两个都返回，是因为调用方**两样都要**：
      · `geographic_frame` 给几何算法用（带号、中央子午线）
      · 生效值给响应体用（`crs_source` / `crs_assumed` —— 契约 v0.2 要求）
      只返回前者，`crs_source` 就得在四条路由里各查一次库；
      查四次就有四次机会和几何用的不是同一份。

    ★★ 2026-10-12（005 / Q3 裁决）：来源由「读 `crs.yaml`」改为「**读库**」。
       库是权威，文件降为派生产物。

    三件事分在三个地方，这是宪法 II 的直接后果：
      · 两级怎么合成、不等就报错      → M3 的 `GeRepository.effective_crs()`
      · 合成出来的值合不合法           → `geo.load_crs()`（assumed ↔ epsg）
      · 上面两类异常翻成 HTTP          → 这里
      ★ `geo.py` 里**没有任何数据库调用**，它收到的只是一个 dict。
        这条分工不是洁癖：让 `geo.py` 自己去查库，它就会长出连接池，
        而 M9 容器刻意不给它 `PG_DSN` —— 到那时它只能在生产里失败。
    """
    try:
        # `ONE_SECTION_SQL` 已经把两组 `crs_*` 列带出来了（39 列）。
        # ★ 不再单独查一次 `design_project`：两级必须来自**同一次读取**，
        #   分两次读会引入一个"两次读之间被人改了"的窗口，
        #   而那个窗口的后果是两级看似一致、其实不等。
        row = dao.ge.section(section_id)
        eff = dao.ge.effective_crs(row)
        # ★★ 005 / T043：把「这个 SRID 是什么投影族」问出来（M3 是唯一有连接的层）。
        #    ★ 它必须在**同一个 try** 里：不然数据库一断，上面两次读抛 DaoError、
        #      这一次会漏出去变成一个 500 —— 同一类故障出现两种状态码。
        _fam = None if eff["epsg"] is None else dao.ge.crs_family(eff["epsg"])
    except DaoError as exc:
        raise _http(exc) from exc

    # ★ `assumed: true`（值是猜的）**没有 EPSG**，所以没有族可查。
    #   但这不算「猜」：在这套模型里，「假定值」**只能**用「带号 + 中央子午线」
    #   表达，而那套表达就是高斯-克吕格。所以这是**声明**，不是推断。
    #   ★ 反过来说：如果哪天模型允许「假定值 + 别的投影」，这一行就是错的 ——
    #     所以它写在这里、只有一个地方，而不是散在四条路由里。
    if _fam is None and eff["epsg"] is None:
        _fam = "tmerc"
    if _fam is None:
        # EPSG 在库里（外键保证），但 `spatial_ref_sys` 里取不到 `+proj=`。
        # ★ 这是「**不知道**它是什么族」，不是「支持」。不许放行。
        raise HTTPException(503, (
            f"坐标基准不可用：路段 {section_id} 的 crs_epsg={eff['epsg']}，"
            "但在 `spatial_ref_sys` 里取不到它的投影族（`proj4text` 里没有 `+proj=`）。"
            "**「不知道」不是「支持」** —— 不猜，拒绝计算。"
        ))

    doc = {"geographic_frame": {
        "zone_number": eff["zone_number"],
        # ★ `_num` 存在的理由变了（2026-10-13，T045）：`app.router.route_class`
        #   挂上 `NoResponseModel` 之后，**全局**已经保证 numeric 以数字出去，
        #   这里不再"不转就出错"。保留是为了**双保险**。
        #   ⚠ 但别再靠它 —— 它只覆盖**手写到的地方**，而漏的那 10 条路由就是证据。
        #     ★ 实测过：摘掉 `_num`，这两列**仍然是数字**（全局那层接住）。
        #       见 tests/contract/test_region_frame.py §16d 的两层判据。
        "central_meridian_deg": _num(eff["central_meridian_deg"]),
        "zone_width_deg": _num(eff["zone_width_deg"]),
        "assumed": eff["assumed"],
        # ★ 键名沿用小节里那个 `epsg_after_confirm`（"确认之后填的 EPSG"）。
        #   库里它就是权威字段本身，所以这里是**映射，不是改名** ——
        #   目的是让 `load_crs` 的校验逻辑与 004 逐字一致，一个字都不用动。
        "epsg_after_confirm": eff["epsg"],
        # ★★ 005 / T043：本实现算不算得了它，由 `load_crs` 里的
        #    `require_family` 判 —— 族不在 `IMPLEMENTED_FAMILIES` 里就抛 CrsError，
        #    下面翻成 503。
        #    ★ 现场：数据模型接受**任意 SRID**（宪法 §VI），而手写换算
        #      **只实现了高斯-克吕格**。填 `2154`（Lambert）时旧代码不报错，
        #      会算出一个量级正常、位置错掉几百米的坐标。
        "projection_family": _fam,
    }}
    try:
        return geo_mod.load_crs(doc, origin=f"路段 {section_id} 的生效基准"), eff
    except geo_mod.CrsError as exc:
        # ★★ 005 / T043：消息里**必须说出那个 SRID 是什么**。
        #    · `require_family` 报的是**族**（`lcc`）—— 那是「算法层」的说法；
        #      而运维/数据录入的人手里看到的是 **EPSG 编码**。
        #      只说族，他要先去查「lcc 对应哪个 SRID」才能动手；
        #      只说 SRID，他不知道为什么不行。**两个都要说。**
        #    · ★ 兜底那句话也不能省：一个没有内容的 503 会被人当成
        #      「服务挂了」，然后去重启 —— 而真正要改的是那一行的 `crs_epsg`。
        _srid_txt = ("未填 EPSG" if eff["epsg"] is None else f"EPSG:{eff['epsg']}")
        raise HTTPException(503, (
            f"坐标基准不可用：路段 {section_id} 的 {_srid_txt} —— {exc}"
        )) from exc


def _geo_elements(section_id: int) -> list[dict[str, Any]]:
    """取某路段的线元链，转成 `geom` 要的形状。

    ⚠ 这里**必须**走适配层（`as_adapter_elements`）。直接把手里的落库行喂给
      `geom` 会少一个 `turn_flag`，而 `geom` 对它的缺失是**静默**的：
      默认按左转处理，右转的曲线朝反方向弯，实测终点差 98 m。
    """
    try:
        raw = dao.ge.alignment(section_id)["elements"]
    except DaoError as exc:
        raise _http(exc) from exc
    if not raw:
        raise HTTPException(
            404, f"路段 {section_id} 没有线形单元（alignment_element 无行）")
    return geo_mod.as_adapter_elements(raw)


@app.get("/v1/geometry/sections/{section_id}/geo", tags=["区域帧"],
         summary="桩号 → 经纬度（单点）")
def get_geo_point(
    section_id: int,
    station_m: float = Query(..., description="路段内里程，**米**"),
) -> dict[str, Any]:
    """把一个桩号换算成经纬度。

    ★ 返回里**必须**有 `epsg`。39 带有两个 EPSG 编码（4548 无带号前缀 /
      4527 有），差别是东坐标 39,000,000 m。只回经纬度的接口让调用方
      无法判断自己拿到的是哪一种，也就无法发现取错。

    ★ `source` 固定为 `"computed_from_station"`：这是**换算**，不是**定位**。
      本平台不声称这条路真的在这个经纬度上（理由见 research.md 的 R7）。
    """
    gf, crs = _geo_crs(section_id)
    els = _geo_elements(section_id)
    try:
        out = geo_mod.station_to_lonlat(els, station_m, gf)
    except geo_mod.StationOutsideAlignment as exc:
        # 桩号越界是**调用方**的问题，用 400 而不是 404：
        # 404 会被读成「这个桩号没有数据」，而实际是「这个桩号不在链上」。
        raise HTTPException(400, str(exc)) from exc
    return {"section_id": section_id, **out, **_crs_provenance(crs)}


@app.get("/v1/geometry/sections/{section_id}/geo/reverse", tags=["区域帧"],
         summary="经纬度 → 最近的桩号（单点）")
def get_geo_reverse(
    section_id: int,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    """把经纬度换回最近的桩号。

    ★★ 回带 `offset_m`（查询点与最近点的距离）与 `offset_band`（分带）。
      **不隐藏它** —— 一个 500 m 外的"最近桩号"和一个 2 m 外的，
      含义完全不同。调用方要能自己判断这个答案有多近。

    ★★★ `offset_band` 三值，边界来自实测数据（不是估计）：
      · `on_roadbed`    ≤ 5.0 m   —— 设计路基半宽（0.75+0.75+0+3.5）
      · `in_corridor`   ≤ 50.0 m  —— 实测地面点横向覆盖 0.019..50.000 m
      · `off_corridor`  > 50.0 m  —— 走廊之外，**残差就是全部答案**

      ★ 只有 `on_roadbed` 时，`station_m` 才同时是"这个点的桩号"。
      其余两带里它只是**最近点**的桩号 —— `station_is_nearest`
      把这个区别显式说出来。

    ⚠️ **不再声称"带外就拒绝"。** 本路由对任何经纬度都返回 200，
      包括离路线 1 281 km 的点 —— 它不拒绝，但如实报出残差与分带。
      原先 spec 的 Edge Cases 与 `data-model.md` §5 写的是"报错，不硬算"，
      与实现不符（2026-10-11 查实），已按平台方裁决改为本措辞。

      ★ 为什么不按"带外就拒"做：实测 `lon=117.36°E`（**在 39 带内**）
      残差已达 371.7 m —— 带号检查会放过它。
      「带外」只是"点不在路上"的一种成因，`offset_m` 才是那道闸。
    """
    gf, crs = _geo_crs(section_id)
    els = _geo_elements(section_id)
    return {"section_id": section_id,
            **geo_mod.reverse_lonlat(els, lat, lon, gf),
            **_crs_provenance(crs)}


@app.get("/v1/geometry/sections/{section_id}/geo/route", tags=["区域帧"],
         summary="整条路线 → 经纬度折线 + 包围盒")
def get_geo_route(
    section_id: int,
    step_m: float = Query(20.0, gt=0, le=1000,
                          description="线元内的采样步长（米）"),
) -> dict[str, Any]:
    """整条路线换算成经纬度折线，附包围盒。

    ★ 采样是**逐个线元**做的，不是"从 0 开始每 step_m 一个"。
      后者的样点密度与曲率无关，而「曲线是弯的」这条判据恰恰要看
      曲率大处的折线够不够密。

    ★ 包围盒是本 feature 落点检查（SC-002）所用的量。
    """
    gf, crs = _geo_crs(section_id)
    els = _geo_elements(section_id)
    return {"section_id": section_id, **geo_mod.route_lonlat(els, gf, step_m),
            **_crs_provenance(crs)}


# ⚠ `DomainRepository.list_objects` 的默认 `limit=100` 是**静默截断**：
#   超过 100 行时它不报错，只是少给。商用平台上断面/设备数以万计，
#   靠"取全表再在应用里过滤"迟早会撞上这条线，而撞上时**不会有人知道**。
#
# ★ 这里不猜表有多大，而是**检测**有没有撞上限：取满即视为截断，直接 503。
#   代价是一次多余的行，收益是"少给了数据"这件事永远会响。
# ★ 真正的修法（给仓储加带条件的取数，而不是取全表）不属本 feature ——
#   已记入 004 的通用性待办，见 tasks.md。
_LIST_PROBE = 100


def _list_tracked(domain_repo, table: str) -> list[dict[str, Any]]:
    """取整表，**并在被截断时报错而不是少给**。"""
    try:
        # ★★ 同样多要一行。**这不只是加个字段的事，它修掉了一个误报**：
        #   原先判的是 `len(rows) >= _LIST_PROBE` —— 而「恰好 100 行」的表
        #   会**拿满 100 行**，于是被判成截断、回 503。
        #   一张正好 100 行的表本来是能完整取到的，却被拒绝服务。
        #   ⇒ 探测之后判定变成**精确**的：要到 101 行才叫截断。
        rows = domain_repo.list_objects(table, limit=_LIST_PROBE + 1)
    except DaoError as exc:
        raise _http(exc) from exc
    if len(rows) > _LIST_PROBE:
        raise HTTPException(
            503, f"{table} 取数触到仓储上限 {_LIST_PROBE} 行，结果可能不完整；"
                 f"在补齐带条件的取数之前，本接口拒绝返回不完整的结果")
    return rows


@app.get("/v1/geometry/sections/{section_id}/monitor-sections", tags=["区域帧"],
         summary="监测断面的位置（含仿真设备溯源）")
def get_monitor_sections(section_id: int) -> dict[str, Any]:
    """列出某路段的监测断面，每个都带上**它的位置是怎么来的**。

    ★★★ 三种来源，穷尽，**不会少一个**：
      · `stored`                —— 库里有经纬度，直接用
      · `computed_from_station` —— 库里没有，由桩号换算
      · `unavailable`           —— 换算不出来，**给出 `unavailable_reason`**

    ★★ `lat_deg` / `lon_deg` 两个键**只在有位置时才出现**。
      这是 FR-010「不得把 NULL 当成 (0,0)」的**结构性**保证：
      无条件取用的人拿到 `KeyError`/`undefined`，**而不是 (0,0)** ——
      也就是说，这个错误无法静默发生。

    ★★ 「算不出来」**不是**错误：同一个平台上有实测坐标的路段、
      只有桩号的路段、以及还没做数字化线形的路段，都是正常的。
      所以 `unavailable` 走 200 而不是 404 —— 它是一个**正常的答案**，
      不是"资源不存在"。

    ★ 设备带 `install_mode` 与 `simulated` 两个字段（判断 + 依据）。
      仿真设备不冒充实体资产。
    """
    gf, crs = _geo_crs(section_id)
    try:
        # ★ 先证明**路段存在**，再回答"它有几个断面"。
        #   两件事必须分开：路段不存在 → 404；路段存在但没有断面 → 200 空列表。
        #   不先证明，写错一个 section_id 得到的是"没有数据"，而不是"没有这个路段" ——
        #   这正是本 feature 一路在挡的那类静默错。
        dao.ge.section(section_id)
        raw_els = dao.ge.alignment(section_id)["elements"]
    except DaoError as exc:
        raise _http(exc) from exc
    # ★ 此处**不**复用 `_geo_elements`：它为"没有线元"抛 404，
    #   而本接口要能对没有线元的路段照常作答（断面全部 unavailable）。
    els = geo_mod.as_adapter_elements(raw_els) if raw_els else []

    xsecs = [r for r in _list_tracked(dao.ge, "monitor_cross_section")
             if r.get("section_id") == section_id]
    sensors = _list_tracked(dao.re, "sensor_install")

    return {"section_id": section_id,
            **geo_mod.section_positions(els, xsecs, sensors, gf),
            **_crs_provenance(crs)}


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