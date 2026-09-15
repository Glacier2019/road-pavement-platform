"""模块契约测试（M4–M10 的产出契约）—— 接入五件套第 5 件

一个文件覆盖 6 份模块契约。**离线可跑**：不依赖 Docker、不依赖数据库、不依赖网络。

为什么合并成一个文件而不是每模块一份：
这 6 份契约的测试**结构完全一样**（表驱动 + 应通过/应拒绝两侧 + 反向确认），
拆成 6 份只会让"新增一个模块要记得新建一个测试文件"成为新的坑。
新增模块只需在此文件的 CASES 里加一组用例。

跑法：
    uv run --with jsonschema --with pyyaml tests/contract/test_module_contracts.py
"""

from __future__ import annotations

import json
import pathlib
import sys

import jsonschema
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "contracts"


def load_schema(rel: str) -> dict:
    return json.loads((CONTRACTS / rel).read_text(encoding="utf-8"))


def accepts(instance: dict, schema: dict) -> bool:
    try:
        jsonschema.validate(instance, schema)
        return True
    except jsonschema.ValidationError:
        return False


# ==================================================================== M5 诊断
D_SCH = load_schema("fusion/diagnosis.v0.1.schema.json")
D_OK = {
    "diagnosis_id": "dg-20260915-0001",
    "object_type": "路段",
    "object_ref": "K12+345~K12+845",
    "finding": "上行第二车道平整度劣化，RQI 降至 2 级",
    "evidence": [
        {"source_table": "wim_axle_record", "metric": "overload_rate", "value": 18.4, "unit": "%"}
    ],
    "confidence": None,
    "batch_no": "skeleton-2026091511",
    "produced_at": "2026-09-15T18:00:00+08:00",
}


def _d(**ch):
    o = dict(D_OK); o.update(ch); return o


# ==================================================================== M7 映射集
M_SCH = load_schema("semantic/mapping_set.v0.1.schema.json")
M_OK = {
    "mapping_id": "mp-0001",
    "source_kind": "桩号",
    "source_sample": "2025年检测报告.xlsx / Sheet1",
    "entries": [{"raw": "K12+345", "normalized": "12.345", "target_field": "road_section.start_station"}],
    "status": "候选",
    "confirmed_by": None,
    "created_at": "2026-09-15T18:00:00+08:00",
}


def _m(**ch):
    o = dict(M_OK); o.update(ch); return o


# ==================================================================== M8 养护计划
P_SCH = load_schema("apps/maintenance_plan.v0.1.schema.json")
P_OK = {
    "plan_id": "pl-0001",
    "items": [{"object_ref": "K12+345", "action": "修补", "priority": 1,
               "basis": "dg-20260915-0001", "est_cost_cny": 12000.0}],
    "generated_at": "2026-09-15T18:00:00+08:00",
    "requires_human_confirm": True,
}


def _p(**ch):
    o = dict(P_OK); o.update(ch); return o


# ==================================================================== M9 模块登记
R_SCH = load_schema("console/module_registry.v0.1.schema.json")
R_OK = {
    "version": "0.1",
    "modules": [{"module": "M4", "name": "数据治理 · 真数据门", "owner": "A组",
                 "phase": "P1", "status": "骨架", "healthz": "http://governance:8000/healthz"}],
}


def _r(**ch):
    o = dict(R_OK); o.update(ch); return o


# ==================================================================== M10 动作工单
T_SCH = load_schema("agent/action_ticket.v0.1.schema.json")
T_OK = {
    "ticket_id": "tk-0001", "action": "对 K12+345 段铣刨罩面", "target": "K12+345~K12+845",
    "requested_by": "张三", "status": "待确认", "via": "M6",
    "created_at": "2026-09-15T18:00:00+08:00",
}


def _t(**ch):
    o = dict(T_OK); o.update(ch); return o


