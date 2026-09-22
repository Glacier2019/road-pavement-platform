"""M2 接入服务（骨架版）——一条竖切的中段：

    MQTT(g228/lo/{site_id}/axle) → 契约校验 → wim_axle_record(+wim_axle_detail)
                                              ↘ 失败 → data_quality_log（待检区）
    批次溯源 → data_import_batch（raw_count / valid_count / truth_flag=false）

只做这三件事，不承担治理（M4）与语义（M7）职责——那正是"模块只认契约"的含义。
暴露接口：GET /healthz、GET /stats、GET /metrics（Prometheus 文本）、GET /docs
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import tempfile
import threading
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from paho.mqtt import client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from pydantic import ValidationError
from rpdao.write import WriteDao

import design_import
from adapters import base, weidi
from adapters.errors import ImportError_, ParseBlocked, SourceInvalid  # noqa: F401
from design_import import LoadError
from models import SCHEMA_VERSION, WimEvent
import violations

LOG = logging.getLogger("ingest")
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

PG_DSN = os.getenv("PG_DSN", "postgresql://rp:rp_change_me@localhost:55432/road_pavement")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "g228/+/+/axle")
BATCH_PREFIX = os.getenv("BATCH_NO_PREFIX", "skeleton")
PROCESSOR = os.getenv("PROCESSOR", "m2-ingest-skeleton")

# 契约③：接入侧的**写**也走 DAO（原先这里自带连接池 + 4 条裸 SQL，绕过了契约）。
# 用 WriteDao 而非 Dao：本模块是七域业务数据的唯一写入方（M2），
# 写权守卫会逐表核对 catalog.TABLE_OWNER，越权/写只读表都会直接报错。
dao = WriteDao(PG_DSN, app_name="rp-ingest", min_size=1, max_size=4, timeout=15)

# 写权身份：本服务只以 M2 身份写库，且只能写 TABLE_OWNER 里属于 M2 的表。
WRITER = "M2"

# ----------------------------------------------------------------- 运行状态
class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mqtt_connected = False
        self.received = 0
        self.inserted = 0
        self.rejected = 0
        self.duplicated = 0
        self.db_errors = 0
        self.last_message_at: str | None = None
        self.last_error: str | None = None
        self.last_topic: str | None = None
        self.batch_no = f"{BATCH_PREFIX}-{datetime.now(timezone.utc).astimezone():%Y%m%d%H}"
        # 去重：device_code+seq 滑动窗口（骨架期内存实现；P1 换 Redis/表）
        self.seen: deque[tuple[str, int]] = deque(maxlen=50_000)
        self.seen_set: set[tuple[str, int]] = set()
        # 内存计数 → 每 10s 刷新进 data_import_batch（避免每条都写批表）
        self.pending_raw = 0
        self.pending_valid = 0

    def mark_seen(self, key: tuple[str, int]) -> bool:
        """返回 True 表示重复。"""
        with self.lock:
            if key in self.seen_set:
                return True
            if len(self.seen) == self.seen.maxlen:
                self.seen_set.discard(self.seen[0])
            self.seen.append(key)
            self.seen_set.add(key)
            return False


STATE = State()

DEVICE_MAP: dict[str, dict[str, Any]] = {}
DEVICE_MAP_LOCK = threading.Lock()

# ----------------------------------------------------------------- SQL
# 绝大多数写已改为走契约③（dao.insert / write_txn），此处仅保留两条**必须**手写的：
#
#   1) MAP_SQL —— 只读的联表查询（设备注册表 → 通道），交给 dao.query。
#   2) UPSERT_BATCH —— 批次计数是 **列 = 列 + 增量** 语义（多轮刷新的增量和），
#      通用 upsert 的 "SET col = EXCLUDED.col" 是**替换**，会把这轮的增量
#      覆盖掉上一轮的累计值。故走 dao.execute_write(受同一个写权守卫保护)，
#      而不是硬塞进通用接口——**为了套用新接口而改变语义，是收口时最容易犯的错**。
MAP_SQL = """
SELECT si.serial_no, si.cross_section_id, sc.id AS channel_id,
       sc.quantity_code, sc.channel_no
