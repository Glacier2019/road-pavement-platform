"""设计数据导入 —— 适配器基类、能力声明、几何完整度等级判定。

设计要点（契约⑤）
-------------------------------------------------------------------------------
① **格式无关的 IR**：纬地 / 鸿业 / 图纸人工录入 / 由监测数据反推，四种来源的适配器
   产出**同一个结构**（road_geometry_ir.v0.2.schema.json）。于是：
     新增一家厂商 = 新增一个适配器；校验规则与落库路径**永不重写**。

② **能力声明 ≠ 解析结果**。适配器先说"我这个来源**有能力**给哪些段"（`capabilities`），
   再说"这次**实际**解出了哪些段"（`segments`）。两者之差 = `gaps`。
   没有这层区分，用户看到的"缺纵断面"就无法分辨是
   「源里没有」还是「有但没解出来」——而这两件事的处理方式完全相反。

③ **几何完整度等级 L0–L4 由实际产出推导**，不允许外部传入。
   让调用方自己填等级，就会出现"填了 L3、实际只有桩号"的假数据。
"""
from __future__ import annotations

import pathlib

from typing import Any, Iterable

from .errors import SourceInvalid

# 几何段名 —— 一律用 GE 域物理表名（与契约⑤ schema 的 enum 严格一致）
SEGMENTS: tuple[str, ...] = (
    "station_sequence",
    "station_equation",
    "alignment_pi",
    "alignment_element",
    "profile_grade_point",
    "profile_ground_point",
    "geometry_point",
    "cross_section",
)

# 各等级所需的"充分条件"（按从高到低判定，命中即返回）
#   L4 平纵横：需横断面（cross_section 属 v0.4 第二批，当前数据源拿不到，故本工程最高到 L3）
#   L3 平纵  ：需纵断面（变坡点或地面线任一）
#   L2 平面  ：需平面线形（交点或线形单元任一）
#   L1 骨架  ：需桩号序列
# 每条规则是 (等级, 该级要求的段, 组合方式)。**组合方式放进数据里**，
# 而不是在判定函数里写死成 any —— 因为两条规则的正确组合方式**本来就不一样**
# （见下方 derive_level 的说明），写死一个，另一条就会长期错着且测不出来。
# 放进数据的额外好处：M3 侧那份副本用 == 就能整条对齐，连组合方式一起钉住。
_LEVEL_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("L4", ("cross_section",), "any"),
    ("L3", ("profile_grade_point", "profile_ground_point"), "all"),
    ("L2", ("alignment_pi", "alignment_element"), "any"),
    ("L1", ("station_sequence",), "any"),
)

# 为什么把「平纵」独立为 L3、而不是我最初提的「L3 = 平纵横全量」
# ---------------------------------------------------------------------------
# 因为横断面表（cross_section 系）是 GE 完整化的**第二批**，要等 DDL v0.4。
# 若 L3 定义成"平纵横全量"，那本工程在 v0.4 之前**永远达不到 L3**，
# 等级就退化成"一直显示 L2"的噪声——一个永远不会变的状态码，比没有更糟。


def read_text_any(path: Any) -> tuple[str, str]:
    """读文本文件，返回 ``(文本, 实际用的编码)``。**utf-8 优先，退到 gbk。**

    为什么必须退这一步：纬地的 **`.PRJ` 是 GBK**（实测首行 `HINTCAD6.00_PRJ_SHUJU`，
    第 24 字节 `0xCF` 就不是合法 UTF-8），而它恰恰是**档案五表**（项目名、等级、
    设计速度、路面结构…）唯一的来源。用 ``utf-8 errors="replace"`` 硬读不会报错，
    只会解出一堆 `\ufffd`，然后解析器报"没有解析到任何 [项目设置] 字段"——
    错误信息指向解析器，真凶却是编码。这个坑实测踩过一次。

    依次尝试 utf-8 → gbk：`.STA/.JD/.pm/.DMX/.ZDM` 都是纯 ASCII，
    gbk 对 ASCII 与 utf-8 完全一致，故这个顺序不会有歧义。
    两种都失败才抛，且把两个失败原因都带上。
    """
    raw = pathlib.Path(path).read_bytes()
    tries: list[str] = []
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError as exc:
            tries.append(f"{enc}: {exc}")
    raise SourceInvalid(
        "既不是 UTF-8 也不是 GBK（疑似二进制文件）：" + "；".join(tries),
        file=pathlib.Path(path).name)


