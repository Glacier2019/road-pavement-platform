"""M4 数据治理 · 真数据门 —— 骨架（机械层已就位，业务逻辑留 TODO）

本模块认哪份契约
----------------
* **消费** 契约① 接入侧批次与质量日志（`data_import_batch` / `data_quality_log`）
* **产出** `contracts/governance/quality_rule.v0.1.schema.json`（质量规则定义）
* **产出** `contracts/governance/promotion.v0.1.schema.json`（真值晋升请求/响应）

设计纪律（不得自行发挥）
------------------------
1. **质量规则的"定义"走配置外置**（`config/quality_rules.yaml`）。
   2026-09-15 起 DDL 为 **v0.2（物理 32 表）**，其中 G5 `quality_rule` 表已建好，
   它承担**标定状态的权威记录**（YAML 是种子、表是权威）。
   要再新建表仍必须走契约变更工单。
2. 本模块的核心动作是**真值晋升**：`data_import_batch.truth_flag` 由 `false` 变 `true`。
   骨架栈至今 `truth_flag` 全为 `false`，即这个动作从未发生过——这正是本模块存在的理由。
3. 阈值（偏差 2%、覆盖率、里程连续性…）**必须由真实数据标定**，
   当前 `config/quality_rules.yaml` 里的值全部标记 `calibrated: false`。
   **未标定的规则不得用于生产判定。**

机械层（已完成，照抄自 `services/ingest`，勿改模式）
--------------------------------------------------
* 容器定义 + healthcheck（五件套 1）
* `/healthz` 逐项报依赖状态、`/metrics` 出 Prometheus 文本（五件套 2）
* 配置全部来自环境变量或 `config/*.yaml`，代码零硬编码（五件套 3）
* 契约文件提交到 `contracts/`（五件套 4）
* `tests/contract/test_governance_contract.py` 离线可跑（五件套 5）
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from psycopg_pool import ConnectionPool

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOG = logging.getLogger("m4-governance")

# ------------------------------------------------------------------ 配置外置
# 全部来自环境变量；默认值只是本地开发的兜底，不是生产配置。
PG_DSN = os.getenv("PG_DSN", "postgresql://rp:rp_change_me@localhost:55432/road_pavement")
PROCESSOR = os.getenv("PROCESSOR", "m4-governance-skeleton")
RULES_PATH = Path(os.getenv("QUALITY_RULES_PATH", "config/quality_rules.yaml"))

# 契约变更纪律：本模块只**读**接入侧的批次/质量日志，不写。
# 写 `data_import_batch.truth_flag` 是晋升动作，走 §晋升接口，且必须留审计。
MODULE_CONTRACTS = {
    "consumes": [
        "contracts/governance/quality_rule.v0.1.schema.json",
        "表 data_import_batch / data_quality_log（契约② DDL v0.2）",
    ],
    "produces": [
        "contracts/governance/promotion.v0.1.schema.json",
    ],
}


class State:
    """进程内计数，供 /metrics 用。"""

    def __init__(self) -> None:
        self.validated = 0
        self.promoted = 0
        self.rejected = 0
        self.last_error: str | None = None


STATE = State()
pool: ConnectionPool | None = None
RULES: dict[str, Any] = {}


def load_rules() -> dict[str, Any]:
    """读质量规则配置。

    **返回空集是合法的**：规则未标定前不应凭空判定。空集时 /v1/rules 返回空列表，
    校验接口一律返回 503（尚未标定），而不是"全部通过"——后者是永远不会失败的检查。
    """
    if not RULES_PATH.exists():
        LOG.warning("规则文件不存在：%s（校验接口将返回 503）", RULES_PATH)
        return {"version": None, "rules": []}
    with RULES_PATH.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    rules = data.get("rules") or []
    uncalibrated = [r.get("code") for r in rules if not r.get("calibrated")]
    if uncalibrated:
        LOG.warning("以下规则尚未用真实数据标定，不得用于生产判定：%s", uncalibrated)
    return data


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool, RULES
    RULES = load_rules()
    try:
        pool = ConnectionPool(PG_DSN, min_size=1, max_size=4, open=True, timeout=5)
        LOG.info("已连接 PostgreSQL")
    except Exception as exc:  # noqa: BLE001
        # 连不上不阻止启动：/healthz 会如实报 pg 不健康，便于定位
        LOG.error("连接 PostgreSQL 失败：%s", exc)
        pool = None
    yield
    if pool is not None:
        pool.close()


app = FastAPI(
    title="M4 数据治理 · 真数据门（骨架）",
    version="0.1.0-skeleton",
    lifespan=lifespan,
)


# ------------------------------------------------------------------ 机械层
@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """逐项报依赖状态。依赖不可用时返回非 200，便于 compose healthcheck 判定。"""
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

    rules_ok = bool(RULES.get("rules"))
    body = {
        "service": "m4-governance",
        "status": "ok" if (pg_ok and rules_ok) else "degraded",
        "dependencies": {
            "postgres": {"ok": pg_ok, "detail": pg_msg},
            "quality_rules": {
                # ok 只表示"规则文件可加载"，**不表示规则可用于判定**
                "ok": rules_ok,
                "detail": "" if rules_ok else "规则文件缺失或为空，校验接口返回 503",
                "count": len(RULES.get("rules") or []),
                "calibrated_count": len(_calibrated_rules()),
                "gate_open": _gate_open(),
                "gate_detail": "" if _gate_open()
                else "规则集整体未标定（骨架期预期）——校验/晋升接口将返回 503",
            },
        },
        "contracts": MODULE_CONTRACTS,
    }
    return JSONResponse(body, status_code=200 if pg_ok else 503)


@app.get("/metrics")
def metrics() -> str:
    lines = [
        "# TYPE rp_governance_validated_total counter",
        f"rp_governance_validated_total {STATE.validated}",
        "# TYPE rp_governance_promoted_total counter",
        f"rp_governance_promoted_total {STATE.promoted}",
        "# TYPE rp_governance_rejected_total counter",
        f"rp_governance_rejected_total {STATE.rejected}",
        "# TYPE rp_governance_rules_loaded gauge",
        f"rp_governance_rules_loaded {len(RULES.get('rules') or [])}",
        "# TYPE rp_governance_rules_calibrated gauge",
        f"rp_governance_rules_calibrated {len(_calibrated_rules())}",
        "# TYPE rp_governance_gate_open gauge",
        f"rp_governance_gate_open {1 if _gate_open() else 0}",
        "# TYPE rp_governance_pg_up gauge",
        f"rp_governance_pg_up {1 if pool is not None else 0}",
    ]
    return "\n".join(lines) + "\n"


@app.get("/metrics", include_in_schema=False)
def metrics_plain() -> PlainTextResponse:  # pragma: no cover - 便于直接 curl
    return PlainTextResponse(metrics())


# ------------------------------------------------------------------ 业务层（TODO）
def _todo(what: str, owner: str = "A 组（数据底座）") -> JSONResponse:
    """统一的占位响应。

    刻意返回 **501 且带 owner**：让"还没实现"这件事在接口层面可见，
    而不是返回一个空数组让调用方以为"没有异常数据"。
    """
    return JSONResponse(
        {
            "detail": "未实现",
            "what": what,
            "owner": owner,
            "note": "业务逻辑需在真实数据标定后实现；契约见 MODULE_CONTRACTS",
        },
        status_code=501,
    )


@app.get("/v1/rules")
def list_rules() -> dict[str, Any]:
    """列出已加载的质量规则。这是**配置**的透出，不是判定。"""
    return {
        "version": RULES.get("version"),
        # 明确标注：这批阈值未经真实数据标定，不得用于生产
        "calibrated": all(r.get("calibrated") for r in (RULES.get("rules") or []))
        if RULES.get("rules")
        else False,
        "rules": RULES.get("rules") or [],
    }


def _gate_open() -> bool:
    """真数据门的门禁是否打开——以**配置顶层的整体标定开关**为准。

    这里踩过两次坑，都记下来：
      ① 首版按"有没有规则"判 → 配置里有 11 条规则，门禁形同虚设；
      ② 二版按"有没有已标定的规则"判 → 11 条里有 2 条结构规则（轴数=明细长度）
         本来就无需标定，于是门又开了——**2/11 通过不等于规则集可用**。
    正确的判据是顶层的 `calibrated`：它表达的是"整套规则可用于生产判定"。
    逐条规则上的 calibrated 只表示该条自身状态，两者含义不同，不可互相替代。
    """
    return bool(RULES.get("calibrated") is True)


def _calibrated_rules() -> list[dict[str, Any]]:
    """已标定的单条规则，用于**进度展示**（不用于门禁判据）。"""
    return [r for r in (RULES.get("rules") or []) if r.get("calibrated")]


@app.post("/v1/batches/{batch_no}/validate")
def validate_batch(batch_no: str) -> JSONResponse:
    """对一个接入批次跑质量规则。

    TODO(A 组)：按 config/quality_rules.yaml 逐条判定，写 `data_quality_log`。
    **未标定前一律 503，不得返回"通过"**——否则就是一个永远不会失败的检查。
    """
    if not _gate_open():
        return JSONResponse(
            {
                "detail": "规则集未标定，拒绝出结论",
                "batch_no": batch_no,
                "rules_defined": len(RULES.get("rules") or []),
                "rules_calibrated": len(_calibrated_rules()),
                "hint": "A 组用真实数据标定后，把 config/quality_rules.yaml 里对应规则的 "
                        "calibrated 改为 true，本接口才会开始出结论",
            },
            status_code=503,
        )
    return _todo(f"按质量规则校验批次 {batch_no}")


@app.post("/v1/promotions")
def promote() -> JSONResponse:
    """真值晋升：`data_import_batch.truth_flag` false → true。

    TODO(A 组)：入参见 contracts/governance/promotion.v0.1.schema.json。
    必须满足：① 该批次全部规则通过 ② 写审计记录 ③ 幂等（重复晋升不重复计数）。
    """
    if not _gate_open():
        return JSONResponse(
            {"detail": "规则集未标定，不得晋升",
             "rules_calibrated": len(_calibrated_rules()),
             "rules_defined": len(RULES.get("rules") or []),
             "hint": "未标定的质量门放行的『真值』没有意义"},
            status_code=503,
        )
    return _todo("真值晋升（truth_flag false → true），需契约 promotion.v0.1 校验通过")


@app.get("/v1/batches/{batch_no}/report")
def batch_report(batch_no: str) -> JSONResponse:
    """批次质量报告。TODO(A 组)：这是 P1 的交付物之一。"""
    return _todo(f"批次 {batch_no} 质量报告")


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
