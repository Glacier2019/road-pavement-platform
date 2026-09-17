"""纬地 HintCAD 适配器 —— 把一套纬地工程文件解析成契约⑤ 的 IR。

能力 vs 已实现（**这个区分必须留在代码里，不能只留在文档里**）
-------------------------------------------------------------------------------
一套完整的纬地工程（.PRJ〔文件名〕段列了 18 类文件）**有能力**提供 7 个几何段；
本适配器**已实现 5 段**：`.STA` → `station_sequence`、`.JD` → `alignment_pi`、
`.pm` → `alignment_element`（平面线形单元）、`.DMX` → `profile_ground_point`（纵断面地面线）、
`.ZDM` → `profile_grade_point`（纵断面设计线）。

**平纵都齐了，故本工程的几何等级到 L3** —— L3 要求设计线与地面线**同时**具备
（见 base.py 里那条 any→all 的说明：只给地面线不算"有纵断面设计"）。

因此 IR 里 `capabilities` 列 7 项、`segments` 有 5 项，`gaps` 如实登记 2 项，
其中多为 `not_supported`（"适配器还没写"），可能是 `source_absent`（"源里没这个文件"）。
**这两种缺口对用户的含义完全不同**：前者等代码、后者要去找文件。
混成一个"缺纵断面"，用户无从下手。
"""
from __future__ import annotations

import pathlib
from typing import Any

from ..base import make_ir
from ..errors import ParseBlocked, SourceInvalid
from . import dmx, jd, pm, sta, zdm

VENDOR = "weidi-hintcad"

# 一套完整纬地工程**有能力**提供的几何段
CAPABILITIES: tuple[str, ...] = (
    "station_sequence",
    "alignment_pi",
    "alignment_element",
    "profile_grade_point",
    "profile_ground_point",
    "geometry_point",
    "cross_section",
)

# 本适配器**已实现**的段。新增解析器时改这里，测试会逼 IR 与之同步。
IMPLEMENTED: tuple[str, ...] = ("station_sequence", "alignment_pi", "alignment_element",
                                "profile_ground_point", "profile_grade_point")

# 段 → 解析器模块。新增一个段只需：① 写个模块（detect/parse/PAYLOAD_KEY/SEGMENT）
# ② 在这里登记 ③ 加进 IMPLEMENTED。IR 结构、缺口推导、等级判定都不用动。
#
# 例外：若新段与已有段之间存在**跨文件对账**（如 .DMX 逐桩对齐 .STA），
# 那部分不在解析器里，而在 build_ir 末尾（同 _link_elements_to_pi 的做法）。
_PARSERS: dict[str, Any] = {
    "station_sequence": sta,
    "alignment_pi": jd,
    "alignment_element": pm,
    "profile_ground_point": dmx,
    "profile_grade_point": zdm,
}


def _link_elements_to_pi(elements: list[dict[str, Any]],
                         control_points: list[dict[str, Any]]) -> list[str]:
    """把线形单元挂到所属交点，并**用 `.JD` 独立算出的转角符号去卡 `.pm` 的转向符号**。

    归属不靠 `.pm` 里那个 ±1 列（它的含义本身要先被证明），而是靠**桩号包容**：
    连续的一段「缓/圆」单元构成一个曲线组，该组桩号区间必然包住它那个交点的桩号。
    交叉验证才是目的：`.pm` 的第 0 列是转向符号，`.JD` 的转角是**由坐标独立算出来的**，
    两者符号必须一致——不一致说明两份文件对不上，或某一侧解析错位了。
    返回告警列表（不抛异常：这是"可疑"不是"非法"，交给预检报告展示）。
    """
    warn: list[str] = []
    pis = [p for p in control_points if p["tag"] not in ("QD", "ZD")]
    if not pis:
        return ["有 alignment_element 却没有可用交点，无法挂接"]

    # 曲线组 = 连续「缓/圆」单元的最大段
    groups: list[list[dict[str, Any]]] = []
    for e in elements:
        if e["type"] in ("transition", "circular"):
            if groups and groups[-1][-1]["seq"] == e["seq"] - 1:
                groups[-1].append(e)
            else:
                groups.append([e])

    for g in groups:
        lo, hi = g[0]["start_station_m"], g[-1]["end_station_m"]
        inside = [p for p in pis if lo <= p["station_m"] <= hi]
        if len(inside) != 1:
            warn.append(f"曲线组 {g[0]['seq']}–{g[-1]['seq']}（{lo:.3f}–{hi:.3f} m）"
                        f"包容了 {len(inside)} 个交点，应为 1 个")
            continue
        pi = inside[0]
        for e in g:
            e["pi_seq"] = pi["seq"]
            # 转向符号必须与 .JD 由坐标算出的转角同号
            d = pi.get("deflection_deg")
            if d is not None and (e["turn_flag"] > 0) != (d > 0):
                warn.append(f"单元 {e['seq']} 的转向符号 {e['turn_flag']:+d} 与交点 {pi['tag']} "
                            f"的转角 {d:+.4f}° 符号相反")
        # 紧邻其前、且止于该曲线组起点的直线单元，算作"引道"，一并挂到同一交点
        lead = next((e for e in elements
                     if e["type"] == "line" and abs(e["end_station_m"] - lo) < 1e-9), None)
        if lead is not None:
            lead["pi_seq"] = pi["seq"]

    # 落到线形之外的直线单元（如末端直线）：不强行归属
    for e in elements:
        e.setdefault("pi_seq", None)
    return warn

