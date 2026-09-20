"""纬地 HintCAD 适配器 —— 把一套纬地工程文件解析成契约⑤ 的 IR。

能力 vs 已实现（**这个区分必须留在代码里，不能只留在文档里**）
-------------------------------------------------------------------------------
一套完整的纬地工程（.PRJ〔文件名〕段列了 18 类文件）**有能力**提供 10 个几何段；
本适配器**已实现 8 段**：`.STA` → `station_sequence`、`.JD` → `alignment_pi`、
`.pm` → `alignment_element`（平面线形单元）、`.DMX` → `profile_ground_point`（纵断面地面线）、
`.ZDM` → `profile_grade_point`（纵断面设计线）、
`.SUP` → `superelev_transition`（超高过渡变化点，E(s) 的真源段）、
`.WID` → `roadbed_width`（路幅宽度分段）、
`.CTR` → `design_control`（**设计参数控制**，v0.4 新增：边坡/边沟/路槽/构造物等 9 张表）。

`.CTR` 与其余七段的**根本区别**：它不是"固定列数的一批数据"，而是**关键字驱动**的 ——
36 个关键字各有一套列义（教程 §13.10 定义 18 类格式）。故它一个段带 **9 张表**的载荷，
而不是一张表。解析器只解析其中**已建表的 9 组**，其余（17 个空关键字 + 教程无定义的
`ZDMDG`）**登记不解析** —— 不假装懂。

**平纵都齐了，故本工程的几何等级到 L3** —— L3 要求设计线与地面线**同时**具备
（见 base.py 里那条 any→all 的说明：只给地面线不算"有纵断面设计"）。
⚠ 超高**不参与**等级判定：L0–L4 是平/纵/横的完整度，横坡是平纵都具备之后的
**设计细节**，不是一级几何。所以多实现了 `.SUP`，等级仍然是 L3。

因此 IR 里 `capabilities` 列 9 项、`segments` 有 7 项，`gaps` 如实登记 2 项，
其中多为 `not_supported`（"适配器还没写"），可能是 `source_absent`（"源里没这个文件"）。
**这两种缺口对用户的含义完全不同**：前者等代码、后者要去找文件。
混成一个"缺纵断面"，用户无从下手。
"""
from __future__ import annotations

import pathlib
from typing import Any

from ..base import make_ir
from ..errors import ParseBlocked, SourceInvalid
from .. import base
from . import ctr, dmx, hdm, jd, lj, pm, sta, sup, tf, wid, zdm

VENDOR = "weidi-hintcad"

# 一套完整纬地工程**有能力**提供的几何段
CAPABILITIES: tuple[str, ...] = (
    "station_sequence",
    "alignment_pi",
    "alignment_element",
    "profile_grade_point",
    "profile_ground_point",
    "superelev_transition",
    "roadbed_width",
    "geometry_point",
    "cross_section",
    "design_control",
    "earthwork_section",
    "roadbed_design_point",
)

