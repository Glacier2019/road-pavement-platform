"""契约违约分类 —— 把 Pydantic 校验错误映射为**稳定的规则码**。

为什么需要这个
--------------
原先接入服务把**所有**违约都记成 `issue_code='contract_violation'`。实测后果：
库里 26 条违约全是同一个码，其中 9 条其实是"轴数不符"、9 条是"总重偏差"、
8 条是"超速越界"——**在日志里完全分不出来**。拿日志排障的人只能看到
"有 26 条契约违约"，不知道该去调哪条校验。

规则码的命名与 `services/governance/config/quality_rules.yaml`（M4 的质量规则集）
使用同一套命名空间：`<层>.<对象>`，层 ∈ {schema, missing, range, enum, cross}。
两条跨字段规则 `cross.axle_num_vs_axles` / `cross.gross_vs_axle_sum` 在
M2（接入时点）与 M4（批次时点）**必须同名**——同一条逻辑规则在两个层被求值，
名字不一致会让两边的统计对不上。契约测试 `test_violation_codes.py` 会盯着这一点。

纪律：本文件只做**分类**，不做判定。判定在 models.py（契约的代码侧实现），
改判定=改契约，要走工单。
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

# Pydantic 错误类型 → 规则码前缀
_RANGE_FIELDS = {
    "speed_kmh": "range.speed_kmh",
    "axle_num": "range.axle_num",
    "lane_no": "range.lane_no",
}
_ENUM_FIELDS = {
    "direction": "enum.direction",
    "axle_type_code": "enum.axle_type_code",
}
# models.py 的 _cross_check 里那几句 ValueError 的指纹 → 规则码
# 指纹取消息里最稳定的片段（数字会变，文字不会）
_CROSS_FINGERPRINTS: list[tuple[str, str]] = [
    ("与 axle_num(", "cross.axle_num_vs_axles"),
    ("与 axle_num(", "cross.axle_num_vs_axles"),
    ("axle_seq 必须从 1 连续编号", "cross.axle_seq_continuity"),
    ("偏差 >2%", "cross.gross_vs_axle_sum"),
]
_VERSION_FINGERPRINT = ("schema_version 必须为", "schema.schema_version")


def classify(exc: ValidationError) -> tuple[str, str]:
    """把校验错误分类为 (规则码, 人类可读说明)。

    规则码保证 ≤32 字符（`data_quality_log.issue_code` 是 varchar(32)）。
    无法识别时回落到 `contract_violation`——**保留这个兜底码**，
    这样"新增了校验但忘了分类"会表现为兜底码增多，可被监控发现，
    而不是静默变成一个看似正常的细码。
    """
    errs: list[dict[str, Any]] = exc.errors()
    if not errs:
        return "contract_violation", "校验失败（无错误详情）"

    e = errs[0]
    etype: str = e.get("type", "")
    loc: tuple[Any, ...] = tuple(e.get("loc", ()))
    msg: str = e.get("msg", "")
    field = str(loc[-1]) if loc else ""

    # ------------------------------------------------ 跨字段（自定义校验器）
    for fp, code in _CROSS_FINGERPRINTS:
        if fp in msg:
            return code, msg
    if _VERSION_FINGERPRINT[0] in msg:
        return _VERSION_FINGERPRINT[1], msg

    # ------------------------------------------------ 结构类
    if etype == "missing":
        return f"missing.{field}"[:32], f"缺少必填字段 {'.'.join(map(str, loc))}"
    if etype == "extra_forbidden":
        return "schema.unexpected_field", f"出现了 schema 未定义的字段 {'.'.join(map(str, loc))}"

    # ------------------------------------------------ 越界
    if etype in ("less_than", "less_than_equal", "greater_than", "greater_than_equal"):
        # 轴明细里的 weight_kg 走 range.axle_weight_kg，其余按字段名映射
        if field == "weight_kg" and "axles" in loc:
            return "range.axle_weight_kg", msg
        return _RANGE_FIELDS.get(field, f"range.{field}"[:32]), msg

    # ------------------------------------------------ 枚举
    if etype == "literal_error":
        return _ENUM_FIELDS.get(field, f"enum.{field}"[:32]), msg

    # ------------------------------------------------ 类型错误
    # 注意：`device_code: None` 这类是**类型错误**（string_type），不是字段缺失
    # （missing）。两者曾经在我自己的测试里被混为一谈，分类器因此漏了一整类形态。
    if etype.endswith("_type") or etype in ("int_parsing", "float_parsing", "bool_parsing",
                                            "datetime_parsing", "datetime_from_date_parsing"):
        return f"type.{field}"[:32], msg

    # ------------------------------------------------ 兜底（刻意保留，可被监控）
    return "contract_violation", "；".join(
        f"{'.'.join(map(str, x.get('loc', ())))}: {x.get('msg', '')}" for x in errs[:2]
    )[:500]


def describe(exc: ValidationError, limit: int = 2) -> str:
    """人类可读的违约描述（进 data_quality_log.issue_desc）。"""
    return "；".join(
        f"{'.'.join(map(str, x.get('loc', ())))}: {x.get('msg', '')}" for x in exc.errors()[:limit]
    )[:500]