# 段 → 纬地文件（后缀, 类别名）。来自 .PRJ〔文件名〕段的实测映射。
SEGMENT_FILES: dict[str, tuple[str, str]] = {
    "station_sequence": (".STA", "桩号序列文件"),
    "alignment_pi": (".JD", "平面交点文件"),
    "alignment_element": (".pm", "平面线形文件"),
    "profile_grade_point": (".ZDM", "纵断面设计文件"),
    "profile_ground_point": (".DMX", "纵断面地面线文件"),
    "geometry_point": (".tf", "土方数据文件（逐桩坐标）"),
    "cross_section": (".HDM", "横断面地面线文件"),
}


def build_ir(project_dir: str | pathlib.Path, *,
             project_name: str | None = None) -> dict[str, Any]:
    """扫一个纬地工程目录 → 契约⑤ IR。

    目录里**没有**某个已实现的文件 → 该段记 `source_absent`；
    段还没实现 → 记 `not_supported`。
    """
    d = pathlib.Path(project_dir)
    if not d.is_dir():
        raise SourceInvalid(f"不是目录：{d}")

    files: list[dict[str, Any]] = []
    segments: dict[str, Any] = {}
    reasons: dict[str, str] = {}
    version: str | None = None

    for seg in CAPABILITIES:
        suffix, kind = SEGMENT_FILES[seg]
        hits = sorted(p for p in d.iterdir()
                      if p.is_file() and p.suffix.lower() == suffix.lower())
        if not hits:
            files.append({"name": "", "kind": kind, "parse_status": "absent",
                          "note": f"目录内未找到 {suffix} 文件"})
            reasons[seg] = "source_absent" if seg in IMPLEMENTED else "not_supported"
            continue

        path = hits[0]
        if seg not in IMPLEMENTED:
            files.append({"name": path.name, "kind": kind, "parse_status": "pending",
                          "note": "适配器尚未实现该段的解析"})
            reasons[seg] = "not_supported"
            continue

        try:
            # utf-8 优先、退 gbk —— 见 base.read_text_any 的说明（.PRJ 是 GBK）
            text, _used_enc = base.read_text_any(path)
            parser = _PARSERS[seg]
            if not parser.detect(text):
                raise SourceInvalid(f"魔数不匹配，可能不是纬地 {suffix} 文件", file=path.name)
            out = parser.parse(text, file=path.name)
            version = version or out["vendor_version"]
            segments[seg] = out[parser.PAYLOAD_KEY]
            files.append({"name": path.name, "kind": kind, "parse_status": "ok", "note": None})
        except SourceInvalid as exc:
            # 解码失败（既不是 UTF-8 也不是 GBK）或魔数不符都在这里。
            # 原来的写法只捕 UnicodeDecodeError，还把原因写成"非 UTF-8/ASCII 文本，
            # 疑似二进制" —— 对 GBK 文件那是**误判**（GBK 是正经文本编码）。
            files.append({"name": path.name, "kind": kind, "parse_status": "blocked",
                          "note": str(exc)[:120]})
            reasons[seg] = "parse_blocked"
        except ParseBlocked as exc:
            files.append({"name": path.name, "kind": kind, "parse_status": "blocked",
                          "note": str(exc)})
            reasons[seg] = "parse_blocked"

    # 只有两段都拿到才能做的跨文件动作。单看一份文件做不了这些事。
    warns: list[str] = []

    # ① 把线形单元挂到交点，并拿 .JD 由坐标独立算出的转角符号去核对 .pm 的转向符号。
    if segments.get("alignment_element") and segments.get("alignment_pi"):
        warns = _link_elements_to_pi(segments["alignment_element"], segments["alignment_pi"])

    # ② 纵断面地面线逐桩对账 .STA。`.DMX` 自己**没有计数行**，行数不可自证；
    #    而它入库时锚的是 station_id —— 对不上就会错位，且错位不报错。
    if segments.get("profile_ground_point") and segments.get("station_sequence"):
        warns += dmx.check_against_stations(segments["profile_ground_point"],
                                            segments["station_sequence"])

    # ③ 设计线的纵坡与竖曲线长是**派生量**（源文件只给桩号/高程/半径）。
    #    在这里一次补出，而不是留给每个下游各自算一遍、各算出一个值。
    if segments.get("profile_grade_point"):
        segments["profile_grade_point"], gw = zdm.derive_grades(
            segments["profile_grade_point"])
        warns += gw

    # ④ 设计线必须覆盖整条路：首尾桩号与桩号序列一致。只查首尾 —— .ZDM 只有十几个
    #    变坡点，本来就不该逐桩对齐；但起终点不一致就是真问题（短了=没设计到头，
    #    长了=越出路线范围）。
    if segments.get("profile_grade_point") and segments.get("station_sequence"):
        warns += zdm.check_against_stations(segments["profile_grade_point"],
                                            segments["station_sequence"])

    return make_ir(
        vendor=VENDOR,
        origin="file",
        files=files,
        capabilities=CAPABILITIES,
        segments=segments,
        project_name=project_name,
        vendor_version=version,
        gap_reasons=reasons,
        warnings=warns,
    )


__all__ = ["VENDOR", "CAPABILITIES", "IMPLEMENTED", "SEGMENT_FILES", "build_ir",
           "sta", "jd", "pm", "prj", "dmx", "zdm"]