FROM sensor_install si
JOIN sensor_channel sc ON sc.install_id = si.id
WHERE si.status = 'active'
"""

UPSERT_BATCH = """
INSERT INTO data_import_batch
  (batch_no, source_type, source_desc, channel_count, raw_count, valid_count,
   truth_flag, import_start, import_end, quality_code, handler, remark)
VALUES (%(batch_no)s, 'mqtt', %(source_desc)s, %(channel_count)s, %(raw_count)s,
        %(valid_count)s, false, %(import_start)s, now(), 'OK', %(handler)s, %(remark)s)
ON CONFLICT (batch_no) DO UPDATE
SET raw_count   = data_import_batch.raw_count + EXCLUDED.raw_count,
    valid_count = data_import_batch.valid_count + EXCLUDED.valid_count,
    import_end  = now()
"""


def load_device_map() -> None:
    """从 PG 读设备注册表（serial_no → 断面/通道）。P1 改为变更通知刷新。"""
    try:
        rows = dao.query(MAP_SQL)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("设备映射加载失败（稍后重试）：%s", exc)
        return

    mapping: dict[str, dict[str, Any]] = {}
    for _r in rows:
        serial_no = _r["serial_no"]
        cross_section_id = _r["cross_section_id"]
        channel_id = _r["channel_id"]
        quantity_code = _r["quantity_code"]
        channel_no = _r["channel_no"]
        cur_best = mapping.get(serial_no)
        is_axle = quantity_code in ("axle_load", "axle")
        if cur_best is None or (is_axle and not cur_best["is_axle"]) or (
            is_axle == cur_best["is_axle"] and channel_no < cur_best["channel_no"]
        ):
            mapping[serial_no] = {
                "cross_section_id": cross_section_id,
                "channel_id": channel_id,
                "quantity_code": quantity_code,
                "channel_no": channel_no,
                "is_axle": is_axle,
            }
    with DEVICE_MAP_LOCK:
        DEVICE_MAP.clear()
        DEVICE_MAP.update(mapping)
    LOG.info("设备映射已加载：%d 台（%s）", len(mapping), ", ".join(sorted(mapping)))


def device_of(device_code: str) -> dict[str, Any] | None:
    with DEVICE_MAP_LOCK:
        dev = DEVICE_MAP.get(device_code)
    if dev is None:
        load_device_map()          # 未知设备 → 立即重载一次（新设备接入场景）
        with DEVICE_MAP_LOCK:
            dev = DEVICE_MAP.get(device_code)
    return dev


# ----------------------------------------------------------------- 落库
def write_reject(dev: dict[str, Any] | None, event_ts: datetime, issue: str, desc: str) -> None:
    """契约违约 → 质量日志（不落主表，进待检区）。"""
    if dev is None:
        LOG.error("拒收但设备未注册，无法写质量日志：%s", desc)
        return
    dao.insert("data_quality_log", [{
        "channel_id": dev["channel_id"],
        "period_start": event_ts, "period_end": event_ts,
        "raw_count": 1, "valid_count": 0,
        "issue_code": issue, "issue_desc": desc[:500],
        "action_code": "mark", "processor": PROCESSOR,
    }], writer=WRITER)


def insert_event(ev: WimEvent, dev: dict[str, Any]) -> None:
    """主记录 ＋ 轴组明细，**同一事务**。

    ★ 收口时最容易丢的就是这里的原子性：若改成两次 dao.insert(...)，
      两次会各取一条池连接，进程在中间挂掉就会留下一条**没有轴组明细的过车记录**
      —— 而下游看它是完全合法的数据，不会报错。故必须用 write_txn。
    """
    pass_time = ev.ts
    with dao.write_txn(writer=WRITER) as tx:
        record_id = tx.insert_returning("wim_axle_record", {
            "cross_section_id": dev["cross_section_id"],
            "pass_time": pass_time,
            "lane_no": ev.payload.lane_no,
            "direction": ev.payload.direction,
            "axle_type_code": ev.payload.axle_type_code,
            "axle_num": ev.payload.axle_num,
            "speed_kmh": ev.payload.speed_kmh,
            "gross_weight_kg": ev.payload.gross_weight_kg,
            "overload_flag": ev.is_overload(),
            "overload_rate": ev.overload_rate(),
            "esal": ev.esal(),
            "plate_no": ev.payload.plate_no,
            "data_source": f"mqtt:{ev.device_code}",
            "quality_code": "OK",
        })
        tx.insert("wim_axle_detail", [{
            "record_id": record_id, "pass_time": pass_time,
            "axle_seq": a.axle_seq, "group_seq": a.group_seq,
            "axle_weight_kg": a.weight_kg, "group_weight_kg": a.group_weight_kg,
            "axle_dist_mm": a.dist_mm,
        } for a in ev.payload.axles], on_conflict=("record_id", "axle_seq"))


# ----------------------------------------------------------------- MQTT
def on_connect(client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
    if reason_code == 0:
        STATE.mqtt_connected = True
        client.subscribe(MQTT_TOPIC, qos=1)
        LOG.info("已连接 MQTT 并订阅 %s", MQTT_TOPIC)
    else:
        STATE.mqtt_connected = False
        LOG.error("MQTT 连接失败：%s", reason_code)


def on_disconnect(client, userdata, flags, reason_code, properties=None):  # noqa: ANN001
    STATE.mqtt_connected = False
    LOG.warning("MQTT 断开：%s（paho 自动重连）", reason_code)


def on_message(client, userdata, msg):  # noqa: ANN001
    with STATE.lock:
        STATE.received += 1
        STATE.pending_raw += 1
        STATE.last_message_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        STATE.last_topic = msg.topic

    try:
        raw = json.loads(msg.payload.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            STATE.rejected += 1
            STATE.last_error = f"JSON 解析失败: {exc}"
        LOG.warning("JSON 解析失败（topic=%s）：%s", msg.topic, exc)
        return

    try:
        ev = WimEvent.model_validate(raw)
    except ValidationError as exc:
        dev = device_of(str(raw.get("device_code", "")))
        with STATE.lock:
            STATE.rejected += 1
            STATE.last_error = f"契约违约: {exc.errors()[0].get('msg')}"
        # 分类出**精确的规则码**（原先一律写 contract_violation，导致 3 类违约
        # 在日志里无法区分——实测 26 条里 9 条是被误记的轴数不符）
        code, _ = violations.classify(exc)
        LOG.warning("契约违约[%s]（device=%s）：%s", code, raw.get("device_code"),
                    exc.errors()[:1])
        try:
            write_reject(dev, datetime.now(timezone.utc), code,
                         violations.describe(exc))
        except Exception as e2:  # noqa: BLE001
            LOG.error("写质量日志失败：%s", e2)
        return

    if STATE.mark_seen((ev.device_code, ev.seq)):
        with STATE.lock:
            STATE.duplicated += 1
        LOG.info("重复报文（device=%s seq=%s）已忽略", ev.device_code, ev.seq)
        return

    dev = device_of(ev.device_code)
    if dev is None:
        with STATE.lock:
            STATE.rejected += 1
            STATE.last_error = f"设备未注册: {ev.device_code}"
        LOG.error("设备未注册（device=%s）——请先在 sensor_install 登记", ev.device_code)
        return

    try:
        insert_event(ev, dev)
    except Exception as exc:  # noqa: BLE001
        with STATE.lock:
            STATE.db_errors += 1
            STATE.last_error = f"入库失败: {exc}"
        LOG.error("入库失败（device=%s seq=%s）：%s", ev.device_code, ev.seq, exc)
        return

    with STATE.lock:
        STATE.inserted += 1
        STATE.pending_valid += 1


MQTT_CLIENT: mqtt.Client | None = None


def start_mqtt() -> None:
    global MQTT_CLIENT
    client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"rp-ingest-{os.getpid()}")
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_message = on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        client.loop_start()
    except Exception as exc:  # noqa: BLE001
        LOG.error("MQTT 首次连接失败（后台自动重试）：%s", exc)
        client.loop_start()
    MQTT_CLIENT = client


def stop_mqtt() -> None:
    if MQTT_CLIENT is not None:
        MQTT_CLIENT.loop_stop()
        MQTT_CLIENT.disconnect()


# ----------------------------------------------------------------- 批次刷新线程
def batch_flusher(interval: float = 10.0) -> None:
    while True:
        time.sleep(interval)
        with STATE.lock:
            raw, valid = STATE.pending_raw, STATE.pending_valid
            STATE.pending_raw = STATE.pending_valid = 0
        if raw or valid:
            try:
                dao.execute_write("data_import_batch", UPSERT_BATCH, {
                    "batch_no": STATE.batch_no,
                    "source_desc": f"MQTT {MQTT_TOPIC}",
                    "channel_count": len(DEVICE_MAP),
                    "raw_count": raw, "valid_count": valid,
                    "import_start": datetime.now(timezone.utc).astimezone(),
                    "handler": PROCESSOR,
                    "remark": "骨架栈：批次计数（truth_flag 由 M4 质量门晋升）",
                }, writer=WRITER)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("批次刷新失败：%s", exc)
                with STATE.lock:                      # 失败则退回计数，下轮重试
                    STATE.pending_raw += raw
                    STATE.pending_valid += valid


# ----------------------------------------------------------------- FastAPI
@asynccontextmanager
async def lifespan(_: FastAPI):
    dao.open()
    load_device_map()
    threading.Thread(target=batch_flusher, daemon=True).start()
    start_mqtt()
    LOG.info("M2 接入服务已启动：topic=%s schema=%s", MQTT_TOPIC, SCHEMA_VERSION)
    yield
    stop_mqtt()
    dao.close()


app = FastAPI(
    title="M2 接入服务（骨架版）",
    version="0.1.0",
    description="MQTT 过车事件 → 契约校验 → 双库落库 + 批次/质量日志。"
                "契约见 contracts/topics.yaml 与 contracts/messages/wim_axle.v1.schema.json",
    lifespan=lifespan,
)


@app.get("/healthz", summary="健康检查（含依赖自检）")
def healthz() -> dict[str, Any]:
    pg_ok, pg_msg = False, ""
    try:
        if not dao.ping():
            raise RuntimeError("PG ping 失败")
            cur.fetchone()
        pg_ok = True
    except Exception as exc:  # noqa: BLE001
        pg_msg = str(exc)[:200]

    with STATE.lock:
        snap = {
            "received": STATE.received, "inserted": STATE.inserted,
            "rejected": STATE.rejected, "duplicated": STATE.duplicated,
            "db_errors": STATE.db_errors, "last_message_at": STATE.last_message_at,
            "last_error": STATE.last_error, "batch_no": STATE.batch_no,
        }
    healthy = pg_ok and STATE.mqtt_connected
    return {
        "status": "ok" if healthy else "degraded",
        "deps": {
            "postgres": {"ok": pg_ok, "error": pg_msg},
            "mqtt": {"ok": STATE.mqtt_connected, "host": f"{MQTT_HOST}:{MQTT_PORT}",
                     "topic": MQTT_TOPIC},
        },
        "devices": len(DEVICE_MAP),
        "counters": snap,
    }


@app.get("/stats", summary="计数器快照")
def stats() -> dict[str, Any]:
    with STATE.lock:
        return {
            "received": STATE.received, "inserted": STATE.inserted,
            "rejected": STATE.rejected, "duplicated": STATE.duplicated,
            "db_errors": STATE.db_errors, "last_topic": STATE.last_topic,
            "last_message_at": STATE.last_message_at, "last_error": STATE.last_error,
        }


@app.get("/metrics", response_class=PlainTextResponse, summary="Prometheus 指标")
def metrics() -> str:
    with STATE.lock:
        lines = [
            "# TYPE rp_ingest_received_total counter",
            f"rp_ingest_received_total {STATE.received}",
            "# TYPE rp_ingest_inserted_total counter",
            f"rp_ingest_inserted_total {STATE.inserted}",
            "# TYPE rp_ingest_rejected_total counter",
            f"rp_ingest_rejected_total {STATE.rejected}",
            "# TYPE rp_ingest_duplicated_total counter",
            f"rp_ingest_duplicated_total {STATE.duplicated}",
            "# TYPE rp_ingest_db_errors_total counter",
            f"rp_ingest_db_errors_total {STATE.db_errors}",
            "# TYPE rp_ingest_mqtt_up gauge",
            f"rp_ingest_mqtt_up {1 if STATE.mqtt_connected else 0}",
        ]
    return "\n".join(lines) + "\n"


# ═══════════════════════════════════════════ 契约⑤：设计数据导入（HTTP 面）
# 把"IR → 落库"这条已经在测试里跑通的链路，接成一个能被 M9 页面调用的端点。
#
# 收的是**单个文件**（不是工程目录）：解析器 `parse(text, *, file=...)` 本来就只吃
# 文本，所以单个上传的文件可以直接解析。做法是把字节写进一个临时目录，再调
# `weidi.build_ir(tmpdir)` —— **复用整条既有链路**，而不是为单文件另写一遍。
# 好处是 IR 的形状、gaps 的分类（source_absent / parse_blocked / not_supported）
# 全部与目录导入一致：目录里没有的那些段照样老实记 source_absent。

#: 契约⑤ 的 schema 在镜像里的位置（由 Dockerfile COPY 进来）
IR_SCHEMA_PATH = pathlib.Path(
    os.getenv("IR_SCHEMA_PATH",
              "/app/contracts/design-import/road_geometry_ir.v0.3.schema.json"))

_ir_validator: Any = None


def _ir_schema_validator() -> Any:
    """懒加载契约⑤ 的校验器。**校验的是 IR 本身**，不是落库结果。"""
    global _ir_validator
    if _ir_validator is None:
        import jsonschema
        _ir_validator = jsonschema.Draft202012Validator(
            json.loads(IR_SCHEMA_PATH.read_text(encoding="utf-8")))
    return _ir_validator


#: 本端点认的后缀 = 已实现解析的 + 已知解不开的。
#  后者也放行：`build_ir` 会把它记成 parse_blocked 进 gaps，
#  让用户看到"这个文件我收到了、但按现有手段读不了"，而不是一个 400。
_ACCEPTED_SUFFIX = set(design_import._IMPLEMENTED_SUFFIX) | set(design_import._BLOCKED_SUFFIX)

#: 一次最多收多少个文件。一套纬地工程的段是有限的（已实现 12 段 + 已知解不开 6 段），
#  留出余量即可。**上限是必须有的**：没有它，一个请求就能把临时目录和内存塞满。
_MAX_FILES = 64
#: 单个文件与单次请求的字节上限。
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_BYTES = 256 * 1024 * 1024


@app.post("/v1/design/import", tags=["设计导入"],
          summary="上传一个纬地设计文件 → 解析成 IR → 落进 GE 表")
async def design_import_route(
    files: list[UploadFile] = File(..., description="纬地设计文件，可一次多个（.STA/.JD/.pm/.DMX/.SUP/.WID/.CTR/.HDM/…）"),
    section_id: int = Form(..., description="落到哪个路段（road_section.id）"),
    dry_run: bool = Form(False, description="true = 只预检（plan+verify），一行都不写"),
    batch_no: str | None = Form(None, description="批次号；留空则自动生成"),
) -> dict[str, Any]:
    """上传（一个或多个文件）→ IR → 落库。返回批次号、逐段计划/实写行数、告警与缺口。

    收多个文件不是图省事，是**必须的**：跨文件校验只在两段同时在场时才成立
    —— `.STA` 非整桩 ≡ 曲线特征点 ∪ {首末} 要 `.STA`+`.pm`；`.JD` 对 `.pm`
    的转向符号对质要两者都在；几何等级也从 L1 升到 L4 要横断面那几段都在。
    一次一个文件时，这些**全都跑不起来**。

    ⚠ 只经 `WriteDao`（本模块是七域业务数据的唯一写入方），越权表会直接抛
      `WriteGuardError` —— 这里**不 catch** 它：那是编程错误，不是用户输入问题，
      应当以 500 暴露出来，而不是伪装成 400 让用户去改文件。
    """
    # ① 数量与体量的硬上限。没有它，一个请求就能把临时目录塞满。
    if not files:
        raise HTTPException(status_code=400, detail="没有收到任何文件")
    if len(files) > _MAX_FILES:
        raise HTTPException(status_code=400,
                            detail=f"一次最多 {_MAX_FILES} 个文件，收到 {len(files)} 个")

    # ② 文件名只取 basename —— 上传方可以送 "../../etc/passwd" 这种，
    #    直接拿去拼临时目录路径就会写到目录外。这是**安全**问题，不是洁癖。
    #
    #    ⚠ 取了 basename 之后**重名就必须挡**：来自不同子目录的 a/x.STA 与 b/x.STA
    #      取完 basename 都是 x.STA，写进同一个临时目录就是**后者覆盖前者** ——
    #      又是一个静默丢数据。这里直接拒收，并点名是哪几个。
    named: list[tuple[str, bytes]] = []
    seen: dict[str, int] = {}
    # 收下但**不解析**的文件。分两种，性质完全不同：
    #   · `.cys` 这类「软件自身的参数」——**按设计**就不该进库（它描述"软件怎么画图"，
    #     不是"这条路是什么"）。静默跳过是可以的，但仍要报出来，免得用户以为导进去了。
    #   · 其余**未登记**的后缀——既没实现、也没登记为解不开。这可能是**真的在丢数据**
    #     （`.hda` 涵洞数据文件就是：它是正经工程数据，只是还没人管它）。必须显眼地说。
    #
    # ⚠ 为什么要收下而不是 400：**拖一整个工程目录是正常用法**，而一套真实工程里
    #   总会有几个没人管的后缀。因为一个 `.hda` 就把整批拒掉，等于逼用户手工挑文件 ——
    #   而"手工挑"正是最容易漏掉关键文件的做法。所以：收下、跳过、说清楚。
    skipped: list[dict[str, str]] = []
    for f in files:
        raw_name = pathlib.Path(f.filename or "").name
        if not raw_name:
            raise HTTPException(status_code=400, detail="有文件的名字是空的")
        suffix = pathlib.Path(raw_name).suffix.lower()
        data = await f.read()
        if len(data) > _MAX_FILE_BYTES:
            raise HTTPException(status_code=400,
                                detail=f"单个文件超过 {_MAX_FILE_BYTES // 1048576} MB：{raw_name}")
        seen[raw_name] = seen.get(raw_name, 0) + 1
        if suffix not in _ACCEPTED_SUFFIX:
            skipped.append({
                "name": raw_name,
                "reason": ("system_param" if suffix in design_import._SYSTEM_PARAM_SUFFIX
                           else "unregistered"),
                "detail": design_import._SYSTEM_PARAM_SUFFIX.get(suffix)
                          or f"未登记的后缀 {suffix!r}：既没有适配器，也没有登记为"
                             f"「存在但解不开」。**它里面可能有本工程的数据而没有被导入。**",
            })
            continue
        if not data:
            raise HTTPException(status_code=400, detail=f"文件是空的（0 字节）：{raw_name}")
        # 已知解不开的后缀（.bdm/.gtm/.dtm/.tsf/…）**不预读**：它们是二进制，
        # 解码必然失败。它们该走的是 build_ir 的 parse_blocked 分支 —— 记进 gaps，
        # 让用户看到"收到了但读不了"，而不是让整批挂掉。
        # ⚠ 我第一版对**所有**文件预读，于是拖一整个工程目录时被 .BDM 直接 400 ——
        #   而 .BDM 恰恰是"登记为解不开"的那一类。预读只该对**本该是文本**的文件做。
        named.append((raw_name, data))

    if not named:
        raise HTTPException(
            status_code=400,
            detail="收到 " + str(len(files)) + " 个文件，但没有一个能解析："
                   + "；".join(f"{k['name']}（{k['reason']}）" for k in skipped))

    dup_names = sorted(n for n, c in seen.items() if c > 1)
    if dup_names:
        raise HTTPException(
            status_code=400,
            detail="有重名文件：" + "、".join(dup_names)
                   + "。它们写进同一个临时目录会互相覆盖（后写的赢），"
                     "而覆盖是静默的 —— 请先确认这些文件是不是属于同一套工程。")

    total = sum(len(d) for _, d in named)
    if total > _MAX_TOTAL_BYTES:
        raise HTTPException(status_code=400,
                            detail=f"合计超过 {_MAX_TOTAL_BYTES // 1048576} MB（{total} 字节）")

    batch = batch_no or f"design-{datetime.now(timezone.utc):%Y%m%d%H%M%S}-{uuid.uuid4().hex[:6]}"

    # ③ 全部写进**同一个**临时目录，然后**复用目录导入的整条链路** ——
    #    这正是"一次多个文件"能自动获得跨文件校验的原因：build_ir 看到的就是
    #    一个完整的工程目录，它那几条跨文件动作（混版告警、转向符号对质、
    #    单元挂交点）全都照常生效。
    with tempfile.TemporaryDirectory(prefix="rp-design-") as td:
        encodings: dict[str, str] = {}
        for raw_name, data in named:
            path = pathlib.Path(td) / raw_name
            path.write_bytes(data)
            suffix = pathlib.Path(raw_name).suffix.lower()
            if suffix in design_import._BLOCKED_SUFFIX:
                # 已知解不开：不预读，交给 build_ir 记 parse_blocked。
                encodings[raw_name] = "(二进制，按已知缺口登记)"
                continue
            # 本该是文本的文件：这里试读一次，是为了把"读不了"落到**具体文件名**上
            # —— 只让 build_ir 去读的话，用户看到的是"某一段 blocked"，
            # 而真正的原因是"这个文件编码不对"，两者行动不同。
            # utf-8 优先、退 gbk（.PRJ 是 GBK）。
            try:
                _text, used_enc = base.read_text_any(path)
            except Exception as exc:                               # noqa: BLE001
                raise HTTPException(
                    status_code=400,
                    detail=f"读不出文本（既不是 UTF-8 也不是 GBK）：{raw_name} —— {exc}") from exc
            encodings[raw_name] = used_enc

        try:
            ir = weidi.build_ir(td)
        except ImportError_ as exc:
            raise HTTPException(status_code=400, detail=f"解析失败：{exc}") from exc

    # ③ 校验 IR 本身过不过契约⑤。落库器已经会挡坏数据，但那是**下游**；
    #    这里挡的是"我们产出的 IR 不符合自己声明的契约" —— 那是我们的 bug，
    #    也要以明确的信息暴露，而不是让它悄悄流进库里。
    errs = sorted(_ir_schema_validator().iter_errors(ir),
                  key=lambda e: list(e.absolute_path))
    if errs:
        first = errs[0]
        where = "/".join(str(p) for p in first.absolute_path) or "(根)"
        raise HTTPException(
            status_code=500,
            detail=f"内部错误：生成的 IR 不符合契约⑤（{len(errs)} 处），"
                   f"首处 {where}：{first.message}")

    # ④ 落库（多表同事务）
    try:
        report = design_import.load(
            ir, dao, section_id=section_id, batch_no=batch,
            source_desc=f"{raw_name}（{used_enc}）",
            writer=WRITER, dry_run=dry_run, strict=True)
    except LoadError as exc:
        raise HTTPException(status_code=400, detail=f"落库前检查未通过：{exc}") from exc
    except ImportError_ as exc:
        raise HTTPException(status_code=400, detail=f"解析/校验失败：{exc}") from exc

    # ⑤ 把"这个文件本身"的信息一并回给调用方：解析状态与缺口原因，
    #    不然用户只看 planned 行数，不知道其余段是"源里没有"还是"适配器没做"。
    report["files"] = [{"name": n, "encoding": encodings.get(n, ""), "bytes": len(d)}
                       for n, d in named]
    # 收到但没解析的文件。与 `files` 分开列：`files` 是"进了这份 IR 的"，
    # `skipped` 是"收到了但没进的"——混在一起会让人以为它们也解析了。
    report["skipped"] = skipped
    # 兼容单文件调用方：以前这里叫 "file"，现在多文件叫 "files"。
    # 只留一个名字会让人以为"只收了一个"，故两个都留，files 是权威。
    report["file"] = report["files"][0] if len(report["files"]) == 1 else None
    report["source"] = ir.get("source")
    report["gaps"] = ir.get("gaps")
    report["ir_warnings"] = ir.get("warnings")
    report["parsed_segments"] = sorted(
        k for k, v in (ir.get("segments") or {}).items() if v)
    return report
