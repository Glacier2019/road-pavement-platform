"""WIM 造数器（骨架版）——在真设备到场前驱动整条链路。

用法（宿主直接跑，不用建环境）：
  uv run --with paho-mqtt simulator/wim_simulator.py \
      --host localhost --port 18883 --count 20 --interval 1.0

  # 只看报文不发送（无 broker 也能演示）
  uv run --with paho-mqtt simulator/wim_simulator.py --dry-run --count 3

设计：报文严格按 contracts/messages/wim_axle.v1.schema.json 生成；
      --invalid-rate 按比例注入"契约违约报文"，用来验证质量门与拒收路径。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone

AXLE_TYPES = {
    "A2": 2, "T3": 3, "T4": 4, "T5": 5, "T6": 6,
}
# 轴重分布参考：按车型限值反推（GB1589 限值 A2=18t / T3=25t / T4=31t / T5=43t / T6=49t）
# 均值取限值的 ~95%，σ=25% → 超载成为"少数但常态"的现象，与 G228 实况相符
AXLE_MEAN_KG = {
    "A2": 6_000.0,    # 2 轴，总重约 12 t
    "T3": 7_500.0,    # 3 轴，约 22.5 t
    "T4": 7_400.0,    # 4 轴，约 29.6 t
    "T5": 8_000.0,    # 5 轴，约 40 t
    "T6": 7_900.0,    # 6 轴，约 47.4 t
}
SCHEMA_VERSION = "wim_axle.v1"


def build_event(device_code: str, seq: int, rng: random.Random,
                invalid: bool = False) -> dict:
    axle_type = rng.choices(["A2", "T3", "T4", "T5", "T6"], weights=[35, 15, 10, 25, 15])[0]
    n = AXLE_TYPES[axle_type]
    weights = [max(500.0, rng.gauss(AXLE_MEAN_KG[axle_type], AXLE_MEAN_KG[axle_type] * 0.25))
               for _ in range(n)]
    gross = round(sum(weights), 1)
    payload = {
        "lane_no": rng.randint(1, 4),
        "direction": rng.choice(["up", "down"]),
        "axle_type_code": axle_type,
        "axle_num": n,
        "speed_kmh": round(rng.gauss(72, 12), 1),
        "gross_weight_kg": gross,
        "plate_no": rng.choice([None, f"闽A{rng.randint(10000, 99999)}"]),
        "axles": [
            {"axle_seq": i + 1, "weight_kg": round(w, 1),
             "dist_mm": 0.0 if i == 0 else round(rng.gauss(3600, 200), 0)}
            for i, w in enumerate(weights)
        ],
    }

    if invalid:                        # 注入违约（随机一种，覆盖质量门的三条分支）
        kind = rng.choice(["axle_num", "gross_mismatch", "overspeed"])
        if kind == "axle_num":         # 轴数与 axles 长度不符
            payload["axle_num"] = n + 1
            payload["axles"].append({"axle_seq": n + 1, "weight_kg": 5_000.0, "dist_mm": 3_600.0})
        elif kind == "gross_mismatch":  # 总重与轴重之和偏差 15%（>2% 阈值）
            payload["gross_weight_kg"] = round(gross * 1.15, 1)
        else:                           # 超速越界（>200 km/h，JSON Schema 也能拒）
            payload["speed_kmh"] = 215.0

    return {
        "device_code": device_code,
        "ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds"),
        "seq": seq,
        "schema_version": SCHEMA_VERSION,
        "payload": payload,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="WIM 过车报文造数器（骨架栈）")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=18883,
                    help="本机 1883 可能被占用，骨架栈默认映射 18883")
    ap.add_argument("--topic", default="g228/lo/wim01/axle")
    ap.add_argument("--device-code", default="WIM01",
                    help="必须已在 sensor_install.serial_no 注册")
    ap.add_argument("--count", type=int, default=10, help="0=不停止")
    ap.add_argument("--interval", type=float, default=1.0, help="秒/条")
    ap.add_argument("--invalid-rate", type=float, default=0.1, help="契约违约报文比例")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true", help="只打印不发送")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    client = None
    if not args.dry_run:
        try:
            from paho.mqtt import client as mqtt
        except ImportError:
            print("缺少 paho-mqtt：uv run --with paho-mqtt 或 pip install paho-mqtt",
                  file=sys.stderr)
            return 2
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                             client_id=f"wim-sim-{rng.randint(1000, 9999)}")
        client.connect(args.host, args.port, 60)
        client.loop_start()

    sent = ok = bad = 0
    try:
        while args.count == 0 or sent < args.count:
            invalid = rng.random() < args.invalid_rate
            ev = build_event(args.device_code, seq=sent, rng=rng, invalid=invalid)
            if client is None:
                print(json.dumps(ev, ensure_ascii=False))
            else:
                client.publish(args.topic, json.dumps(ev, ensure_ascii=False), qos=1)
                flag = "违约" if invalid else "正常"
                print(f"[{sent:>4}] {flag} {ev['payload']['axle_type_code']} "
                      f"{ev['payload']['axle_num']}轴 {ev['payload']['gross_weight_kg']/1000:.1f}t "
                      f"{ev['payload']['speed_kmh']:.0f}km/h → {args.topic}")
            sent += 1
            bad += invalid
            ok += not invalid
            if args.interval:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已中断")
    finally:
        if client is not None:
            time.sleep(0.5)
            client.loop_stop()
            client.disconnect()

    print(f"\n合计 {sent} 条（正常 {ok} / 故意违约 {bad}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
