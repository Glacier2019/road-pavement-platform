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
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from paho.mqtt import client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
from psycopg_pool import ConnectionPool
from pydantic import ValidationError

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

pool = ConnectionPool(PG_DSN, min_size=1, max_size=4, open=False, timeout=15,
                      kwargs={"application_name": "rp-ingest"})

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
MAP_SQL = """
SELECT si.serial_no, si.cross_section_id, sc.id AS channel_id,
       sc.quantity_code, sc.channel_no
FROM sensor_install si
JOIN sensor_channel sc ON sc.install_id = si.id
WHERE si.status = 'active'
"""

INSERT_RECORD = """
INSERT INTO wim_axle_record
  (cross_section_id, pass_time, lane_no, direction, axle_type_code, axle_num,
   speed_kmh, gross_weight_kg, overload_flag, overload_rate, esal,
   plate_no, data_source, quality_code)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OK')
RETURNING id
"""

INSERT_DETAIL = """
INSERT INTO wim_axle_detail
  (record_id, pass_time, axle_seq, group_seq, axle_weight_kg, group_weight_kg, axle_dist_mm)
VALUES (%s, %s, %s, %s, %s, %s, %s)
"""

UPSERT_BATCH = """
INSERT INTO data_import_batch
  (batch_no, source_type, source_desc, channel_count, raw_count, valid_count,
   truth_flag, import_start, import_end, quality_code, handler, remark)
VALUES (%s, 'mqtt', %s, %s, %s, %s, false, %s, now(), 'OK', %s, %s)
ON CONFLICT (batch_no) DO UPDATE
SET raw_count   = data_import_batch.raw_count + EXCLUDED.raw_count,
    valid_count = data_import_batch.valid_count + EXCLUDED.valid_count,
    import_end  = now()
"""

INSERT_DQL = """
INSERT INTO data_quality_log
  (channel_id, period_start, period_end, raw_count, valid_count,
   issue_code, issue_desc, action_code, process_time, processor)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), %s)
"""


def load_device_map() -> None:
    """从 PG 读设备注册表（serial_no → 断面/通道）。P1 改为变更通知刷新。"""
    try:
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(MAP_SQL)
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("设备映射加载失败（稍后重试）：%s", exc)
        return

    mapping: dict[str, dict[str, Any]] = {}
    for serial_no, cross_section_id, channel_id, quantity_code, channel_no in rows:
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
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(INSERT_DQL, (
            dev["channel_id"], event_ts, event_ts, 1, 0,
            issue, desc[:500], "mark", PROCESSOR,
        ))


def insert_event(ev: WimEvent, dev: dict[str, Any]) -> None:
    pass_time = ev.ts
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(INSERT_RECORD, (
                dev["cross_section_id"], pass_time, ev.payload.lane_no, ev.payload.direction,
                ev.payload.axle_type_code, ev.payload.axle_num, ev.payload.speed_kmh,
                ev.payload.gross_weight_kg, ev.is_overload(), ev.overload_rate(),
                ev.esal(), ev.payload.plate_no, f"mqtt:{ev.device_code}",
            ))
            record_id = cur.fetchone()[0]
            cur.executemany(INSERT_DETAIL, [
                (record_id, pass_time, a.axle_seq, a.group_seq, a.weight_kg,
                 a.group_weight_kg, a.dist_mm)
                for a in ev.payload.axles
            ])
        conn.commit()


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
                with pool.connection() as conn, conn.cursor() as cur:
                    cur.execute(UPSERT_BATCH, (
                        STATE.batch_no, f"MQTT {MQTT_TOPIC}", len(DEVICE_MAP),
                        raw, valid, datetime.now(timezone.utc).astimezone(), PROCESSOR,
                        "骨架栈：批次计数（truth_flag 由 M4 质量门晋升）",
                    ))
                    conn.commit()
            except Exception as exc:  # noqa: BLE001
                LOG.warning("批次刷新失败：%s", exc)
                with STATE.lock:                      # 失败则退回计数，下轮重试
                    STATE.pending_raw += raw
                    STATE.pending_valid += valid


# ----------------------------------------------------------------- FastAPI
@asynccontextmanager
async def lifespan(_: FastAPI):
    pool.open()
    load_device_map()
    threading.Thread(target=batch_flusher, daemon=True).start()
    start_mqtt()
    LOG.info("M2 接入服务已启动：topic=%s schema=%s", MQTT_TOPIC, SCHEMA_VERSION)
    yield
    stop_mqtt()
    pool.close()


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
        with pool.connection(timeout=3) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
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
