"""校验"造数器输出 ⊆ 契约"：模拟器是设备仿真，它的输出必须完全合法。

运行：
  cd /data/cy/shujuku/scaffold
  uv run --with jsonschema --with pydantic tests/contract/test_simulator_contract.py

为什么单独测造数器：真设备到场前，造数器就是"唯一的数据来源"。若造数器产出的报文
本身违反契约，P1 全组都会围着假问题打转。同时要确认 `--invalid-rate` 注入的违约报文
**确实会被拒**——否则质量门永远测不到。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import random
import sys

import jsonschema
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parents[2]


def load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SCHEMA = json.loads((ROOT / "contracts/messages/wim_axle.v1.schema.json").read_text(encoding="utf-8"))
MODELS = load(ROOT / "services/ingest/models.py", "ingest_models")
SIM = load(ROOT / "simulator/wim_simulator.py", "wim_simulator")


def schema_ok(ev: dict) -> bool:
    try:
        jsonschema.validate(ev, SCHEMA)
        return True
    except jsonschema.ValidationError:
        return False


def model_ok(ev: dict) -> bool:
    try:
        MODELS.WimEvent.model_validate(ev)
        return True
    except ValidationError:
        return False


def main() -> int:
    fails: list[str] = []
    total = valid_seen = 0

    # 1) 正常报文必须 100% 合法（含字段分布、边界轴型/轴数）
    for seed in (1, 2, 3):
        rng = random.Random(seed)
        for i in range(150):
            ev = SIM.build_event("WIM01", i, rng, invalid=False)
            total += 1
            if not (schema_ok(ev) and model_ok(ev)):
                fails.append(f"seed={seed} seq={i} 正常报文不合规")
            else:
                valid_seen += 1
    print(f"正常报文：{valid_seen}/{total} 合规")

    # 2) 注入的违约报文：必须被 Pydantic 拒（Schema 按设计放行）
    rng = random.Random(99)
    rejected = schema_pass = 0
    for i in range(50):
        ev = SIM.build_event("WIM01", i, rng, invalid=True)
        if not model_ok(ev):
            rejected += 1
        if schema_ok(ev):
            schema_pass += 1
    print(f"违约报文：{rejected}/50 被接入层拒收（其中 {schema_pass} 条对 JSON Schema 仍合法"
          f"——再次印证 Schema 的表达力边界）")
    if rejected != 50:
        fails.append("违约报文未被拒收")

    # 3) 造数器输出可序列化（能真发出去）
    ev = SIM.build_event("WIM01", 1, random.Random(7))
    payload = json.dumps(ev, ensure_ascii=False)
    if json.loads(payload)["schema_version"] != "wim_axle.v1":
        fails.append("序列化后版本字段异常")
    print(f"序列化：{len(payload)} 字节，可直接 publish")

    # 4) 轴重/轴数分布合理性（G228 重载为主，5–6 轴应占相当比例）
    rng = random.Random(5)
    from collections import Counter
    dist = Counter(SIM.build_event("WIM01", i, rng)["payload"]["axle_type_code"] for i in range(1000))
    print("轴型分布(1000 条)：" + "，".join(f"{k}={v}" for k, v in sorted(dist.items())))
    heavy = sum(v for k, v in dist.items() if k in ("T5", "T6"))
    if heavy < 200:
        fails.append(f"重载车型占比过低({heavy/1000:.0%})，与 G228 重载特征不符")

    print("\n结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails[:3]}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
