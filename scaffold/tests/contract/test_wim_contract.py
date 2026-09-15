"""契约一致性测试：JSON Schema（契约真源）↔ Pydantic 模型（代码实现）必须裁决一致。

运行（无需建环境）：
  cd /data/cy/shujuku/scaffold
  uv run --with jsonschema --with pydantic tests/contract/test_wim_contract.py

为什么必须两个实现都测：报文由设备/第三方产生，边缘侧（接入服务）用 Pydantic 校验，
而契约文档、数据集校验、自动化脚本用 JSON Schema。两者一旦漂移，就会出现
"文档说合法、服务说非法"的扯皮——这是多模块接入最常见的集成故障源。

本测试还刻意暴露一个事实：**JSON Schema 只能管"形状与取值域"，管不了跨字段业务规则**
（长度=轴数、总重=各轴之和、轴序连续）。这类规则必须由接入服务兜底，因此
"契约文件"与"接入服务校验"缺一不可——这正是骨架期要让学生亲手撞一次的认识。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import pathlib
import sys

import jsonschema
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "messages" / "wim_axle.v1.schema.json"
MODELS_PATH = ROOT / "modules" / "M2-ingest" / "models.py"


def load_models():
    spec = importlib.util.spec_from_file_location("ingest_models", MODELS_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ingest_models"] = mod
    spec.loader.exec_module(mod)
    return mod


SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
MODELS = load_models()

VALID = {
    "device_code": "WIM01",
    "ts": "2026-09-14T13:45:02.123+08:00",
    "seq": 1024,
    "schema_version": "wim_axle.v1",
    "payload": {
        "lane_no": 2,
        "direction": "down",
        "axle_type_code": "T5",
        "axle_num": 5,
        "speed_kmh": 68.4,
        "gross_weight_kg": 51_200.0,
        "plate_no": "闽A12345",
        "axles": [
            {"axle_seq": 1, "weight_kg": 6_400.0, "dist_mm": 3_600.0},
            {"axle_seq": 2, "weight_kg": 11_800.0, "dist_mm": 1_350.0},
            {"axle_seq": 3, "weight_kg": 11_500.0, "dist_mm": 1_350.0},
            {"axle_seq": 4, "weight_kg": 10_900.0, "dist_mm": 4_500.0},
            {"axle_seq": 5, "weight_kg": 10_600.0, "dist_mm": 1_350.0},
        ],
    },
}


def mutate(**changes) -> dict:
    ev = copy.deepcopy(VALID)
    payload_changes = changes.pop("payload", None)
    ev.update(changes)
    if payload_changes:
        ev["payload"].update(payload_changes)
    return ev


# (用例名, 报文, JSON Schema 期望, Pydantic 期望)
# Schema 期望=True 而 Pydantic 期望=False 的用例，即"Schema 表达力边界"。
CASES: list[tuple[str, dict, bool, bool]] = [
    ("合法报文", VALID, True, True),
    ("超速（>200 km/h）", mutate(payload={"speed_kmh": 240.0}), False, False),
    ("车道号越界（0）", mutate(payload={"lane_no": 0}), False, False),
    ("版本不匹配", mutate(schema_version="wim_axle.v2"), False, False),
    ("缺少 device_code", {k: v for k, v in VALID.items() if k != "device_code"}, False, False),
    ("多余字段（信封层）", mutate(extra_field=1), False, False),
    ("轴型不在字典内", mutate(payload={"axle_type_code": "T9"}), False, False),
    ("【Schema 边界】轴数与 axles 长度不符", mutate(payload={"axle_num": 4}), True, False),
    ("【Schema 边界】总重与轴重之和偏差 >2%",
     mutate(payload={"gross_weight_kg": 60_000.0}), True, False),
    ("【Schema 边界】轴序不连续", mutate(payload={
        "axles": [{"axle_seq": 1, "weight_kg": 6_400.0},
                  {"axle_seq": 3, "weight_kg": 44_800.0}],
        "axle_num": 2}), True, False),
]


def schema_accepts(ev: dict) -> bool:
    try:
        jsonschema.validate(ev, SCHEMA)
        return True
    except jsonschema.ValidationError:
        return False


def model_accepts(ev: dict) -> bool:
    try:
        MODELS.WimEvent.model_validate(ev)
        return True
    except ValidationError:
        return False


def main() -> int:
    fails: list[str] = []
    print(f"{'用例':<34}{'期望(schema/model)':<20}{'实测':<16}结论")
    print("-" * 92)
    for name, ev, want_s, want_m in CASES:
        got_s, got_m = schema_accepts(ev), model_accepts(ev)
        ok = (got_s == want_s) and (got_m == want_m)
        print(f"{name:<32}{str(want_s) + ' / ' + str(want_m):<20}"
              f"{str(got_s) + ' / ' + str(got_m):<16}{'✓' if ok else '✗ 不符预期'}")
        if not ok:
            fails.append(name)

    # 反向确认：Schema 管形状、模型管业务，两者互补而非重复
    shape_cases = [c for c in CASES if not c[0].startswith("【")]
    boundary_cases = [c for c in CASES if c[0].startswith("【")]
    print(f"\n形状/取值域用例 {len(shape_cases)} 条：两个实现裁决应完全一致")
    print(f"业务规则用例 {len(boundary_cases)} 条：仅 Pydantic 能判（JSON Schema 表达力边界，"
          f"故接入服务的业务校验不可省略）")

    # 派生量自检
    ev = MODELS.WimEvent.model_validate(VALID)
    esal, rate, overload = ev.esal(), ev.overload_rate(), ev.is_overload()
    expect_rate = round((51_200 - 43_000) / 43_000 * 100, 2)      # T5 限值 43 t
    print(f"\n派生量：ESAL={esal} 超载率={rate}%（期望 {expect_rate}%）超载={overload}")
    if not overload:
        fails.append("超载判定")
    if abs(rate - expect_rate) > 0.01:
        fails.append("超载率公式")
    if esal <= 0:
        fails.append("ESAL 计算")

    print("\n结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
