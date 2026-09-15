"""M4 数据治理 · 真数据门 —— 契约测试（接入五件套第 5 件）

覆盖"应通过/应拒绝"两侧，且**离线可跑**（不依赖 Docker、不依赖数据库）。

与既有三个契约测试的分工：
* 契约① 报文（test_wim_contract.py）—— M2
* 契约② DDL   （test_dao_contract.py）—— M3
* 契约③ 服务  （test_api_routes.py）  —— M6
* **本文件 = M4 的规则契约与晋升契约**

本文件刻意多做两件事（既有测试没有的，也是本项目踩过的坑）：
1. **拿真实配置文件反向验证 schema**：`config/quality_rules.yaml` 必须符合
   `quality_rule.v0.1.schema.json`。否则契约是契约、配置是配置，两者脱钩。
2. **反向确认检查本身会失败**：每个断言都用一条已知不合法的输入反证。
   一个永远不会失败的检查，比没有检查更糟。

跑法：
    uv run --with jsonschema --with pyyaml tests/contract/test_governance_contract.py
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys

import jsonschema
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
RULE_SCHEMA_PATH = ROOT / "contracts" / "governance" / "quality_rule.v0.1.schema.json"
PROMO_SCHEMA_PATH = ROOT / "contracts" / "governance" / "promotion.v0.1.schema.json"
RULES_YAML_PATH = ROOT / "modules" / "M4-governance" / "config" / "quality_rules.yaml"

RULE_SCHEMA = json.loads(RULE_SCHEMA_PATH.read_text(encoding="utf-8"))
PROMO_SCHEMA = json.loads(PROMO_SCHEMA_PATH.read_text(encoding="utf-8"))
PROMO_REQ = PROMO_SCHEMA["$defs"]["PromotionRequest"]
PROMO_RES = PROMO_SCHEMA["$defs"]["PromotionResponse"]


def accepts(instance: dict, schema: dict) -> bool:
    try:
        jsonschema.validate(instance, schema)
        return True
    except jsonschema.ValidationError:
        return False


# ------------------------------------------------------------------ 规则契约
VALID_RULESET = {
    "version": "0.1-skeleton",
    "updated": "2026-09-15",
    "calibrated": False,
    "rules": [
        {
            "code": "range.speed_kmh",
            "suite": "越界",
            "target": "wim_axle_record",
            "field": "speed_kmh",
            "rule": "0 ≤ 值 ≤ 阈值",
            "threshold": 200.0,
            "calibrated": False,
            "remark": "待标定",
        }
    ],
}


def _ruleset(**changes) -> dict:
    out = copy.deepcopy(VALID_RULESET)
    out.update(changes)
    return out


def _rule(**changes) -> dict:
    out = copy.deepcopy(VALID_RULESET["rules"][0])
    out.update(changes)
    return {"version": "0.1", "calibrated": False, "rules": [out]}


# (用例名, 实例, 期望)
RULE_CASES: list[tuple[str, dict, bool]] = [
    ("合法规则集", VALID_RULESET, True),
    ("缺少 version", {k: v for k, v in VALID_RULESET.items() if k != "version"}, False),
    ("缺少 calibrated 总开关", {k: v for k, v in VALID_RULESET.items() if k != "calibrated"}, False),
    ("rules 不是数组", _ruleset(rules={"code": "x.y"}), False),
    ("规则缺少 code", _ruleset(
        rules=[{k: v for k, v in VALID_RULESET["rules"][0].items() if k != "code"}]), False),
    ("规则缺少 calibrated", _ruleset(
        rules=[{k: v for k, v in VALID_RULESET["rules"][0].items() if k != "calibrated"}]), False),
    ("code 格式错误（无分组前缀）", _rule(code="speedkmh"), False),
    ("code 格式错误（含大写）", _rule(code="Range.Speed"), False),
    ("suite 不在枚举内", _rule(suite="其它"), False),
    ("多余字段", _ruleset(extra=1), False),
    ("threshold 允许 null", _rule(threshold=None), True),
    ("updated 日期格式错误", _ruleset(updated="2026/09/15"), False),
    ("remark 可省略", _ruleset(
        rules=[{k: v for k, v in VALID_RULESET["rules"][0].items() if k != "remark"}]), True),
]

# ------------------------------------------------------------------ 晋升契约
VALID_PROMO_REQ = {"batch_no": "skeleton-2026091511", "operator": "张三", "reason": "抽查 200 条全部合规"}
VALID_PROMO_RES = {
    "batch_no": "skeleton-2026091511",
    "truth_flag": True,
    "promoted_at": "2026-09-15T18:00:00+08:00",
    "rules_passed": ["cross.gross_vs_axle_sum"],
    "rules_failed": [],
}


def _req(**changes) -> dict:
    out = copy.deepcopy(VALID_PROMO_REQ)
    out.update(changes)
    return out


def _res(**changes) -> dict:
    out = copy.deepcopy(VALID_PROMO_RES)
    out.update(changes)
    return out


PROMO_CASES: list[tuple[str, dict, dict, bool]] = [
    ("合法晋升请求", VALID_PROMO_REQ, PROMO_REQ, True),
    ("合法晋升响应", VALID_PROMO_RES, PROMO_RES, True),
    ("缺少 operator（审计要求）", {k: v for k, v in VALID_PROMO_REQ.items() if k != "operator"},
     PROMO_REQ, False),
    ("operator 为空串", _req(operator=""), PROMO_REQ, False),
    ("缺少 reason", {k: v for k, v in VALID_PROMO_REQ.items() if k != "reason"}, PROMO_REQ, False),
    ("reason 太短（'ok' 不算理由）", _req(reason="ok"), PROMO_REQ, False),
    ("override=true 但无 override_reason", _req(override=True), PROMO_REQ, False),
    # 强制放行是**合法**用法：带 override_reason 时 schema 必须接受（首轮我把期望写成 False，被测试抓出）
    ("override=true 且有 override_reason（强制放行）",
     _req(override=True, override_reason="导师批准"), PROMO_REQ, True),
    ("override=false 时不要求 override_reason", _req(override=False), PROMO_REQ, True),
    ("响应 truth_flag=false（晋升失败不得返回本结构）", _res(truth_flag=False), PROMO_RES, False),
    ("响应缺 rules_failed", {k: v for k, v in VALID_PROMO_RES.items() if k != "rules_failed"},
     PROMO_RES, False),
    ("请求多余字段", _req(extra=1), PROMO_REQ, False),
]


def main() -> int:
    fails: list[str] = []

    # ---------------------------------------------------------- 规则契约
    print("=" * 96)
    print("一、质量规则契约 quality_rule.v0.1")
    print("=" * 96)
    print(f"{'用例':<38}{'期望':<8}{'实测':<8}结论")
    print("-" * 96)
    for name, inst, want in RULE_CASES:
        got = accepts(inst, RULE_SCHEMA)
        ok = got == want
        print(f"{name:<36}{str(want):<8}{str(got):<8}{'✓' if ok else '✗ 不符预期'}")
        if not ok:
            fails.append(f"质量规则契约/{name}")

    # ---------------------------------------------------------- 晋升契约
    print()
    print("=" * 96)
    print("二、真值晋升契约 promotion.v0.1")
    print("=" * 96)
    print(f"{'用例':<46}{'期望':<8}{'实测':<8}结论")
    print("-" * 96)
    for name, inst, schema, want in PROMO_CASES:
        got = accepts(inst, schema)
        ok = got == want
        print(f"{name:<44}{str(want):<8}{str(got):<8}{'✓' if ok else '✗ 不符预期'}")
        if not ok:
            fails.append(f"晋升契约/{name}")

    # ---------------------------------------------------------- 配置 ↔ 契约
    print()
    print("=" * 96)
    print("三、真实配置文件必须符合契约（既有测试没有的一项）")
    print("=" * 96)
    if not RULES_YAML_PATH.exists():
        print(f"✗ 规则配置不存在：{RULES_YAML_PATH}")
        fails.append("配置缺失")
    else:
        raw = yaml.safe_load(RULES_YAML_PATH.read_text(encoding="utf-8"))
        ok = accepts(raw, RULE_SCHEMA)
        print(f"{'modules/M4-governance/config/quality_rules.yaml':<56}{'True':<8}{str(ok):<8}"
              f"{'✓' if ok else '✗ 与契约不符'}")
        if not ok:
            fails.append("配置不符合契约")
        else:
            rules = raw["rules"]
            code = [r["code"] for r in rules]
            dup = {c for c in code if code.count(c) > 1}
            print(f"\n  规则条数 {len(rules)}；用真实数据标定过的 {sum(1 for r in rules if r['calibrated'])} 条")
            print(f"  code 唯一性：{'✓ 无重复' if not dup else f'✗ 重复 {dup}'}")
            if dup:
                fails.append("规则 code 重复")
            # 未标定的规则必须显式标注——这是"未标定不得用于生产判定"的机械保障
            uncal = [r["code"] for r in rules if not r["calibrated"]]
            print(f"  未标定（不得用于生产判定）：{len(uncal)} 条")
            for c in uncal[:3]:
                print(f"    · {c}")
            if len(uncal) > 3:
                print(f"    · …另 {len(uncal) - 3} 条")
            # 每条未标定的规则都必须写清待办，否则标定工作会失传
            no_remark = [r["code"] for r in rules if not r["calibrated"] and not r.get("remark")]
            print(f"  未标定且有 remark 说明：{'✓ 全部有' if not no_remark else f'✗ 缺 {no_remark}'}")
            if no_remark:
                fails.append("未标定规则缺 remark")

    # ---------------------------------------------------------- 反向确认
    print()
    print("=" * 96)
    print("四、反向确认：这些检查确实会失败（防止'永远不会失败的检查'）")
    print("=" * 96)
    probes = [
        ("把一个必填字段删掉，schema 应拒绝",
         {k: v for k, v in VALID_PROMO_REQ.items() if k != "batch_no"}, PROMO_REQ, False),
        ("把 code 改成不合法格式，schema 应拒绝", _rule(code="BAD CODE"), RULE_SCHEMA, False),
        ("把 truth_flag 改成 false，schema 应拒绝", _res(truth_flag=False), PROMO_RES, False),
    ]
    for desc, inst, schema, want in probes:
        got = accepts(inst, schema)
        ok = got == want
        print(f"  {desc:<52}{str(got):<8}{'✓' if ok else '✗'}")
        if not ok:
            fails.append(f"反向确认/{desc}")

    print()
    print("=" * 96)
    print("结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
