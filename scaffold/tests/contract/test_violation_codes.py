"""违约分类契约测试

盯住三件事：
  ① 造数器注入的每类违约，分类码必须**精确且唯一**（不是都落到兜底码）
  ② 每种违约形态都能被分类（兜底码的出现频率应是"新增校验忘了分类"的信号）
  ③ **跨层命名一致**：M2 在接入时点求值的规则，与 M4 在批次时点求值的规则，
     同一条逻辑规则必须用**完全相同的码**——否则两边统计对不上。

为什么要单独测这个：接入服务原先把一切违约都记成 `contract_violation`，
实测库里 26 条违约全靠同一个码，其中 9 条是被误记的轴数不符、9 条真总重偏差、
8 条超速——**日志里完全分不出来**，排障的人不知道去调哪条校验。

跑法：
    uv run --with jsonschema --with pydantic --with pyyaml tests/contract/test_violation_codes.py
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import random
import sys

import yaml
from pydantic import ValidationError

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "services/ingest"))


def load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


MODELS = load(ROOT / "services/ingest/models.py", "ingest_models")
VIOL = load(ROOT / "services/ingest/violations.py", "ingest_violations")
SIM = load(ROOT / "simulator/wim_simulator.py", "wim_simulator")

BASE = {
    "device_code": "WIM01", "ts": "2026-09-15T10:00:00+08:00", "seq": 1,
    "schema_version": "wim_axle.v1",
    "payload": {
        "lane_no": 1, "direction": "up", "axle_type_code": "A2", "axle_num": 2,
        "speed_kmh": 70.0, "gross_weight_kg": 10000.0,
        "axles": [{"axle_seq": 1, "weight_kg": 5000.0},
                  {"axle_seq": 2, "weight_kg": 5000.0}],
    },
}


def code_of(obj: dict) -> str:
    try:
        MODELS.WimEvent.model_validate(obj)
        return "(未被拒)"
    except ValidationError as exc:
        return VIOL.classify(exc)[0]


# ---------------------------------------------------------------- 用例表
# (说明, 报文, 期望码)
SHAPE_CASES: list[tuple[str, dict, str]] = [
    ("字段类型错 device_code=None", {**BASE, "device_code": None}, "type.device_code"),
    ("必填字段真的缺失",
     {k: v for k, v in BASE.items() if k != "device_code"}, "missing.device_code"),
    ("版本号错误", {**BASE, "schema_version": "wim_axle.v2"}, "schema.schema_version"),
    ("未知字段", {**BASE, "payload": {**BASE["payload"], "whoops": 1}}, "schema.unexpected_field"),
    ("方向枚举错", {**BASE, "payload": {**BASE["payload"], "direction": "sideways"}}, "enum.direction"),
    ("轴型枚举错", {**BASE, "payload": {**BASE["payload"], "axle_type_code": "A9"}}, "enum.axle_type_code"),
    ("超速越界", {**BASE, "payload": {**BASE["payload"], "speed_kmh": 215.0}}, "range.speed_kmh"),
    ("轴数越界", {**BASE, "payload": {**BASE["payload"], "axle_num": 99, "axles": []}}, "range.axle_num"),
    ("车道号越界", {**BASE, "payload": {**BASE["payload"], "lane_no": 99}}, "range.lane_no"),
    ("轴重为负", {**BASE, "payload": {**BASE["payload"],
                 "axles": [{"axle_seq": 1, "weight_kg": -5.0}, {"axle_seq": 2, "weight_kg": 5000.0}]}},
     "range.axle_weight_kg"),
    ("axle_seq 不连续", {**BASE, "payload": {**BASE["payload"],
                        "axles": [{"axle_seq": 2, "weight_kg": 5000.0}, {"axle_seq": 3, "weight_kg": 5000.0}]}},
     "cross.axle_seq_continuity"),
    ("轴数与明细长度不符", {**BASE, "payload": {**BASE["payload"], "axle_num": 3}},
     "cross.axle_num_vs_axles"),
    ("总重与轴重之和偏差", {**BASE, "payload": {**BASE["payload"], "gross_weight_kg": 20000.0}},
     "cross.gross_vs_axle_sum"),
]


def main() -> int:
    fails: list[str] = []

    # ------------------------------------------------ 一、形态 → 码
    print("=" * 92)
    print("一、违约形态 → 分类码")
    print("=" * 92)
    print(f"{'违约形态':<30}{'期望码':<30}{'实测码':<30}结论")
    print("-" * 92)
    for name, obj, want in SHAPE_CASES:
        got = code_of(obj)
        ok = got == want
        print(f"{name:<28}{want:<30}{got:<30}{'✓' if ok else '✗'}")
        if not ok:
            fails.append(f"{name}: 期望 {want}，实测 {got}")

    # ------------------------------------------------ 二、造数器三类违约
    print()
    print("=" * 92)
    print("二、造数器注入的三类违约（每类必须落到各自的码，且码有区分度）")
    print("=" * 92)
    want_map = {"axle_num": "cross.axle_num_vs_axles",
                "gross_mismatch": "cross.gross_vs_axle_sum",
                "overspeed": "range.speed_kmh"}
    rng = random.Random(2026)
    seen: dict[str, dict[str, int]] = {}
    for i in range(300):
        ev = SIM.build_event("WIM01", i, rng, invalid=True)
        p = ev["payload"]
        if p["axle_num"] != len(p["axles"]):
            kind = "axle_num"
        elif p["speed_kmh"] > 200:
            kind = "overspeed"
        else:
            kind = "gross_mismatch"
        c = code_of(ev)
        seen.setdefault(kind, {})
        seen[kind][c] = seen[kind].get(c, 0) + 1
    for kind, want in want_map.items():
        d = seen.get(kind, {})
        dist = "，".join(f"{k}×{v}" for k, v in d.items()) or "（未产生）"
        good = list(d) == [want]
        print(f"  {kind:<18}{dist:<44}{'✓' if good else '✗ 应为 ' + want}")
        if not d:
            fails.append(f"{kind} 违约从未产生")
        elif not good:
            fails.append(f"{kind} 分类码错误：{list(d)}")
    if len(seen) != 3:
        fails.append(f"只产生 {len(seen)}/3 类违约")
    codes = {c for d in seen.values() for c in d}
    print(f"\n  三类违约共产生 {len(codes)} 个不同的码（{'✓ 有区分度' if len(codes) >= 3 else '✗ 码没有区分度'}）")
    if len(codes) < 3:
        fails.append("三类的码没有区分度")

    # ------------------------------------------------ 三、跨层命名一致
    print()
    print("=" * 92)
    print("三、跨层命名一致：M2（接入时点）与 M4（批次时点）的同名规则必须字面相同")
    print("=" * 92)
    rules = yaml.safe_load(
        (ROOT / "services/governance/config/quality_rules.yaml").read_text(encoding="utf-8"))
    m4_codes = {r["code"] for r in rules["rules"]}
    # M2 能产出的全部码
    m2_codes = {c for _, _, c in SHAPE_CASES} | codes

    overlap = m2_codes & m4_codes
    print(f"  M2 可产出 {len(m2_codes)} 个码；M4 规则集定义 {len(m4_codes)} 个码")
    print(f"  交集（同一条规则在两个层求值）：{sorted(overlap) or '无'}")
    # 同名是好事；真正的风险是"同一条规则两边叫不同的名字"——那没法自动检测，
    # 所以退而求其次：检查交集里的码在 M4 侧确实存在且已实现/已标定状态可查
    for c in sorted(overlap):
        r = next(x for x in rules["rules"] if x["code"] == c)
        print(f"    {c:<30} M4 侧 suite={r['suite']:<6} calibrated={r['calibrated']}")
    # 反向：M4 里那两条跨字段规则，M2 必须也能产出（否则两边的统计对不上）
    for c in ("cross.axle_num_vs_axles", "cross.gross_vs_axle_sum"):
        if c not in m2_codes:
            print(f"    ✗ M4 定义了 {c}，但 M2 产不出这个码——两层统计会对不上")
            fails.append(f"M2 缺少码 {c}")

    # ------------------------------------------------ 四、兜底码
    print()
    print("=" * 92)
    print("四、兜底码 contract_violation 不应出现在已知形态里（它只该接住未分类的新校验）")
    print("=" * 92)
    fallback = [n for n, obj, _ in SHAPE_CASES if code_of(obj) == "contract_violation"]
    print(f"  已知 {len(SHAPE_CASES)} 种形态中落到兜底码的：{fallback or '无 ✓'}")
    if fallback:
        fails.append(f"这些形态未分类：{fallback}")

    print()
    print("=" * 92)
    print("结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