def level_hit(required: tuple[str, ...], present: set[str], mode: str) -> bool:
    """单个等级的判定：``required`` 里的段算不算"齐"。

    纯函数，只吃一个"哪些段有数据"的**集合** —— 因为两个调用方的"有没有数据"
    判法不同（M2 看解析结果是否非空列表，M3 看行数是否 > 0），但**判定逻辑必须一致**。
    归一化成集合之后，两边的这个函数可以逐个用例对拍（见 test_dao_contract.py）。
    """
    got = [s for s in required if s in present]
    return bool(got) if mode == "any" else len(got) == len(required)


def derive_level(segments: dict[str, Any]) -> str:
    """由**实际解析出的段**推导几何完整度等级。

    >>> derive_level({})
    'L0'
    >>> derive_level({"station_sequence": [1, 2]})
    'L1'
    >>> derive_level({"station_sequence": [1, 2], "alignment_pi": [1]})
    'L2'
    >>> derive_level({"station_sequence": [1, 2], "alignment_pi": [1],
    ...               "alignment_element": [1], "profile_ground_point": [1]})
    'L2'

    组合方式**每条规则不同**，改动前先读完这段：
      · L2（alignment_pi / alignment_element）用 any 是**刻意的**：拿到交点链 + R + A(Ls)
        在数学上已足以定出整条平面线形，线形单元只是同一定线的另一种表述。
      · L4（cross_section）单元素，any/all 无差别。
      · L3（profile_grade_point / profile_ground_point）**用 all**：
        L3 的含义是"有纵断面**设计**"，而地面线是测量结果、不是设计成果 ——
        "只有地面线、没有设计线"依然回答不了"这个桩号该铺多厚"。
        这条曾经写成 any，代码里留了"落地时必须回来改"的话；
        2026-09：`.DMX` 解析器落地后**实测复现了虚高**（只加地面线，等级即从 L2 跳到 L3），
        于是改成 all。那条预言写在解析器存在之前，正是为了这一刻。
    """
    present = {seg for seg, v in segments.items() if v}
    for level, required, mode in _LEVEL_RULES:
        if level_hit(required, present, mode):
            return level
    return "L0"


def build_gaps(capabilities: Iterable[str],
               segments: dict[str, Any],
               reasons: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """算出「声明有能力、但这次没给出」的段，并附原因。

    `reasons` 由适配器提供（source_absent / parse_blocked / manual_required / not_supported）；
    未提供时保守取 `not_supported`——**不默认成 source_absent**：
    "源里没有"是一个需要证据的断言，不能由缺省值冒充。
    """
    reasons = reasons or {}
    out: list[dict[str, Any]] = []
    for seg in capabilities:
        if seg in segments and segments[seg]:
            continue
        out.append({
            "segment": seg,
            "reason": reasons.get(seg, "not_supported"),
            "detail": None,
        })
    return out


def make_ir(*,
            vendor: str,
            origin: str,
            files: list[dict[str, Any]],
            capabilities: Iterable[str],
            segments: dict[str, Any],
            project_name: str | None = None,
            vendor_version: str | None = None,
            gap_reasons: dict[str, str] | None = None,
            warnings: list[str] | None = None) -> dict[str, Any]:
    """组装一份符合契约⑤ 的 IR。

    等级**在这里推导**，不从参数接收——单一入口，杜绝"手填等级"。

    `warnings` 是**「可疑但合法」**这一类，与 `gaps`（"没有"）严格区分：
    有缺口是差数据，有告警是**可能要出错的**数据。导入照做，但预检报告必须把它顶到人眼前。
    （例：.pm 的转向符号与 .JD 由坐标独立算出的转角符号相反——两份文件对不上。）
    无告警时不输出该键，保持 IR 紧凑。
    """
    caps = tuple(dict.fromkeys(capabilities))          # 去重且保序
    clean_segments = {k: v for k, v in segments.items() if v}
    ir = {
        "ir_version": "0.2",
        "source": {
            "vendor": vendor,
            "vendor_version": vendor_version,
            "project_name": project_name,
            "origin": origin,
            "files": files,
        },
        "geometry_level": derive_level(clean_segments),
        "capabilities": sorted(caps),
        "segments": clean_segments,
        "gaps": build_gaps(caps, clean_segments, gap_reasons),
    }
    if warnings:
        ir["warnings"] = list(warnings)
    return ir


__all__ = [
    "SEGMENTS",
    "derive_level",
    "build_gaps",
    "make_ir",
]