# (契约, 用例名, 实例, 期望)
CASES: list[tuple[str, str, dict, bool]] = [
    # ---------------------------------------------------------- M5
    ("M5 诊断", "合法诊断（带证据链）", D_OK, True),
    ("M5 诊断", "无证据链（不得入库）", _d(evidence=[]), False),
    ("M5 诊断", "object_type 不在枚举", _d(object_type="随便"), False),
    ("M5 诊断", "finding 太短", _d(finding="坏"), False),
    ("M5 诊断", "confidence 超出 [0,1]", _d(confidence=1.5), False),
    ("M5 诊断", "confidence 允许 null（未标定模型必须留空）", _d(confidence=None), True),
    ("M5 诊断", "缺 batch_no（真值门禁溯源用）", {k: v for k, v in D_OK.items() if k != "batch_no"}, False),
    ("M5 诊断", "多余字段", _d(extra=1), False),

    # ---------------------------------------------------------- M7
    ("M7 映射", "合法候选映射（未确认）", M_OK, True),
    ("M7 映射", "已确认但无 confirmed_by（人工确认是硬要求）", _m(status="已确认"), False),
    ("M7 映射", "已确认且有 confirmed_by", _m(status="已确认", confirmed_by="李四"), True),
    ("M7 映射", "source_kind 不在枚举", _m(source_kind="别的"), False),
    ("M7 映射", "entries 为空", _m(entries=[]), False),
    ("M7 映射", "条目缺 target_field", _m(entries=[{"raw": "K12+345", "normalized": "12.345"}]), False),

    # ---------------------------------------------------------- M8
    ("M8 养护", "合法计划", P_OK, True),
    ("M8 养护", "requires_human_confirm=false（硬线，不允许）", _p(requires_human_confirm=False), False),
    ("M8 养护", "action 不在枚举", _p(items=[{"object_ref": "K1", "action": "随便修", "priority": 1, "basis": "x"}]), False),
    ("M8 养护", "priority 越界（6）", _p(items=[{"object_ref": "K1", "action": "修补", "priority": 6, "basis": "x"}]), False),
    ("M8 养护", "basis 为空（不许不给依据）", _p(items=[{"object_ref": "K1", "action": "修补", "priority": 1, "basis": ""}]), False),
    ("M8 养护", "items 为空", _p(items=[]), False),

    # ---------------------------------------------------------- M9
    ("M9 登记", "合法登记", R_OK, True),
    ("M9 登记", "module 编号格式错误", _r(modules=[{**R_OK["modules"][0], "module": "M04"}]),
     False),
    ("M9 登记", "owner 留空（空白会掩盖问题）", _r(modules=[{**R_OK["modules"][0], "owner": ""}]), False),
    ("M9 登记", "status 不在枚举", _r(modules=[{**R_OK["modules"][0], "status": "差不多了"}]), False),
    ("M9 登记", "phase 不在枚举", _r(modules=[{**R_OK["modules"][0], "phase": "P9"}]), False),

    # ---------------------------------------------------------- M10
    ("M10 工单", "合法工单", T_OK, True),
    ("M10 工单", "via 不是 M6（不得直连库）", _t(via="PG"), False),
    ("M10 工单", "status 不在枚举", _t(status="随便"), False),
    ("M10 工单", "缺 requested_by", {k: v for k, v in T_OK.items() if k != "requested_by"}, False),
]


def main() -> int:
    fails: list[str] = []
    schemas = {"M5 诊断": D_SCH, "M7 映射": M_SCH, "M8 养护": P_SCH,
               "M9 登记": R_SCH, "M10 工单": T_SCH}

    print("=" * 100)
    print("一、模块契约 —— 应通过 / 应拒绝 两侧")
    print("=" * 100)
    print(f"{'契约':<10}{'用例':<46}{'期望':<8}{'实测':<8}结论")
    print("-" * 100)
    for contract, name, inst, want in CASES:
        got = accepts(inst, schemas[contract])
        ok = got == want
        print(f"{contract:<10}{name:<44}{str(want):<8}{str(got):<8}{'✓' if ok else '✗ 不符预期'}")
        if not ok:
            fails.append(f"{contract}/{name}")

    # ------------------------------------------------------ 配置 ↔ 契约
    print()
    print("=" * 100)
    print("二、真实配置文件必须符合契约（配置与契约脱钩是这类项目最常见的腐烂方式）")
    print("=" * 100)
    pairs = [
        ("M9 登记", "modules/M9-console/config/modules.yaml", R_SCH),
        ("M4 规则", "modules/M4-governance/config/quality_rules.yaml",
         load_schema("governance/quality_rule.v0.1.schema.json")),
    ]
    for label, rel, sch in pairs:
        p = ROOT / rel
        if not p.exists():
            print(f"  ✗ {rel} 不存在")
            fails.append(f"配置缺失/{rel}")
            continue
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        ok = accepts(raw, sch)
        print(f"  {label:<8}{rel:<52}{'✓ 符合契约' if ok else '✗ 与契约不符'}")
        if not ok:
            fails.append(f"配置不符合契约/{rel}")

    # 重点：登记表里的 owner=待指派 必须是真的可见问题，不能悄悄溜过去
    reg = yaml.safe_load((ROOT / "modules/M9-console/config/modules.yaml").read_text(encoding="utf-8"))
    unassigned = [m["module"] for m in reg["modules"] if m["owner"] == "待指派"]
    print(f"\n  登记表中未指派责任人的模块：{unassigned or '无'}")
    if unassigned:
        print(f"    ⚠️  {unassigned} —— 这与图 C 的待决注记一致，P1 结束前需导师指派")

    # ------------------------------------------------------ 反向确认
    print()
    print("=" * 100)
    print("三、反向确认：这些检查确实会失败（防止『永远不会失败的检查』）")
    print("=" * 100)
    probes = [
        ("把 M5 的证据链清空，schema 应拒绝", _d(evidence=[]), D_SCH),
        ("把 M8 的人工确认关掉，schema 应拒绝", _p(requires_human_confirm=False), P_SCH),
        ("把 M7 的确认人去掉，schema 应拒绝", _m(status="已确认", confirmed_by=None), M_SCH),
        ("把 M10 的通道改成直连库，schema 应拒绝", _t(via="PG"), T_SCH),
        ("把 owner 留空，schema 应拒绝", _r(modules=[{**R_OK["modules"][0], "owner": ""}]), R_SCH),
    ]
    for desc, inst, sch in probes:
        got = accepts(inst, sch)
        print(f"  {desc:<50}{str(got):<8}{'✓ 正确拒绝' if not got else '✗ 竟然通过了'}")
        if got:
            fails.append(f"反向确认/{desc}")

    print()
    print("=" * 100)
    print("结果：" + ("全部通过 ✓" if not fails else f"失败 {len(fails)} 项 → {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
