#!/usr/bin/env python3
"""把 Chrono 仿真导出的轮载 CSV 变成一条真实的 WIM 过车报文（wim_axle.v1），
投进骨架栈，看它一路走到库里。

    Chrono 轮载 CSV ──(本文件)──▶ wim_axle.v1 报文 ──MQTT──▶ rp-ingest ──▶ wim_axle_record

★ 这一步在做什么，为什么需要它
  `demo_vehicle_on_crg --csv` 导出的是**轮载**：每根轴左右各一轮，四个分力
  （垂向/侧向/纵向）加轮心高度。而 WIM 设备报的是**轴载** —— 一根轴一个数。
  中间那次「左右两轮相加」不是格式转换，是物理量的换算，所以它必须在一处
  写明白，而不是散在某个脚本的列表推导里。

★ 为什么不删掉中间那段拟合：轮载是横向分布，轴载是它的和
  a0_L_Fz + a0_R_Fz = 前轴轴载。这是**定义**，不是近似。所以本文件不做任何
  拟合、不做任何平滑：读进来是 4 个数，出去是 2 个数，其余全是单位换算。

用法
----
    # 只看报文，不发送（默认）
    uv run --with jsonschema python3 simulator/crg/chrono_to_wim.py \\
        ../../artifacts/sim/wheel_loads.csv --station-m 2900

    # 真发送
    uv run --with paho-mqtt --with jsonschema python3 simulator/crg/chrono_to_wim.py \\
        ../../artifacts/sim/wheel_loads.csv --station-m 2900 \\
        --publish --host localhost --port 18883

★ 溯源：报文里**没有**放「这是仿真」的位置
  `wim_axle.v1.schema.json` 三层（信封 / payload / axles[]）都是
  `additionalProperties: false`。所以不能加一个 `simulated: true` 之类的字段。
  唯一的溯源通道是 `device_code` —— ingest 会把它写成
  `data_source = 'mqtt:<device_code>'`。因此本文件默认发往 `SIMWIM01`，
  **不要**图省事发到 `WIM01` 名下（见下面「设备」一节）。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import pathlib
import re
import sys
from datetime import datetime, timezone

# ★ 标准重力加速度。kN → kg 必须过这一关：报文里的 weight_kg 是**质量**，
#   仿真给的是**力**。用 9.8 会在 40 t 上带来 40 kg 的偏差，恰恰落在
#   models.py 那条 2% 总重一致性检查的量级上 —— 不许四舍五入。
G = 9.80665

# ★ 轴型编码：轴数 → 车型代号。这张表来自报文的枚举
#   （A2|T3|T4|T5|T6），不是猜的。查不到就拒绝，不硬凑 ——
#   一个 7 轴车的报文宁可发不出去，也不要错标成 T6。
AXLE_COUNT_TO_TYPE = {2: "A2", 3: "T3", 4: "T4", 5: "T5", 6: "T6"}

# 轮载列名形如 a0_L_Fz_kN / a1_R_Fz_kN
WHEEL_COL = re.compile(r"^a(?P<axle>\d+)_(?P<side>[LR])_Fz_kN$")

# ★ 轴距从**车型定义文件**里读，不写死。
#   写死 3.378 的后果：换车之后这个数没人会记得改，而它看起来完全合理。
DEFAULT_VEHICLE_JSON = pathlib.Path(
    "/home/zhanghe/Packages/chrono/share/chrono/data/vehicle/hmmwv/vehicle/HMMWV_Vehicle.json"
)


def read_wheelbase_mm(vehicle_json: pathlib.Path) -> float:
    """从车型 JSON 读轴距（mm）。读不到就拒绝，不返回默认值。"""
    try:
        data = json.loads(vehicle_json.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SystemExit(
            f"读不到车型文件：{vehicle_json}\n"
            f"  轴距是报文里 dist_mm 的唯一来源，缺了它宁可失败也不许编一个。\n"
            f"  原始错误：{exc}"
        ) from exc
    wb = data.get("Wheelbase")
    if not isinstance(wb, (int, float)) or wb <= 0:
        raise SystemExit(
            f"{vehicle_json} 里没有可用的 Wheelbase 字段（读到 {wb!r}）。"
            f"拒绝继续 —— 编一个轴距会让报文通过所有检查却全是错的。"
        )
    return float(wb) * 1000.0


def parse_wheel_row(row: dict[str, str]) -> dict[int, dict[str, float]]:
    """一行 CSV → {轴号: {'L': Fz_kN, 'R': Fz_kN}}。"""
    axles: dict[int, dict[str, float]] = {}
    for key, raw in row.items():
        m = WHEEL_COL.match(key or "")
        if not m or raw in (None, ""):
            continue
        a = int(m.group("axle"))
        axles.setdefault(a, {})[m.group("side")] = float(raw)
    if not axles:
        raise SystemExit("这一行里一个 aN_{L,R}_Fz_kN 列都没有，CSV 表头对不上。")
    for a, sides in sorted(axles.items()):
        missing = {"L", "R"} - set(sides)
        if missing:
            # ★ 半根轴不许静默通过：缺一个轮会让轴载凭空少一半，
            #   而总重一致性检查（≤2%）此时可能仍然过得去 —— 因为
            #   gross 也是按缺了轮的和算的。这种错必须在这里挡住。
            raise SystemExit(f"轴 a{a} 缺 {'/'.join(sorted(missing))} 轮，拒绝按半边轴出报文。")
    return axles


def pick_row(rows: list[dict[str, str]], station_m: float) -> dict[str, str]:
    """选s_m 最接近 station_m 的那一行。"""
    best, best_d = None, math.inf
    for r in rows:
        try:
            d = abs(float(r["s_m"]) - station_m)
        except (KeyError, TypeError, ValueError):
            continue
        if d < best_d:
            best, best_d = r, d
    if best is None:
        raise SystemExit("CSV 里没有可用的 s_m 列。")
    return best


def build_envelope(
    row: dict[str, str],
    *,
    device_code: str,
    lane_no: int,
    direction: str,
    wheelbase_mm: float,
    seq: int,
    ts: str,
) -> dict:
    axles = parse_wheel_row(row)

    detail = []
    for i, a in enumerate(sorted(axles)):
        kn = axles[a]["L"] + axles[a]["R"]
        detail.append(
            {
                "axle_seq": i + 1,                       # 契约要求从 1 连续
                "weight_kg": round(kn * 1000.0 / G, 1),
                "group_seq": i + 1,                      # 两轴车：每轴自成一组
                "group_weight_kg": round(kn * 1000.0 / G, 1),
                # ★ 首轴 0，其余按车型轴距递推。真实 WIM 报的是**相邻轴距**，
                #   这里两轴车只有一段，所以第 2 轴就是整车轴距。
                "dist_mm": 0.0 if i == 0 else round(wheelbase_mm, 1),
            }
        )

    n = len(detail)
    if n not in AXLE_COUNT_TO_TYPE:
        raise SystemExit(
            f"仿真出来 {n} 根轴，报文枚举里没有对应车型代号"
            f"（有 {sorted(AXLE_COUNT_TO_TYPE)}）。拒绝硬凑一个。"
        )

    gross = round(sum(d["weight_kg"] for d in detail), 1)
    speed_kmh = round(abs(float(row["speed_mps"])) * 3.6, 2)

    return {
        "device_code": device_code,
        "ts": ts,
        "seq": seq,
        "schema_version": "wim_axle.v1",
        "payload": {
            "lane_no": lane_no,
            "direction": direction,
            "axle_type_code": AXLE_COUNT_TO_TYPE[n],
            "axle_num": n,
            "speed_kmh": speed_kmh,
            "gross_weight_kg": gross,
            # ★ 车牌留空：Chrono 里那辆 HMMWV 没有号牌。编一个「闽A·XXXXX」
            #   会让这条记录看起来比它实际知道的更多。契约允许 null。
            "plate_no": None,
            "axles": detail,
        },
    }


def self_check(env: dict, row: dict[str, str]) -> list[str]:
    """在发出去之前，把能自己核的先核一遍。返回差错清单（空=通过）。"""
    bad: list[str] = []
    pl = env["payload"]

    seqs = [a["axle_seq"] for a in pl["axles"]]
    if seqs != list(range(1, len(seqs) + 1)):
        bad.append(f"axle_seq 不连续：{seqs}")

    if pl["axle_num"] != len(pl["axles"]):
        bad.append(f"axle_num={pl['axle_num']} 与 axles 条数 {len(pl['axles'])} 不符")

    s = sum(a["weight_kg"] for a in pl["axles"])
    if s > 0 and abs(pl["gross_weight_kg"] - s) / s > 0.02:
        bad.append(f"总重与轴重之和偏差 >2%（{pl['gross_weight_kg']} vs {s:.1f}）")

    # ★ 反向核对：把轴载加回去，和 CSV 四个轮子的总和比。
    #   这是唯一能证明「轮载→轴载」这一步没丢数、没重复计的办法。
    #
    #   容差必须**跟着舍入粒度走**：gross 是各轴重（已四舍五入到 0.1 kg）之和，
    #   而这里是未舍入的总和。n 根轴最多差 0.05n kg。用 1e-9 之类的紧容差会
    #   永远判红，而报错文本里两个数**打印出来一模一样** —— 那种失败说明的是
    #   检查写错了，不是数据错了。（第一版就是 1e-9，实测卡在这里。）
    total_kn = sum(
        float(v) for k, v in row.items() if WHEEL_COL.match(k or "") and v not in (None, "")
    )
    back_kg = total_kn * 1000.0 / G
    tol = 0.05 * len(pl["axles"]) + 1e-6
    if back_kg > 0 and abs(pl["gross_weight_kg"] - back_kg) > tol:
        bad.append(
            f"总重回算不符：报文 {pl['gross_weight_kg']:.4f} kg，"
            f"CSV 四轮合计 {back_kg:.4f} kg，差 {abs(pl['gross_weight_kg'] - back_kg):.4f} kg "
            f"（容差 {tol:.4f}）"
        )

    # ★ 轴距得真读进来。两轴车第 2 轴的 dist_mm 就是整车轴距，为 0 说明
    #   车型文件没读到（或读到了空值）—— 而 0 是一个**看起来合法**的数。
    if len(pl["axles"]) >= 2 and pl["axles"][1]["dist_mm"] <= 0:
        bad.append("第二轴 dist_mm 为 0 —— 轴距没读进来")

    return bad


def validate_schema(env: dict, schema_path: pathlib.Path) -> list[str]:
    """按契约校验。jsonschema 不在就明说跳过，不假装通过。"""
    try:
        import jsonschema
    except ImportError:
        return ["（跳过 schema 校验：没装 jsonschema —— 这一次不算通过）"]
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    try:
        jsonschema.validate(env, schema)
    except jsonschema.ValidationError as exc:
        return [f"schema 校验失败：{exc.message}（在 {list(exc.absolute_path)}）"]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Chrono 轮载 CSV → wim_axle.v1 报文")
    ap.add_argument("csv_path", type=pathlib.Path)
    ap.add_argument("--station-m", type=float, required=True,
                    help="取 s_m 最接近这个桩号（米）的那一行")
    ap.add_argument("--device-code", default="SIMWIM01",
                    help="★ 默认发往仿真专用设备。发到 WIM01 会让记录落在滨海大通道上，而仿真跑的是毕设那条路")
    ap.add_argument("--lane", type=int, default=2)
    ap.add_argument("--direction", default="up", choices=["up", "down"])
    ap.add_argument("--vehicle-json", type=pathlib.Path, default=DEFAULT_VEHICLE_JSON)
    ap.add_argument("--seq", type=int, default=None,
                    help="报文序号。默认取整桩号 —— 去重键是 device_code+seq，"
                         "所以同一个桩号重发不会重复入库")
    ap.add_argument("--schema", type=pathlib.Path,
                    default=pathlib.Path(__file__).resolve().parents[2]
                    / "contracts" / "messages" / "wim_axle.v1.schema.json")
    ap.add_argument("--publish", action="store_true", help="真发送（默认只打印）")
    ap.add_argument("--host", default="localhost")
    ap.add_argument("--port", type=int, default=18883)
    ap.add_argument("--topic", default="g228/lo/simwim01/axle")
    args = ap.parse_args()

    rows = list(csv.DictReader(args.csv_path.open(encoding="utf-8")))
    if not rows:
        raise SystemExit(f"{args.csv_path} 是空的")
    row = pick_row(rows, args.station_m)

    wheelbase_mm = read_wheelbase_mm(args.vehicle_json)
    seq = args.seq if args.seq is not None else int(round(float(row["s_m"])))
    # ★ ts 是**发布时刻**，不是仿真时刻。契约三层都 additionalProperties:false，
    #   没有放仿真时刻的位置 —— 仿真时刻（t_s/s_m）只留在本目录的日志与 CSV 里。
    ts = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    env = build_envelope(
        row,
        device_code=args.device_code,
        lane_no=args.lane,
        direction=args.direction,
        wheelbase_mm=wheelbase_mm,
        seq=seq,
        ts=ts,
    )

    pl = env["payload"]
    print(f"CSV 行：t={row['t_s']}s  s={row['s_m']}m  "
          f"车速={row['speed_mps']}m/s  航向={row['heading_deg']}°")
    print(f"车型轴距（读自 {args.vehicle_json.name}）：{wheelbase_mm:.1f} mm")
    print(f"轴数 {pl['axle_num']} → 代号 {pl['axle_type_code']}"
          f"（{pl['axle_num']} 轴映射到 {pl['axle_type_code']}）")
    for a in pl["axles"]:
        print(f"  轴{a['axle_seq']}  {a['weight_kg']:9.1f} kg   距前轴 {a['dist_mm']:.0f} mm")
    print(f"总重 {pl['gross_weight_kg']:.1f} kg   车速 {pl['speed_kmh']} km/h")

    problems = validate_schema(env, args.schema) + self_check(env, row)
    if problems:
        print("\n自检没通过：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    print("\n自检通过：schema 合规、轴序连续、总重与轴重之和一致、总重与四轮合计回算一致。")

    if not args.publish:
        print(f"\n（只打印不发送）报文 topic={args.topic}：")
        print(json.dumps(env, ensure_ascii=False, indent=2))
        return 0

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        print("要发的话得装 paho-mqtt：uv run --with paho-mqtt …", file=sys.stderr)
        return 70

    body = json.dumps(env, ensure_ascii=False)
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"chrono-wim-{seq}")
    c.connect(args.host, args.port, 30)
    c.loop_start()
    info = c.publish(args.topic, body, qos=1)
    info.wait_for_publish(timeout=10)
    c.loop_stop()
    c.disconnect()
    if info.rc != mqtt.MQTT_ERR_SUCCESS:
        print(f"发布失败 rc={info.rc}", file=sys.stderr)
        return 1
    print(f"\n已发布 {len(body)} 字节 → {args.topic}  seq={seq} device={args.device_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