# 本适配器**已实现**的段。新增解析器时改这里，测试会逼 IR 与之同步。
IMPLEMENTED: tuple[str, ...] = ("station_sequence", "alignment_pi", "alignment_element",
                                "profile_ground_point", "profile_grade_point",
                                "superelev_transition", "roadbed_width",
                                "design_control",
                                "earthwork_section", "roadbed_design_point",
                                # v0.5 K 节：.HDM 横断面地面线。★它一直在 CAPABILITIES 里，
                                # 但不在 IMPLEMENTED 里 —— 即"声明支持、其实没解析器"。
                                # 这个状态是**故意允许**的（CAPABILITIES 是能力清单，
                                # IMPLEMENTED 是真做了的清单，两者不同才如实反映进度），
                                # 但本工程有 .HDM 文件，长期停在"没解析器"就是静默丢数据。
                                "cross_section")

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
    "superelev_transition": sup,
    "roadbed_width": wid,
    "design_control": ctr,
    "earthwork_section": tf,
    "roadbed_design_point": lj,
    "cross_section": hdm,
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
    "superelev_transition": (".SUP", "超高过渡数据文件"),
    "roadbed_width": (".WID", "路幅宽度数据文件"),
    # ⚠ 两次修正，记下来免得再走一遍：
    #   ① 原写作 (".tf", "土方数据文件（逐桩坐标）") —— **错的**。.tf 实测是 74 列
    #      **土方数据**（文件自带表头，无任何坐标列），见 tf.py。这正是它一直 0 行的原因。
    #   ② 我一度改成 .GTM 并标"待考" —— **也是错的**。读教程 §13.14 并实测文件头：
    #      .gtm 魔数是 `HB  HINT40_GROUP_DTM_VER6`，**二进制**，内容是各数模的
    #      **边界框坐标 + .DTM 路径 + 大小**（实测里读出 534293.6/34.25、537926.6/229.11…），
    #      即说明书原话「纪录了一个项目中所有已建立数模的边界、路径、大小等信息」——
    #      它是**索引**，不含逐桩坐标。
    #   ③ 正解：教程 §13.15 的 **.3DR 横断面三维数据文件**——「由横断面设计绘图时自动生成」、
    #      「纪录每一断面全三维的相关数据信息」、且**纯文本**，形状正对。
    #      本工程**没生成**它，故本段自动落 absent（源里没有），不需要特判。
    "geometry_point": (".3DR", "横断面三维数据文件"),
    "cross_section": (".HDM", "横断面地面线文件"),
    "design_control": (".CTR", "设计参数控制文件"),
    "earthwork_section": (".tf", "土方数据文件"),
    "roadbed_design_point": (".lj", "路基设计中间数据文件"),
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
    # 每个文件自己声明的厂商版本。**不能只留第一个**：实测这套工程里
    # .STA 是 5.84，而 .JD/.pm/.DMX/.ZDM 都是 5.83 —— 只留第一个的话，
    # "另外四个文件是另一个版本"这件事会被静默吃掉，而跨版本格式差异无从保证。
    versions: dict[str, str] = {}
    warns_at_parse: list[str] = []

    for seg in CAPABILITIES:
        suffix, kind = SEGMENT_FILES[seg]
        hits = sorted(p for p in d.iterdir()
                      if p.is_file() and p.suffix.lower() == suffix.lower())
        if not hits:
            files.append({"name": "", "kind": kind, "parse_status": "absent",
                          "note": f"目录内未找到 {suffix} 文件"})
            # ⚠ 顺序要紧：**源里没有**这个文件，比"适配器没做"更根本 ——
            #   源都不在，做不做适配器都导不出东西来。原来写成
            #   "在 IMPLEMENTED 里才报 source_absent，否则报 not_supported"，
            #   结果 .3DR（本工程没生成）被报成 not_supported，把真正的原因盖掉了。
            #   诊断要说的是**真正**的那一个，所以 source_absent 优先。
            reasons[seg] = "source_absent"
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
            versions[seg] = out["vendor_version"]
            segments[seg] = out[parser.PAYLOAD_KEY]
            # 解析器可把「可疑但合法」的发现放在**顶层** notes（不进 payload ——
            # payload 会被原样塞进 IR，而 IR 的段定义是 additionalProperties: false，
            # 内部键会被 schema 拒）。对没有 notes 的解析器这是 no-op。
            warns_at_parse += out.get("notes") or []
            # note 是自由文本，正好用来记该文件自报的版本 —— 这样"哪个文件是哪个版本"
            # 在 source.files 里逐条可见，而不是只留一个汇总值。
            files.append({"name": path.name, "kind": kind, "parse_status": "ok",
                          "note": f"厂商版本 {out['vendor_version']}"})
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
    # （warns_at_parse 是**单文件内部**的发现，由解析器自己给出，先攒着）
    warns: list[str] = list(warns_at_parse)

    # ⓪ 混版告警：同一套工程里出现了多个厂商版本。放在最前，因为它是"整批数据的
    #    来源前提"，后面那些对质结论都建立在"格式一致"这个假设上。
    #    实测：.STA=5.84，.JD/.pm/.DMX/.ZDM=5.83。
    if len(set(versions.values())) > 1:
        detail = "、".join(f"{SEGMENT_FILES[s][0]}（{SEGMENT_FILES[s][1]}）={v}"
                           for s, v in versions.items())
        warns.append(
            f"同一套工程混用了 {len(set(versions.values()))} 个厂商版本：{detail}。"
            f"source.vendor_version 只取了先读到的那个（{version}），**它不代表全部文件**；"
            f"跨版本的格式差异无从保证，按需请对具体文件分别核对")

    # ① 把线形单元挂到交点，并拿 .JD 由坐标独立算出的转角符号去核对 .pm 的转向符号。
    if segments.get("alignment_element") and segments.get("alignment_pi"):
        # 必须 +=，不能 = —— 它是赋值就会把**排在它前面的告警全部抹掉**。
        # 原写法是 =（当年它是第一条，后面几条都用了 +=，只有它没有），
        # 于是本轮新加的"混版告警"算出来了却被静默覆盖，页面上一条都看不到。
        # 这类"算了但丢掉"不报错，和本会话其它几个坑同一性质。
        warns += _link_elements_to_pi(segments["alignment_element"],
                                      segments["alignment_pi"])

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

    # ⑤ 超高过渡：横坡的物理合理性 + 不越出路线范围。
    #    与 ④ **刻意不同**：.ZDM 的设计线必须与路线首尾对齐（短了=没设计到头），
    #    而 .SUP 的过渡点**不必**从 0 开始、也不必到终点结束 —— 没有超高过渡的路段
    #    本来就没有过渡点。所以这里只查越界，不查首尾对齐。
    if segments.get("superelev_transition"):
        warns += sup.check_superelev(segments["superelev_transition"])
        if segments.get("station_sequence"):
            warns += sup.check_against_stations(segments["superelev_transition"],
                                                segments["station_sequence"])

    # ⑥ 路幅宽度：区间连续性 + 覆盖范围。
    #    ⚠ 覆盖检查与 .ZDM 的**含义相反**：.ZDM 不覆盖全线是"设计没做到头"（错误），
    #    而 .WID 不覆盖全线是**源文件的真实缺口**（本工程后 103.960 m 就没有宽度数据）——
    #    报出来是为了让下游知道"落在这段里取不到宽度"，不是因为文件格式不对。
    if segments.get("roadbed_width"):
        warns += wid.check_stations(segments["roadbed_width"])
        if segments.get("station_sequence"):
            warns += wid.check_against_stations(segments["roadbed_width"],
                                                segments["station_sequence"])

    # ⑦ 设计参数控制：分段桩号越界 + 内部一致性（土石占比和为 100、砌护控制只 0/1、
    #    同 (side,kind,group_seq) 桩号重复）。
    #    ⚠ 与 .ZDM/.WID **都不同**：.CTR 的分段桩号**本来就不必覆盖全线** ——
    #    分段变化只在有变化的地方写一行（本工程 ZTFBP 只写了 5805.421 一行，
    #    即整条路一个边坡方案）。故只查越界，不查覆盖。
    if segments.get("design_control"):
        warns += ctr.check_control(segments["design_control"])
        if segments.get("station_sequence"):
            warns += ctr.check_against_stations(segments["design_control"],
                                                segments["station_sequence"])

    # ⑧ 逐桩土方断面（.tf）与逐桩路基设计断面（.lj）。
    #    ⚠ 这两段与 I 节（.CTR）**不同**：它们是**逐桩**文件，实测行数与 .STA 完全相同，
    #    所以对账比的是**集合相等**（少一个桩号 = 那个断面的数据丢了），
    #    而不是像 .CTR 那样只比范围（.CTR 的分段桩号本来就不必覆盖全线）。
    if segments.get("earthwork_section"):
        warns += tf.check_earthwork(segments["earthwork_section"])
        if segments.get("station_sequence"):
            warns += tf.check_against_stations(segments["earthwork_section"],
                                               segments["station_sequence"])

    if segments.get("roadbed_design_point"):
        warns += lj.check_design(segments["roadbed_design_point"])
        if segments.get("station_sequence"):
            warns += lj.check_against_stations(segments["roadbed_design_point"],
                                               segments["station_sequence"])

    # ⑨ 横断面地面线（.HDM）与桩号序列对账。
    #    ⚠ 与 ②（.DMX）**结论不同**，别照抄：`.DMX` 与 `.STA` 实测逐条相同
    #    （332 = 332），那边"条数不等"就是**错**；`.HDM` 实测是 333 vs 332 ——
    #    **多一个 5701.461 是正常的**（测量断面比设计桩号多一个加密点很常见）。
    #    所以 hdm.check_against_stations 不把"条数不等"当错误，只报事实 + 方向：
    #      · .HDM 有、.STA 无 → 正常（加密断面），但要说清有几个；
    #      · .STA 有、.HDM 无 → 更可疑：桩号序列里有断面没测。
    #    正因为 .HDM 不 ⊂ .STA，cross_section_ground_point 才直接带 station_km
    #    而不是锚 station_id —— 这条对账的存在是为了**解释**那个设计，不是为了拦截。
    if segments.get("cross_section") and segments.get("station_sequence"):
        warns += hdm.check_against_stations(segments["cross_section"],
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
           "sta", "jd", "pm", "prj", "dmx", "zdm", "sup", "wid", "ctr",
           "lj", "tf"]
