"""设计数据落库器（契约⑤ 的最后一环）：IR → GE 表，经 M2 的 WriteDao。

三条硬线里的第一条在这里落地
-------------------------------------------------------------------------------
**写只经 M2**。本模块不自己开连接池、不写裸 SQL —— 每一次写入都走
``rpdao.write.WriteDao`` 的写权守卫，越权或写只读表会直接抛 ``WriteGuardError``。

设计要点：把"算什么"与"写什么"分开
-------------------------------------------------------------------------------
``plan(ir)`` 是**纯函数**：IR → 各表的待写行 + 待写批次。不碰数据库。
``verify(ir, planned)`` 也是纯函数：跨段/跨文件的几何一致性检查。
``load(...)`` 才碰库，且只做两件事：把 plan 出来的行写进去、登记批次。

这样切开的理由很实在：落库器的正确性 90% 在"映射对不对"，而那部分**不需要数据库
就能测**（见 ``tests/contract/test_design_import.py`` 第 11 组）。需要真库的部分只剩
事务与写权，那两条由 ``test_write_guard.py`` 覆盖 —— 两边的测试各自都不需要对方。

关于 alignment_pi —— 它是**推导出来的**，不是读进来的
-------------------------------------------------------------------------------
契约⑤ 的两个平面段是同一个东西的两种记法：``.pm`` 的单元链是**真源**，
``.JD`` 的交点是它的**摘要**（两条相邻切线求交即得，实测偏差 3×10⁻⁸ m）。

所以本模块：

* 有单元链 → 用 :func:`adapters.geom.derive_control_points` **推导**交点再入库；
* 只有 ``.JD``（没有 ``.pm``）→ 没有真源可推，才退而用文件里的交点，并记为 ``origin=file``；
* 两者都有 → **推导为主，`.JD` 作验算**。不一致就落进 ``warnings`` 或直接拒收
  （由 ``strict`` 决定），**绝不静默采信其中一份**。

这正是把 `.JD` 从"输入"降为"验算"的落地处：它不再是能悄悄写歪数据的入口，
而变成一道独立来源的防线。
"""
from __future__ import annotations
import pathlib

from typing import Any, Mapping, Sequence

from adapters import base, geom
from adapters.errors import SourceInvalid
# ★ 列名**从适配器取**，不在这里手抄 —— .tf 有 74 列，手抄一遍就多一个会漂移的真源。
from adapters.weidi import lj as lj_mod
from adapters.weidi import tsf as tsf_mod
from adapters.weidi import tf as tf_mod

# 落库器只写这几张表。白名单是刻意的：**新增映射必须在这里显式登记**，
# 免得一个 IR 段的增删悄悄改变写入范围。
LOADABLE_TABLES = ("station_sequence", "alignment_pi", "alignment_element",
                   "profile_grade_point", "profile_ground_point",
                   "superelev_transition", "roadbed_width",
                   "earthwork_section", "roadbed_design_point")

# 推导值与 .JD 文件值的允许偏差。实测全部 ≤ 3.6×10⁻⁸，此处留三个数量级余量，
# 但仍远小于任何有工程意义的差（1 mm = 1×10⁻³）。
TOL_COORD_M = 1e-6      # 坐标、长度、桩号（米）
TOL_ANGLE_DEG = 1e-6    # 方位角、转角（度）

# 推导交点与 .JD 交点的**配对**容差（米）。配对按桩号做，所以这个值要选得
# "远大于任何真实偏差、又远小于相邻两交点的间距"——本项目交点间距在百米级，
# 取 10 m 既不会配错，也不会把一个明显的几何错误误判成"找不到对应"。
PI_MATCH_TOL_M = 10.0

# 20 m 整桩判据：.STA 里 332 个桩号中 291 个是 20 m 整数倍，
# 其余 41 个正好是曲线特征点 ∪ 终点 —— 这个等式本身就是一条跨文件不变量（见 verify）。
INTEGER_STATION_STEP_M = 20.0


class LoadError(Exception):
    """落库前的检查未通过。**在写库之前**抛出，不留半条数据。"""


# --------------------------------------------------------------------- 文本格式
def station_text(station_m: float) -> str:
    """米 → K 格式桩号文本（``545.874`` → ``K0+545.874``）。

    与 ``station_sequence.station_text`` 的注释一致（``K4+635.000`` 这一支）。
    ``.STA`` 的桩号是**相对该路段起点**的里程（0 起），所以千位以内即为 ``K0+…``。
    绝对桩号（``K4635+710`` 那一支）需要路网基准，属 ``road_section`` 的属性，
    不在 IR 里 —— 故 ``station_absolute_km`` 留空，不猜。
    """
    km, m = divmod(round(station_m, 3), 1000.0)
    # 宽度 7 = 3 位整数 + 小数点 + 3 位小数（``000.000``）。写成 6 会得到 ``00.000``，
    # 即 K0+00.000 —— 少一位，肉眼看不出，但按字符串排序或跟图纸比对时立刻出错。
    return f"K{int(km)}+{m:07.3f}"


def is_integer_station(station_m: float) -> bool:
    """是否 20 m 整桩。"""
    r = station_m % INTEGER_STATION_STEP_M
    return r < 1e-9 or (INTEGER_STATION_STEP_M - r) < 1e-9


def station_type(station_m: float, *, first: float, last: float) -> str:
    """桩号类型：起终点 / 整桩 / 加桩（曲线特征点都是加桩）。"""
    if abs(station_m - first) < 1e-9 or abs(station_m - last) < 1e-9:
        return "endpoint"
    return "integer" if is_integer_station(station_m) else "jiazi"


# ----------------------------------------------------------------- IR → 待写行
def _plan_stations(ir: Mapping[str, Any], section_id: int, *,
                   section_start_km: float | None = None) -> list[dict[str, Any]]:
    """桩号序列行。

    ``section_start_km`` 是**该路段起点的绝对桩号**（= ``road_section.start_station_km``）。
    给了就把 ``station_absolute_km`` 填上：``绝对 = 路段起点 + 局部``。

    为什么必须能填：`.STA` 里的桩号是**相对该路段起点**的（毕设是 0→5805.421）。
    而平台其余数据（WIM / 病害 / 试验）用的是**绝对桩号**（如 K4635+000）。
    绝对桩号空着，GE 的几何就永远 JOIN 不上那些数据 —— 而 GE 域的全部价值
    就是当桩号锚定基准。所以这不是可选项，是这一步的意义所在。

    ``station_text`` 保持**局部**写法：它镜像的是 `.STA` 文件里的原文，
    不是派生量。绝对桩号另有 ``station_absolute_km`` 一列，别把两者混起来。
    """
    pts = ir["segments"].get("station_sequence") or []
    if not pts:
        return []
    first, last = pts[0]["station_m"], pts[-1]["station_m"]
    base = section_start_km
    return [{
        "section_id": section_id,
        "station_seq_no": p["seq_no"],
        # 内部字段：落库时按它把 station_id 对给 profile_ground_point。
        # 用**米**（IR 的原始单位）当键，而不是拿 station_local_km 反乘回米 ——
        # 那样要写两次换算，而两次舍入只要差 1e-6 km 就会静默对不上。
        "_station_m": round(p["station_m"], 6),
        "station_local_km": round(p["station_m"] / 1000.0, 6),
        "station_absolute_km": (None if base is None
                                else round(base + p["station_m"] / 1000.0, 6)),
        "station_text": station_text(p["station_m"]),
        "station_type": station_type(p["station_m"], first=first, last=last),
        "is_integer_station": is_integer_station(p["station_m"]),
    } for p in pts]


def _plan_grade_points(ir: Mapping[str, Any], section_id: int) -> list[dict[str, Any]]:
    """``profile_grade_point`` 行（纵断面**设计线**：变坡点）。

    ``station_km`` 是**千米**（表里就是这么定的），IR 里是米 —— 单位换算只在这一处做，
    不留给每个下游各自换算。纵坡与竖曲线长是 ``zdm.derive_grades`` 在适配器阶段
    补出的派生量，这里原样带上：``None`` 就是 ``None``
    （首/末变坡点缺一侧纵坡，或 R>0 却算不出来），不填 0 冒充。
    """
    out = []
    for p in ir["segments"].get("profile_grade_point") or []:
        out.append({
            "section_id": section_id,
            "vpi_seq": p["vpi_seq"],
            "station_km": round(p["station_m"] / 1000.0, 6),
            "elevation_m": round(p["elevation_m"], 6),
            "vertical_curve_radius_m": p.get("vertical_curve_radius_m"),
            "grade_in_pct": p.get("grade_in_pct"),
            "grade_out_pct": p.get("grade_out_pct"),
            "grade_len_m": p.get("grade_len_m"),
            # 错台（教程 §13.3 的 .ZDM 第 4/5 列）。DDL 里这两列**早就有**，
            # 注释也写对了 —— 但 planner 此前**没往下带**，数据就丢在落库之前。
            # 全 0 是正常值（一般公路主线没有错台），不是缺值，故原样写 0。
            "offset_station_km": (round(p["offset_station_m"] / 1000.0, 6)
                                  if p.get("offset_station_m") is not None else None),
            "offset_elev_m": p.get("offset_elev_m"),
        })
    return out


#: 超高六列 → 中文名。顺序即教程 §13.5 的**列位置顺序**（左三 / 桩号 / 右三），
#: 与 ``adapters.weidi.sup.PCT_COLUMNS`` 必须一致。用途有二：生成 ``remark`` 里
#: 「哪几列被 9999 跳过」的可读文字；以及给测试一个稳定的列顺序。
_SUP_PCT_LABELS: tuple[tuple[str, str], ...] = (
    ("earth_shoulder_left_pct", "左侧土路肩横坡"),
    ("hard_shoulder_left_pct", "左侧硬路肩横坡"),
    ("lane_left_pct", "左侧行车道横坡"),
    ("lane_right_pct", "右侧行车道横坡"),
    ("hard_shoulder_right_pct", "右侧硬路肩横坡"),
    ("earth_shoulder_right_pct", "右侧土路肩横坡"),
)


def _plan_superelev_transitions(ir: Mapping[str, Any],
                                section_id: int) -> list[dict[str, Any]]:
    """``superelev_transition`` 行（超高过渡**变化点**，.SUP 的真源）。

    ``station_km`` 是**千米**（表里就是这么定的），IR 里是米 —— 与
    :func:`_plan_grade_points` 同一处换算，不留给下游各自换算。

    ▲ 六个横坡的 ``None`` **原样带过去**，不填 0。IR 里的 ``None`` 来自源文件的
    9999，教程 §13.5 的语义是「可以忽略此数据，横坡渐变至此位置时，系统跳过此数据的
    计算继续进行横坡的超高渐变」—— 也就是**该列在此点不参与约束**。填 0 会把
    "不约束"变成"约束为平坡"，是**造数据**：下游会算出一条源文件里没有的过渡曲线。
    （.SUP 每格非数即 9999，所以 NULL 与"缺值"无歧义。）

    ``remark`` 记下**哪几列被 9999 跳过了**。这不是派生几何量，是**来源注记**：
    表里 NULL 只说明"不约束"，说不出"为什么是 NULL、其他列是否还约束着"，
    而人翻这张表时正需要这一句。
    """
    out = []
    for p in ir["segments"].get("superelev_transition") or []:
        row: dict[str, Any] = {
            "section_id": section_id,
            "transition_seq": p["seq_no"],
            "station_km": round(p["station_m"] / 1000.0, 6),
        }
        ignored: list[str] = []
        for name, label in _SUP_PCT_LABELS:
            v = p.get(name)
            row[name] = v
            if v is None:
                ignored.append(label)
        row["remark"] = (f"源文件 9999（忽略此数据）：{'、'.join(ignored)}"
                         if ignored else None)
        out.append(row)
    return out


def _plan_roadbed_widths(ir: Mapping[str, Any], section_id: int) -> list[dict[str, Any]]:
    """``roadbed_width`` 行（路幅宽度，.WID 的真源）。

    ★ **一行 = 一侧的一个桩号**（与 `superelev_transition` 同形），不折叠成区间：
    区间起终点由同侧相邻两行推得，不落库。见 `wid.py` 顶部的说明。

    ``station_km`` 是**千米**（表里就是这么定的），IR 里是米 —— 与其余 GE 表同一处换算。
    """
    out = []
    for r in ir["segments"].get("roadbed_width") or []:
        out.append({
            "section_id": section_id,
            "side": r["side"],
            "seq_no": r["seq_no"],
            "group_seq": r["group_seq"],
            "station_km": round(r["station_m"] / 1000.0, 6),
            "median_width_m": r.get("median_width_m"),
            "half_carriageway_width_m": r.get("half_carriageway_width_m"),
            "extra_lane_flag": r.get("extra_lane_flag"),
            "hard_shoulder_width_m": r.get("hard_shoulder_width_m"),
            "earth_shoulder_width_m": r.get("earth_shoulder_width_m"),
            "extra_lane_file": r.get("extra_lane_file"),
            "remark": None,          # 组内不一致的说明走 IR 根部 warnings，不落 remark
        })
    return out


def _plan_design_control(ir: Mapping[str, Any], section_id: int) -> dict[str, list[dict[str, Any]]]:
    """``.CTR`` 设计参数控制 → **9 张表**的行（I1–I9）。

    与其余 7 个段不同：`.CTR` 是**关键字驱动**的文件，一个段带 9 张表的载荷
    （教程 §13.10 定义 18 类格式 / 36 个关键字），故这里一次返回 9 个列表。

    ★ 与 A16/A17 同一个锚定方式：``section_id`` + ``station_km``，**键是桩号**。
      **不挂 station_id 外键** —— .CTR 的分段桩号不是 .STA 桩号序列的子集
      （实测：ZDMDG 的 20 个桩号里 1.790/2.610/3.130 都不在序列里）。

    ``station_km`` 是**千米**（表里就是这么定的），IR 里是米 —— 与其余 GE 表同一处换算。

    本工程实测各表行数：22 / 6 / 2 / 2 / 6 / 1 / 4 / 0 / 0。
    后两组（超填、地质/水准点）在源文件里**是空的** —— 空列表不写库，
      不写 0 行，也不造占位行。
    """
    dc = ir["segments"].get("design_control") or {}
    km = lambda m: None if m is None else round(m / 1000.0, 6)   # noqa: E731

    out: dict[str, list[dict[str, Any]]] = {
        "slope_segment": [], "ditch_segment": [], "standard_cross_section": [],
        "roadbed_trench": [], "structure_control": [], "earthwork_composition": [],
        "land_use_width": [], "extra_fill": [], "design_control_text": [],
    }

    for r in dc.get("slope_segments") or []:
        out["slope_segment"].append({
            "section_id": section_id, "side": r["side"], "slope_kind": r["slope_kind"],
            "station_km": km(r["station_m"]), "group_seq": r["group_seq"],
            "slope_ratio": r.get("slope_ratio"),          # 9999（垂直）在解析器里已收成 None
            "control_height_m": r.get("control_height_m"),
            "max_height_m": r.get("max_height_m"),
            "protection": r.get("protection"),
            "remark": None,
        })

    for r in dc.get("ditch_segments") or []:
        out["ditch_segment"].append({
            "section_id": section_id, "side": r["side"], "ditch_kind": r["ditch_kind"],
            "station_km": km(r["station_m"]), "group_seq": r["group_seq"],
            "slope_ratio": r.get("slope_ratio"),
            "height_m": r.get("height_m"),
            "protection": r.get("protection"),
            "remark": None,
        })

    for r in dc.get("standard_cross_sections") or []:
        out["standard_cross_section"].append({
            "section_id": section_id, "side": r["side"], "station_km": km(r["station_m"]),
            "median_half_width_m": r.get("median_half_width_m"),
            "median_crossfall_pct": r.get("median_crossfall_pct"),
            "median_height_m": r.get("median_height_m"),
            "lane_width_m": r.get("lane_width_m"),
            "lane_crossfall_pct": r.get("lane_crossfall_pct"),
            "hard_shoulder_width_m": r.get("hard_shoulder_width_m"),
            "hard_shoulder_crossfall_pct": r.get("hard_shoulder_crossfall_pct"),
            "earth_shoulder_width_m": r.get("earth_shoulder_width_m"),
            "earth_shoulder_crossfall_pct": r.get("earth_shoulder_crossfall_pct"),
            "remark": None,
        })

    for r in dc.get("roadbed_trenches") or []:
        out["roadbed_trench"].append({
            "section_id": section_id, "side": r["side"], "station_km": km(r["station_m"]),
            "median_trench_depth_m": r.get("median_trench_depth_m"),
            "lane_trench_depth_m": r.get("lane_trench_depth_m"),
            "hard_shoulder_trench_depth_m": r.get("hard_shoulder_trench_depth_m"),
            "earth_shoulder_trench_depth_m": r.get("earth_shoulder_trench_depth_m"),
            "remark": None,
        })

    for r in dc.get("structures") or []:
        out["structure_control"].append({
            "section_id": section_id, "structure_kind": r["structure_kind"],
            "anchor_station_km": km(r["anchor_station_m"]), "name": r["name"],
            "start_station_km": km(r.get("start_station_m")),
            "end_station_km": km(r.get("end_station_m")),
            "center_station_km": km(r.get("center_station_m")),
            "angle_deg": r.get("angle_deg"),
            "span_text": r.get("span_text"),             # ★text 不是数值：教程跨径含 + 与 ×
            "structure_form": r.get("structure_form"),
            "control_elev_m": r.get("control_elev_m"),
            "elev_control_type": r.get("elev_control_type"),
            "deck_type": r.get("deck_type"),
            # ★教程正文只列了 2 个尾随整数，但教程示例与实测文件都是 3 个 —— 第 3 个待考
            "trailing_flag": r.get("trailing_flag"),
            "remark": None,
        })

    for r in dc.get("earthwork_compositions") or []:
        out["earthwork_composition"].append({
            "section_id": section_id, "station_km": km(r["station_m"]),
            **{f"pct_{k}": r.get(f"pct_{k}") for k in range(1, 7)},
            "remark": None,
        })

    for r in dc.get("land_use_widths") or []:
        out["land_use_width"].append({
            "section_id": section_id, "side": r["side"], "station_km": km(r["station_m"]),
            "fill_land_width_m": r.get("fill_land_width_m"),
            "cut_land_width_m": r.get("cut_land_width_m"),
            "remark": None,
        })

    for r in dc.get("extra_fills") or []:
        out["extra_fill"].append({
            "section_id": section_id, "side": r.get("side"), "fill_kind": r["fill_kind"],
            "station_km": km(r["station_m"]),
            "width_m": r.get("width_m"), "thickness_m": r.get("thickness_m"),
            "remark": None,
        })

    for r in dc.get("design_control_texts") or []:
        out["design_control_text"].append({
            "section_id": section_id, "text_kind": r["text_kind"],
            "station_km": km(r["station_m"]), "name": r.get("name"),
            "elev_m": r.get("elev_m"), "content": r.get("content"),
            "remark": None,
        })

    return out


def _plan_earthwork_sections(ir: Mapping[str, Any],
                             section_id: int) -> list[dict[str, Any]]:
    """``earthwork_section`` 行（逐桩土方断面，.tf 的真源）。

    ▲ 本表**同时**锚 ``section_id`` 与 ``station_id``：它属于某个路段，又是逐桩数据。
    与 ``profile_ground_point`` 一样，plan 里只带内部 ``_station_m``，
    真实 station_id 只有写进 station_sequence 之后才存在（见 :func:`load`）。

    ``station_km`` 是**千米**（表里就是这么定的），IR 里是米 —— 与其余 GE 表同一处换算。
    ★ 它在这里是**冗余**的（有 station_id 就够定位），保留是为了可追溯：
    直接看这张表就能读出桩号，不必回 join station_sequence。

    ★ 列名来自 ``tf_mod.COLUMNS`` —— 74 列一个不落地照收，含本工程全为 0 的 44 列。
    建表原则是「照数据文件的样式，好追溯」，不是"只留有用的"。
    """
    out = []
    for p in ir["segments"].get("earthwork_section") or []:
        row: dict[str, Any] = {
            "section_id": section_id,
            "station_km": round(p["station_m"] / 1000.0, 6),
            "_station_m": round(p["station_m"], 6),
            "remark": None,
        }
        for _, en in tf_mod.COLUMNS:
            if en == "station_m":
                continue
            row[en] = p.get(en)
        out.append(row)
    return out


def _plan_roadbed_design_points(ir: Mapping[str, Any],
                                section_id: int) -> list[dict[str, Any]]:
    """``roadbed_design_point`` 行（逐桩路基设计断面，.lj 的真源）。

    同 ``_plan_earthwork_sections``：锚 section_id + station_id，内部带 ``_station_m``。
    列名来自 ``lj_mod.COLUMNS``（24 列，含两个**待考**列 —— 说明书没有对应项，
    按位置命名、原样照收，不猜也不丢）。
    """
    out = []
    for p in ir["segments"].get("roadbed_design_point") or []:
        row: dict[str, Any] = {
            "section_id": section_id,
            "station_km": round(p["station_m"] / 1000.0, 6),
            "_station_m": round(p["station_m"], 6),
            "remark": None,
        }
        for en in lj_mod.COLUMNS:
            if en == "station_m":
                continue
            row[en] = p.get(en)
        out.append(row)
    return out


def _plan_ground_points(ir: Mapping[str, Any]) -> list[dict[str, Any]]:
    """``profile_ground_point`` 行（纵断面**地面线**：逐桩原始地形高程）。

    ▲ 本表**没有 section_id 列** —— 它是 GE 域里唯一锚在 ``station_id`` 上的表。
    所以这里只带载荷与一个内部 ``_station_m``：id 只有写进 station_sequence 之后
    才存在，而 plan 是纯函数、不碰数据库。解析发生在 :func:`load` 里。
    """
    out = []
    for p in ir["segments"].get("profile_ground_point") or []:
        out.append({
            "ground_elev_m": round(p["ground_elev_m"], 6),
            "_station_m": round(p["station_m"], 6),
        })
    return out


def _plan_cross_section_points(ir: Mapping[str, Any],
                               section_id: int) -> list[dict[str, Any]]:
    """``cross_section_ground_point`` 行（横断面**地面线**测点：`.HDM` 逐点摊平）。

    ★★ 与 :func:`_plan_ground_points` 的**关键差异**，别照抄那边：
       `profile_ground_point` 锚 ``station_id``，所以 plan 里带内部 ``_station_m``，
       落库时在 `load` 里换成真实 id。
       **本表不锚 station_id** —— `.HDM` 实测 333 个断面 vs `station_sequence` 332 个
       （多一个 5701.461），**不是子集**，换不出 id 来。
       所以这里直接带 ``station_km``，落库时**不需要任何 id 解析**，也没有"挂空"风险。
       这也正是它和 `.CTR`/`.SUP`/`.WID` 同类（直接带桩号）的原因。

    嵌套载荷 → 扁平行：一个断面 → 左右两侧 → 每侧若干测点，摊成一行一个测点。
    ``seq_no`` 从 1 起、与文件中的测量顺序一致；**每侧点数不落列**（那是 COUNT(*) 派生量）。
    """
    out: list[dict[str, Any]] = []
    for sec in ir["segments"].get("cross_section") or []:
        # IR 里是**米**（源文件原生单位），落库换算成 km —— 与全库 station*_km 一致。
        station_km = round(sec["station_m"] / 1000.0, 6)
        # ★ 侧的词表必须与全库其余 7 张 side 表一致：**left / right**。
        #   `.HDM` 源文件**没有任何侧的标记**（靠"3 行一组"的位置关系：
        #   桩号 / 左行 / 右行），所以两侧是**位置推出来的**，不是读到的。
        #   一开始写成 "L"/"R"，与 roadbed_width 等 7 张表不一致 —— 已改。
        #   （源文件里写 [LEFT]/[RIGHT] 的是 `.WID`，见 DDL 里 roadbed_width 的注释。）
        for side, pts in (("left", sec["left"]), ("right", sec["right"])):
            for i, pt in enumerate(pts, start=1):
                out.append({
                    "section_id": section_id,
                    "station_km": station_km,
                    "side": side,
                    "seq_no": i,
                    "offset_m": round(pt["offset_m"], 4),
                    "elev_diff_m": round(pt["elev_diff_m"], 4),
                })
    return out


def _plan_elements(ir: Mapping[str, Any], section_id: int) -> list[dict[str, Any]]:
    """``alignment_element`` 行。**`curvature_1pm` 不再写入** —— 该列已随工单 #3 删除，
    κ 由 ``radius_start_m``/``radius_end_m`` 表达（缓和曲线单元的 κ 不是常数，一列装不下）。

    ▲ 单元↔交点的挂接（``pi_id``）在**这里自己算**，不用 IR 里可能存在的 ``pi_seq`` 批注。
    理由：那个批注是 ``weidi.build_ir`` 阶段加的，手工拼的 IR、别的厂商适配器、
    或者上游改版都可能没有它 —— 而"这个单元属于哪个交点"是**几何事实**，
    重算一遍（``geom.curve_groups``）比信任一个可能缺席的字段可靠。
    （本模块第一次跑就踩了这个：批注缺席 → ``pi_id`` 全 NULL → 单元挂不上交点。）
    """
    els = ir["segments"].get("alignment_element") or []
    # 组号即 ``geom.derive_control_points`` 里的交点序号（都用同一个 curve_groups）
    pi_seq_of = {e["seq"]: i
                 for i, g in enumerate(geom.curve_groups(els), start=1)
                 for e in g}
    out = []
    for e in els:
        c = e.get("center") or {}
        out.append({
            "section_id": section_id,
            "element_seq": e["seq"],
            "element_type": e["type"],
            "start_station_km": round(e["start_station_m"] / 1000.0, 6),
            "end_station_km": round(e["end_station_m"] / 1000.0, 6),
            # length_m 是生成列（＝桩号差），**不能也不该写**
            "start_x": e.get("start_x"), "start_y": e.get("start_y"),
            "end_x": e.get("end_x"), "end_y": e.get("end_y"),
            "center_x": c.get("x"), "center_y": c.get("y"),
            "azimuth_deg": e.get("azimuth_deg"),
            "end_azimuth_deg": e.get("end_azimuth_deg"),
            "radius_start_m": e.get("radius_start_m"),
            "radius_end_m": e.get("radius_end_m"),
            "_pi_seq": pi_seq_of.get(e["seq"]),   # 内部用：落库时换成 pi_id，不入表
        })
    return out


def _plan_pi_from_elements(ir: Mapping[str, Any],
                           section_id: int) -> list[dict[str, Any]]:
    """由单元链**推导**交点行（.JD 不参与）。"""
    els = ir["segments"].get("alignment_element") or []
    if not els:
        return []
    # geom 用 length_m / start_station_m 等"文件口径"的名字，这里先补上
    shaped = [dict(e, length_m=e["end_station_m"] - e["start_station_m"]) for e in els]
    out = []
    for p in geom.derive_control_points(shaped):
        out.append({
            "section_id": section_id,
            "pi_seq": p["seq"],
            "pi_type": "JD",
            # ★交点桩号：.JD 里本来就有（f10[0]），**不是纯派生量** —— 这里写派生值，
            #   verify() 拿它与文件里的对账（两条路独立，实测差 ≤2.4e-8 m）。
            "station_km": round(p["station_m"] / 1000.0, 6),
            "x_coord": round(p["x"], 6),
            "y_coord": round(p["y"], 6),
            "radius_m": p["radius_m"],
            "spiral_ls1_m": p["spiral_ls1"],
            "spiral_ls2_m": p["spiral_ls2"],
            # spiral_a1/a2 是生成列，**不能写**
            "prev_tangent_len_m": p["prev_tangent_len_m"],
            "tangent_len_m": p["tangent_len_m"],
            "tangent_len2_m": p["tangent_len2_m"],
            "arc_len_m": p["arc_len_m"],
            "curve_len_m": p["curve_len_m"],
            "deflection_deg": p["deflection_deg"],
            "external_m": p["external_m"],
            # 内部字段（下划线开头，落库前剔除，不入表）：
            #   _station_m 是**派生值**（ZH + 切线长），verify ② 拿它与 .JD 的
            #   station_m 逐条对质（两条路独立，实测差 ≤2.4e-8 m）。
            #   ⚠ 原注释写「交点桩号是派生量，故 DDL 里没有它的列」—— **前提是错的**：
            #   .JD 本来就给了它（f10[0]）。落库写的是 station_km（见上），
            #   这里保留 _station_m 是因为对质用米、入库用公里，两处单位不同。
            "_station_m": p["station_m"],
            "_feat_stations_m": list(p["feat_stations_m"]),
            "_source": "derived",
        })
    return out


def _plan_pi_from_file(ir: Mapping[str, Any], section_id: int) -> list[dict[str, Any]]:
    """没有单元链时的退路：用 ``.JD`` 里读到的交点（origin=file）。"""
    out = []
    for i, p in enumerate(ir["segments"].get("alignment_pi") or []):
        if p.get("pi_type") in ("QD", "ZD"):
            continue                            # 起终点不是交点，没有曲线参数
        out.append({
            "section_id": section_id,
            "pi_seq": len(out) + 1,
            "pi_type": p.get("pi_type") or "JD",
            "station_km": (round(p["station_m"] / 1000.0, 6)
                           if p.get("station_m") is not None else None),
            "x_coord": round(p["x"], 6),
            "y_coord": round(p["y"], 6),
            "radius_m": p.get("radius_m"),
            "spiral_ls1_m": p.get("spiral_ls1"),
            "spiral_ls2_m": p.get("spiral_ls2"),
            "prev_tangent_len_m": p.get("prev_tangent_len_m"),
            "tangent_len_m": p.get("tangent_len_m"),
            "tangent_len2_m": p.get("tangent_len2_m"),
            "arc_len_m": p.get("arc_len_m"),
            "curve_len_m": p.get("curve_len_m"),
            "deflection_deg": p.get("deflection_deg"),
            "external_m": p.get("external_m"),
            "_source": "file",
        })
    return out


def plan(ir: Mapping[str, Any], *, section_id: int,
         section_start_km: float | None = None) -> dict[str, Any]:
    """IR → 待写行。**纯函数，不碰数据库**（故可离线测）。

    ``section_start_km`` 见 :func:`_plan_stations`。

    返回 ``{"tables": {表名: [行…]}, "pi_source": "derived"|"file"|None}``。
    """
    pi_derived = _plan_pi_from_elements(ir, section_id)
    pi_file = _plan_pi_from_file(ir, section_id)
    pi_rows, pi_source = (pi_derived, "derived") if pi_derived else \
                         ((pi_file, "file") if pi_file else ([], None))
    tables = {
        "station_sequence": _plan_stations(ir, section_id,
                                           section_start_km=section_start_km),
        "alignment_pi": pi_rows,
        "alignment_element": _plan_elements(ir, section_id),
        "profile_grade_point": _plan_grade_points(ir, section_id),
        "profile_ground_point": _plan_ground_points(ir),
        "superelev_transition": _plan_superelev_transitions(ir, section_id),
        "roadbed_width": _plan_roadbed_widths(ir, section_id),
        "earthwork_section": _plan_earthwork_sections(ir, section_id),
        "roadbed_design_point": _plan_roadbed_design_points(ir, section_id),
        # v0.5 K 节：.HDM 横断面地面线。★与上面那些不同，本表**不做 station_id 中转**
        # （见 _plan_cross_section_points），所以它是这里唯一"plan 完就能直接写"的逐桩表。
        "cross_section_ground_point": _plan_cross_section_points(ir, section_id),
    }
    # .CTR 一个段带 9 张表 —— 展开进同一张 tables 字典，键就是**物理表名**，
    # 故 load / verify / 测试都按表名取，不需要知道它们同源。
    tables.update(_plan_design_control(ir, section_id))
    return {"tables": tables, "pi_source": pi_source,
            "pi_from_file_ignored": bool(pi_derived) and bool(pi_file)}


# ----------------------------------------------------------------- 一致性检查
def _close(a: Any, b: Any, tol: float) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= tol


def verify(ir: Mapping[str, Any], planned: Mapping[str, Any],
           *, tol_coord: float = TOL_COORD_M,
           tol_angle: float = TOL_ANGLE_DEG) -> dict[str, list[str]]:
    """跨段/跨文件的一致性检查。**纯函数**。

    返回 ``{"errors": [...], "warnings": [...]}``：``errors`` 一律拒收（写库前抛出），
    ``warnings`` 是"可疑但合法"（照写，但记进批次备注）。

    这里放的都是**独立来源**之间的对质 —— 同一份数据自己跟自己比不算检查。
    """
    errors: list[str] = []
    warnings: list[str] = list(ir.get("warnings") or [])

    els = ir["segments"].get("alignment_element") or []
    pis = ir["segments"].get("alignment_pi") or []

    # ① 单元链连续：单元 k 的终点 ≡ 单元 k+1 的起点。断了就说明解析或数据本身有问题，
    #    后面所有推导都不可信 —— 这是**硬错误**。
    for a, b in zip(els, els[1:]):
        if a["seq"] + 1 != b["seq"]:
            errors.append(f"单元序号不连续：{a['seq']} → {b['seq']}")
        if not _close(a["end_station_m"], b["start_station_m"], 1e-6):
            errors.append(f"单元 {a['seq']}→{b['seq']} 桩号链断裂："
                          f"{a['end_station_m']} ≠ {b['start_station_m']}")
        if not (_close(a.get("end_x"), b.get("start_x"), 1e-6)
                and _close(a.get("end_y"), b.get("start_y"), 1e-6)):
            errors.append(f"单元 {a['seq']}→{b['seq']} 坐标链断裂")

    # ② 推导交点 vs .JD（**两份独立来源**）：这是把 .JD 从输入降为验算之后真正的用处。
    #    比的是"推导值 vs 文件值"，两边都不读对方 —— 同源自比不算检查。
    #
    #    ⚠ 按**桩号**配对，不按序号。两份文件的覆盖范围可能不同（一个是节选、
    #    一个不含某段），按序号硬配会把"覆盖不同"误报成"数值不符" ——
    #    这正是假警报的来源。配不上的点各自单独说明，不混进数值对质里。
    if planned["pi_source"] == "derived" and pis:
        jd_pi = [p for p in pis if p.get("pi_type") not in ("QD", "ZD")]
        mine = planned["tables"]["alignment_pi"]
        pairs = (("_station_m", "station_m", tol_coord),
                 ("x_coord", "x", tol_coord), ("y_coord", "y", tol_coord),
                 ("radius_m", "radius_m", tol_coord),
                 ("spiral_ls1_m", "spiral_ls1", tol_coord),
                 ("spiral_ls2_m", "spiral_ls2", tol_coord),
                 ("tangent_len_m", "tangent_len_m", tol_coord),
                 ("tangent_len2_m", "tangent_len2_m", tol_coord),
                 ("arc_len_m", "arc_len_m", tol_coord),
                 ("curve_len_m", "curve_len_m", tol_coord),
                 ("external_m", "external_m", tol_coord),
                 ("deflection_deg", "deflection_deg", tol_angle))
        used: set[int] = set()
        for got in mine:
            st = got["_station_m"]
            k = min(range(len(jd_pi)),
                    key=lambda i: abs(jd_pi[i]["station_m"] - st), default=None)
            if k is None or abs(jd_pi[k]["station_m"] - st) > PI_MATCH_TOL_M:
                warnings.append(f"交点 {got['pi_seq']}（桩号 {st:.3f}）在 .JD 里找不到对应，未对质")
                continue
            used.add(k)
            want = jd_pi[k]
            for mine_key, file_key, tol in pairs:
                want_v = want.get(file_key)
                if not _close(got.get(mine_key), want_v, tol):
                    diff = (abs(float(got[mine_key]) - float(want_v))
                            if got.get(mine_key) is not None and want_v is not None else None)
                    warnings.append(
                        f"交点 {got['pi_seq']} 的 {mine_key.lstrip('_')}：推导 "
                        f"{got.get(mine_key)} ≠ .JD {want_v}"
                        + (f"（差 {diff:.3e}）" if diff is not None else ""))
        missed = [p for i, p in enumerate(jd_pi) if i not in used]
        if missed:
            warnings.append(
                f".JD 里有 {len(missed)} 个交点在本次推导范围内没有对应"
                f"（桩号 {[round(p['station_m'], 3) for p in missed[:4]]}）"
                "—— 通常是两份文件覆盖范围不同，不是几何错误")

    # ③ ★跨文件不变量：.STA 的**非 20 m 整桩**必须恰好等于
    #    「由 .pm 单元链推出的各交点 5 个特征点」∪ {首桩, 末桩}。
    #    两条完全独立的路径（逐桩序列 vs 线形几何）算出同一个集合 —— 这是全项目
    #    最强的一条交叉验证，且**不需要 .JD 在场**（.STA + .pm 即可）。
    #    实测 G228 试验段：41 个非整桩 ≡ 40 个特征点 + 终点，全等。
    sts = [p["station_m"] for p in (ir["segments"].get("station_sequence") or [])]
    if sts and planned["pi_source"] == "derived":
        odd = {round(s, 3) for s in sts if not is_integer_station(s)}
        feat = {round(s, 3) for row in planned["tables"]["alignment_pi"]
                for s in row.get("_feat_stations_m") or []}
        expect = feat | {round(sts[0], 3), round(sts[-1], 3)}
        only_sta = sorted(odd - expect)
        if only_sta:
            warnings.append(
                f".STA 有 {len(only_sta)} 个非整桩不在任何曲线特征点上：{only_sta[:6]}"
                + ("…" if len(only_sta) > 6 else ""))

    # ④ 桩号序列自身：序号必须是 1..N，桩号必须严格递增
    seq = [p["seq_no"] for p in (ir["segments"].get("station_sequence") or [])]
    if seq and seq != list(range(1, len(seq) + 1)):
        errors.append("桩号序列的 seq_no 不是严格的 1..N")
    for a, b in zip(sts, sts[1:]):
        if b <= a:
            errors.append(f"桩号非严格递增：{a} → {b}")
            break

    return {"errors": errors, "warnings": warnings}


# --------------------------------------------------------------------- 落库
#: ``.CTR`` 的 9 张表 → ``on_conflict`` 目标列。
#:
#: ★ **必须是模块级常量**，不能藏在 ``load()`` 里当局部变量：契约测试的
#: ★★ 对账检查（源码 ``on_conflict`` ↔ 实库 ``pg_constraint``）是**静态**读源码的，
#: 它抠得到内联元组，抠不到循环里的变量 —— 那 9 对就会**静默漏检**。
#: 实测确实漏了：9 处写成局部变量时，对账测试只覆盖 12 对（A0–A17 的内联那批），
#: 而 .CTR 的 9 对完全没被比过。提到模块级后，测试改为直接读本常量，
#: 覆盖面从 12 对变成 21 对。
#:
#: ⚠ 列序必须与 DDL 的 UNIQUE **完全一致**，否则 PG 报
#:   "no unique or exclusion constraint matching the ON CONFLICT specification"。
#:   这一条曾经真出过（superelev_transition 用错列序），故有 ★★ 对账测试钉住。
CTR_ON_CONFLICT: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("slope_segment",           ("section_id", "side", "slope_kind", "station_km", "group_seq")),
    ("ditch_segment",           ("section_id", "side", "ditch_kind", "station_km", "group_seq")),
    ("standard_cross_section",  ("section_id", "side", "station_km")),
    ("roadbed_trench",          ("section_id", "side", "station_km")),
    ("structure_control",       ("section_id", "structure_kind", "anchor_station_km")),
    ("earthwork_composition",   ("section_id", "station_km")),
    ("land_use_width",          ("section_id", "side", "station_km")),
    ("extra_fill",              ("section_id", "side", "fill_kind", "station_km")),
    ("design_control_text",     ("section_id", "text_kind", "station_km")),
)


def load(ir: Mapping[str, Any], dao: Any, *,
         section_id: int, batch_no: str,
         source_desc: str | None = None,
         section_start_km: float | None = None,
         writer: str = "M2",
         dry_run: bool = False,
         strict: bool = True) -> dict[str, Any]:
    """把 IR 落进 GE 表。**多表同事务**：要么全成，要么全不成。

    * ``dry_run=True``：只做 plan + verify，**一行都不写**（预检页用这个）。
    * ``strict=True``：``verify`` 出 ``errors`` 就抛 :class:`LoadError`；否则降级为警告。

    写权由 ``WriteDao`` 守卫逐表核对（``catalog.TABLE_OWNER``），本模块无从绕过。
    """
    # 没显式给路段起点就从库里读 —— 它决定绝对桩号，属路段自身的属性，
    # 不该要求每个调用方自己记着传（忘了传就是一批 NULL，且不会报错）。
    if section_start_km is None and not dry_run:
        row = dao.query_one(
            "SELECT start_station_km FROM road_section WHERE id = %(s)s",
            {"s": section_id})
        if row and row["start_station_km"] is not None:
            section_start_km = float(row["start_station_km"])

    planned = plan(ir, section_id=section_id, section_start_km=section_start_km)
    verdict = verify(ir, planned)
    if verdict["errors"] and strict:
        raise LoadError("落库前检查未通过：\n  - " + "\n  - ".join(verdict["errors"]))

    tables = planned["tables"]
    counts = {t: len(rows) for t, rows in tables.items()}
    report: dict[str, Any] = {
        "batch_no": batch_no,
        "section_id": section_id,
        "geometry_level": ir.get("geometry_level"),
        "section_start_km": section_start_km,
        "pi_source": planned["pi_source"],
        "pi_from_file_ignored": planned["pi_from_file_ignored"],
        "planned": counts,
        "written": {t: 0 for t in tables},
        "warnings": verdict["warnings"],
        "errors": verdict["errors"],
        "dry_run": dry_run,
    }
    if dry_run:
        return report

    # ---- 批次行先备好（放同一个事务里，批次不会先于数据存在）
    batch = {
        "batch_no": batch_no,
        "source_type": "file",
        "source_desc": (source_desc or _default_desc(ir))[:256],
        "raw_count": sum(counts.values()),
        "valid_count": sum(counts.values()),
        "quality_code": "OK" if not verdict["warnings"] else "WARN",
        "handler": f"{writer}-design-import",
        "remark": _batch_remark(ir, planned, verdict),
    }

    with dao.write_txn(writer=writer) as tx:
        # ① 桩号序列（一等实体）：其余表都以它/路段为锚。
        #    这里用**逐行 insert_returning**（而不是批量 insert）的唯一理由是拿回
        #    station_id —— profile_ground_point 锚的就是它，而这一层不许读库，
        #    id 只能从写入拿。on_conflict 给出时走 DO UPDATE，故重放也必返回 id（非 None）。
        station_id_by_m: dict[float, int] = {}
        if tables["station_sequence"]:
            for row in tables["station_sequence"]:
                clean = {k: v for k, v in row.items() if not k.startswith("_")}
                station_id_by_m[row["_station_m"]] = tx.insert_returning(
                    "station_sequence", clean,
                    on_conflict=("section_id", "station_local_km"))
            report["written"]["station_sequence"] = len(tables["station_sequence"])

        # ② 交点：先落，因为单元表要 FK 指向它。
        #    按 (section_id, pi_seq) 幂等 —— 重放同一批不会撞唯一约束，
        #    且仍能拿回 id 去挂单元（这正是给 insert_returning 补 on_conflict 的原因）。
        pi_id_by_seq: dict[int, int] = {}
        if tables["alignment_pi"]:
            for row in tables["alignment_pi"]:
                clean = {k: v for k, v in row.items() if not k.startswith("_")}
                pi_id_by_seq[row["pi_seq"]] = tx.insert_returning(
                    "alignment_pi", clean, on_conflict=("section_id", "pi_seq"))
            report["written"]["alignment_pi"] = len(tables["alignment_pi"])

        # ③ 线形单元：把内部的 _pi_seq 换成真实 pi_id
        if tables["alignment_element"]:
            element_rows = []
            for row in tables["alignment_element"]:
                clean = {k: v for k, v in row.items() if not k.startswith("_")}
                clean["pi_id"] = pi_id_by_seq.get(row.get("_pi_seq"))
                element_rows.append(clean)
            report["written"]["alignment_element"] = tx.insert(
                "alignment_element", element_rows,
                on_conflict=("section_id", "element_seq"))

        # ④ 纵断面设计线：锚 section_id，与其余 GE 表相同
        if tables["profile_grade_point"]:
            report["written"]["profile_grade_point"] = tx.insert(
                "profile_grade_point", tables["profile_grade_point"],
                on_conflict=("section_id", "vpi_seq"))

        # ⑤ 纵断面地面线：把内部 _station_m 换成真实 station_id。
        #    查不到就**在写之前抛错**，绝不留一条挂空的地面点 ——
        #    逐桩高程错位不会报任何错，只会让整条路的地形静默错位。
        if tables["profile_ground_point"]:
            ground_rows = []
            for row in tables["profile_ground_point"]:
                sid_of_station = station_id_by_m.get(row["_station_m"])
                if sid_of_station is None:
                    raise LoadError(
                        f"地面线桩号 {row['_station_m']} m 在桩号序列里找不到对应"
                        f"（section_id={section_id}）—— 本表锚 station_id，错位不报错，"
                        f"故此处直接拒收")
                ground_rows.append({"station_id": sid_of_station,
                                    "ground_elev_m": row["ground_elev_m"]})
            report["written"]["profile_ground_point"] = tx.insert(
                "profile_ground_point", ground_rows, on_conflict=("station_id",))

        # ⑤b 横断面地面线测点：**不做 station_id 中转**，直接写。
        #     `.HDM` 不是 `station_sequence` 的子集（333 vs 332，多 5701.461），
        #     换不出 station_id —— 故本表直接带 station_km。
        #     对比 ⑤：那边查不到就抛 LoadError（因为锚错了会静默错位）；
        #     这边**没有可锚错的 id**，风险由 UNIQUE(section_id, station_km, side, seq_no)
        #     与解析器的"3 行一组 + 点数自校验"兜住。
        if tables["cross_section_ground_point"]:
            report["written"]["cross_section_ground_point"] = tx.insert(
                "cross_section_ground_point", tables["cross_section_ground_point"],
                on_conflict=("section_id", "station_km", "side", "seq_no"))

        # ⑥ 超高过渡变化点：锚 section_id，与其余 GE 表相同。
        #    六个横坡的 None 是**源文件 9999「忽略此数据」**，原样写 NULL，不填 0
        #    （见 _plan_superelev_transitions 的说明：填 0 会把"不约束"变成
        #    "约束为平坡"，下游会算出一条源文件里没有的过渡曲线）。
        if tables["superelev_transition"]:
            report["written"]["superelev_transition"] = tx.insert(
                "superelev_transition", tables["superelev_transition"],
                on_conflict=("section_id", "station_km"))

        # ⑦ 路幅宽度分段：锚 section_id，与其余 GE 表相同
        if tables["roadbed_width"]:
            report["written"]["roadbed_width"] = tx.insert(
                "roadbed_width", tables["roadbed_width"],
                on_conflict=("section_id", "side", "station_km"))

        # ⑧ 设计参数控制（.CTR）9 张表 —— 全部锚 section_id + station_km，键是桩号。
        #    为什么键里必须带 group_seq（前两张）：同一个桩号下天然有多级边坡/多个沟折点
        #    （本工程填方 5 级、挖方 6 级、边沟 3 折点），不带就等于把它们折叠成一行。
        #    为什么 I3 standard_cross_section 也在写：它是 A17 roadbed_width 的**独立校验源**，
        #    两个来源不同的文件在同一组数上对上，是这套解析链最硬的一条证据。
        #    ⚠ on_conflict 的列序必须与 DDL 的 UNIQUE 完全一致，否则 PG 报
        #      "no unique or exclusion constraint matching the ON CONFLICT specification"。
        #      这一条曾经真出过（superelev_transition 用错列序），故有 ★★ 对账测试钉住。
        for _t, _oc in CTR_ON_CONFLICT:
            if tables.get(_t):
                report["written"][_t] = tx.insert(_t, tables[_t], on_conflict=_oc)

        # ⑨ 逐桩土方断面（.tf）与逐桩路基设计断面（.lj）——
        #    **同时锚 section_id 与 station_id**：既属于某个路段，又是逐桩数据。
        #    与 ⑤ 地面线同一个道理：把内部 _station_m 换成真实 station_id，
        #    **查不到就在写之前抛错**，绝不留一条挂空的断面行 ——
        #    逐桩数据错位不会报任何错，只会让整条路的土方量与路幅断面静默错位。
        #    on_conflict 用 (section_id, station_id)：与 DDL 的 UNIQUE 完全一致。
        for _t, _seg in (("earthwork_section", "earthwork_section"),
                         ("roadbed_design_point", "roadbed_design_point")):
            if not tables.get(_t):
                continue
            _rows = []
            for row in tables[_t]:
                sid_of_station = station_id_by_m.get(row["_station_m"])
                if sid_of_station is None:
                    raise LoadError(
                        f"{_seg} 桩号 {row['_station_m']} m 在桩号序列里找不到对应"
                        f"（section_id={section_id}）—— 本表锚 station_id，错位不报错，"
                        f"故此处直接拒收")
                _rows.append({**{k: v for k, v in row.items() if k != "_station_m"},
                              "station_id": sid_of_station})
            report["written"][_t] = tx.insert(
                _t, _rows, on_conflict=("section_id", "station_id"))

        # ⑩ 批次登记
        tx.insert("data_import_batch", [batch], on_conflict=("batch_no",))

    return report


def _default_desc(ir: Mapping[str, Any]) -> str:
    files = [f.get("file_name") or "" for f in (ir.get("source") or {}).get("files", [])]
    return "＋".join(f for f in files if f) or "design-import"


def _batch_remark(ir: Mapping[str, Any], planned: Mapping[str, Any],
                  verdict: Mapping[str, Any]) -> str:
    """批次的备注：**导入当时**的几何等级 / 交点来源 / 缺口与告警。

    ⚠ 几何等级与缺口**没有独立列可放** —— ``data_import_batch`` 是采集批次表，
    不含这几个字段。放在 remark 里意味着**不可查询**（只能看全文）。
    若以后要按等级挑批次，需要给该表加工单加列；此处不擅自扩表。

    ★★ 「**导入当时**」这四个字是后加的，加它是因为一个**真实发生过的误读**：
      毕设路段有两条批次 ——
        id=8   WEIDI-BS-2026-09-17  备注「几何等级 L3｜…缺口 not_supported×1」
        id=155 WEIDI-BS-2026-09-HDM 备注「几何等级 L4｜…缺口 source_absent×1」
      两条都对：8 是 .HDM 适配器**还没写**时导的（当时确实 L3，缺口确实叫
      not_supported），155 是写完 .HDM 之后导的（L4）。
      但 8 那句原文只写「几何等级 L3」，**读起来像当前状态** —— 而它早就不是了。
      这正是本会话反复出现的同一形状：**一个看起来权威的过期断言**。

    ★ 等级是**派生量**，不该存（见契约⑤ README「推导得出，不允许外部传入」）。
      所以补救不是加列，而是**说清这个数字的时点**。要拿**当前**等级有两条路：
        · M3 ``GeRepository`` 现算（``rpdao/repo.py`` 的 completeness.geometry_level）；
        · 或由 ``design_file.parse_status = 'ok'`` 反推段集合再 ``derive_level()``。
      两者都以「当前库里有什么」为准，不受本备注的时点影响。
    """
    gaps = ir.get("gaps") or []
    parts = [f"导入当时几何等级 {ir.get('geometry_level')}",
             f"交点来源 {planned['pi_source']}"]
    if gaps:
        by_reason: dict[str, int] = {}
        for g in gaps:
            by_reason[g["reason"]] = by_reason.get(g["reason"], 0) + 1
        parts.append("缺口 " + "／".join(f"{k}×{v}" for k, v in sorted(by_reason.items())))
    if verdict["warnings"]:
        parts.append(f"告警 {len(verdict['warnings'])} 条：" + "；".join(verdict["warnings"][:3]))
    return "｜".join(parts)[:2000]


# ============================================================ 档案与路段（第一段）
# `.PRJ` 走这里，不走 `plan()`/`load()` —— 见 adapters/weidi/prj.py 的说明：
# 它是**项目档案**（design_project / section_design_attr / design_file + road_line /
# road_section），不是 `road_geometry_ir` 的 geometry segment。
#
# 两段式的理由不只是"schema 塞不下"：**档案必须先于几何存在**。
# 几何表用 FK 锚在 `road_section.id` 上，路段没建出来就没有 section_id 可用。
# 所以顺序是天生的：档案 → 路段 → 几何。
ARCHIVE_TABLES = ("design_project", "road_line", "road_section",
                  "section_design_attr", "design_file",
                  # v0.5 L 节：工程级的设计输入（锚 design_project，非 section）
                  "earthwork_factor")

#: 已实现适配器的后缀 → 该文件可解析。用于 design_file.parse_status。
#  ⚠ 这张表曾漏掉 .ctr/.tf/.lj 三个**已经实现**的后缀，导致它们被标成 pending
#  （"适配器尚未实现"）—— 表是手抄的，就会漂。所以下面有一条测试钉住：
#  **凡是 adapters/weidi 里 IMPLEMENTED 的段，其后缀必须在这张表里**。
_IMPLEMENTED_SUFFIX = {".sta": "station_sequence", ".jd": "alignment_pi",
                       ".pm": "alignment_element", ".prj": "design_project",
                       ".dmx": "profile_ground_point", ".zdm": "profile_grade_point",
                       ".sup": "superelev_transition", ".wid": "roadbed_width",
                       ".ctr": "design_control", ".tf": "earthwork_section",
                       ".lj": "roadbed_design_point",
                       # v0.5 K 节。★★ 这里填的是**段名** `cross_section`，不是表名
                       # `cross_section_ground_point` —— 我第一版就填成了表名，被下面那条
                       # 钉子当场抓住（"漏了 ['cross_section']"）。
                       # 对照 `.ctr`：值是 `design_control`（段），而它落的 9 张表叫
                       # slope_segment/ditch_segment/… —— 段名 ≠ 表名，这张表按**段**索引。
                       # 这已经是本会话第二次栽在同一个混淆上（第一次在 ER 图脚本里，
                       # 把段名写进了按表名索引的 WEIDI_SOURCE）。两次都是测试抓的。
                       ".hdm": "cross_section",
                       # v0.5 L 节。⚠ 后缀写 **.tsftxt 不是 .tsf** —— 适配器读的是
                       # tools/tsf2txt.py 摊出来的文本，不是那个 Access 二进制。
                       # 写成 .tsf 会让这张表声称"这个文件能导"，而实际导入前还得先转换。
                       ".tsftxt": "earthwork_factor"}

#: 台账要不要收「**磁盘上有、`.PRJ` 里没声明**」的文件（v0.5 迁移 ⑨②）。
#  值是 `file_kind_name` —— `.PRJ` 没给名字（它压根没提），这里给一个。
#
#  ★ 为什么需要这一类：`design_file` 的数据来源原来**只有一个** —— `.PRJ` 的
#    〔文件名〕段。于是它回答的是「`.PRJ` 里写了哪些文件」，**不是**「这个工程
#    有哪些文件」。实测两者不等价：磁盘 20 个文件，库里 16 行。
#    「这个工程有哪些文件？哪些我们还没处理？」这句话因此**答不出来**。
#
#  ⚠ 只收**明确登记过的**，不做"见到就收"。理由：`.cys`（涵洞系统参数）也
#    在磁盘上、也不在〔文件名〕段里，但它是**软件参数**不是工程数据，收了就把
#    台账弄脏了（见 `_SYSTEM_PARAM_SUFFIX`）。所以这张表必须一行一行加。
# ★ 台账的第二张显式登记表：**`.PRJ` 里声明了、但纬地没给键号**的文件。
#   与 `_LEDGER_EXTRA_SUFFIX` 是两回事 —— 那张表管的是"磁盘上有、`.PRJ` 压根没提"，
#   这张表管的是"`.PRJ` 提了、就排在〔文件名〕段里、唯独没有号"。
#   ⚠ 同样一行一行加，不做"没号就收"：没号的原因**各不相同**（见下面 _SYSTEM_PARAM_SUFFIX）。
_LEDGER_DECLARED_CODELESS = {
    # ★ 2026-09-22 用户定「甲 = 加」。依据（网上查证 + 实测）：
    #   ① 纬地官方技术支持原文：「每一个涵洞项目都有两个设计文件（hda 和 cys）组成，
    #      其中 **hda 文件用于保存涵洞设计参数**，cys 文件用于保存绘图参数」——
    #      **hda 是工程数据**。
    #   ② 「只需要打开新的路线项目，**在项目管理器中把原来的（hda 和 cys）文件路径
    #      添加进来即可**」—— 涵洞是**独立产品**(HintHD)，按路径手工挂进来，
    #      所以它在〔文件名〕段里**有名有路径、就是没有号**。**不是疏漏。**
    #   ③ 实测 4 个涵洞，桩号 820/2700/3700/4500 m 全在本路段(0~5805.421 m)内 ——
    #      确实是本工程的数据。
    #   ④ 与 .dtm 同类：都是"确实是工程文件、暂时解析不了"。
    #
    #   ⚠ 为什么是 `pending` 而不是 `blocked`：`blocked` = **结构上**读不了（二进制）；
    #     而 .hda 是**纯文本 GBK**，框架**能**解析 —— 实测 `BEGIN_CUL` + 桩号 + GUID，
    #     4 个涵洞的 10 个分节**完全一致**、节号是固定枚举
    #     （[基本参数]0 [涵身参数]1 [分段错台]2 [左帽石]3 [右帽石]4
    #       [左洞口]7 [右洞口]8 [计算参数]11 [涵洞附注]12 [钢筋参数]13；5/6/9/10 缺号）。
    #     解析不了的是**每一列数字的含义** —— 只有教程 §24.13.2 说得清，而那本
    #     72 页 PDF 公开网络取不到（试过道客巴巴/土木在线/百度报告，都没拿到正文）。
    #     → 所以：**登记 + 记下能确证的（桩号），但不建涵洞表**。
    #       猜列含义会往库里灌一批"看着有、其实含义错"的数据，比没有更糟。
    ".hda": ("pending", "涵洞设计参数文件（HintHD 涵洞系统的工程数据）"),
}

# 补一句「为什么是 pending」—— 没有它，台账上只看得出"还没做"，
# 看不出"**已经确认过能读、只是没写**"，下一个人会重新去查一遍。
_LEDGER_EXTRA_NOTE = {
    ".tsf": ("Microsoft Access (Jet 4) 数据库；**实测可读**（纯 Python 解出 20 张表，"
             "表名/列名全中文）。"
             "⚠ **但这个文件本身不能直接导入** —— M2 的适配器契约是 `parse(text)`、"
             "零第三方依赖，而读 Access 需要 `access-parser`。"
             "故先经 `tools/tsf2txt.py`（一次性工具，不进运行时）摊成 `.tsftxt` 文本，"
             "适配器读**那个**。**导入本文件之前必须先跑一遍转换器。**"
             "（所以这里仍是 pending 而不是 ok：磁盘上这个 .tsf 确实还导不进去。）"),
}

_LEDGER_EXTRA_SUFFIX = {
    ".prj": "总项目文件(*.PRJ)",
    # ★ 2026-09-22 用户定「甲 = 加」。三条理由（按硬度排）：
    #   ① **不自洽**：`.gtm`（数模**组**索引，115 号）已经进了台账，而 `.gtm`
    #      只是 `.DTM` 的目录 —— 记目录不记正主，说不通。
    #   ② **不可再生**：`.DTM` 是从「三维地形数据」（dwg/dxf/dgn/asc/pnt…）建出来的，
    #      而那份原始数据**不在本工程目录里**（20 个文件里没有）。`.gtm` 也不含点云
    #      （实测它只有边界框 + 路径）。**这份 .DTM 是唯一一份** —— 丢了就得回测绘。
    #   ③ **同类一致**：台账里已有 4 个 blocked（.dq/.gtm/.BDM/.HDMSJ），
    #      都是读不了但确实是工程文件。.DTM 归同一类，没理由单把它排除。
    #
    #   实测：33,460 B，魔数 44 54 4d 02 = DTM+版本 2，其后是双精度三维坐标
    #   （E 534293.6084 / N 2784587.1637 —— 六位东 + 七位北，标准高斯投影）。
    #   教程 §1.1.3 与 342 行：DTM「直接剖切纵、横断地面线，进而得到土方数量」——
    #   也就是说它是 `.DMX`/`.HDM` 的**上游**，而那两者**已经落库**了
    #   （profile_ground_point 332 行 / cross_section_ground_point 2215 行）：
    #   库里有结果，没有源头。
    #   ⚠ 库里那两张像模像样的表（scan3d_model / model_output）**都不是它** ——
    #     那是 M2 感知接入的（三维扫描仪 / 有限元），且都是 0 行。
    ".dtm": "数模文件(*.DTM)",
    # ★ 2026-09-22 用户定「甲 = 加」。它是这五个里**唯一一个"现在就能写适配器"**的：
    #   开放格式（Access Jet 4）、列名中文自解释、**不需要任何说明书**。
    #   ★ 而且它是唯一一个能**真正扩库**的 —— 逐表比对下来，它独有的有：
    #     · 土石系数（土方 1.23/1.16/1.09，石方 0.92）—— 库里没有
    #     · 取土坑 / 弃土坑 —— 库里没有
    #     · **调配过程表 / 过程（各 27 段）** —— 库里没有 ★★ 这是它的核心价值：
    #       「哪段土运到哪段、运多远」是**调配决策的结果**，
    #       而 `earthwork_section`（逐桩断面）里**只有数量、没有去向**。
    #       教程的分工也印证：数量来自 `.tf`，**调配**来自 HintTF。数量 ≠ 调配。
    #   ★ 它还**重复**了不少已在库里的东西（可当交叉验证素材）：
    #     竖曲线 12 行 —— 桩号与 `profile_grade_point` **逐位相同**（0/300/790/1980/
    #       2660/3080/3480/3715/4050/4430/4950/5805.421）；
    #     土石含量 1 行 —— 与 `.CTR` TFFD 落下的 `earthwork_composition` 一样；
    #     构造物 2 行 —— 与 `structure_control` 一样；项目/项目分段 —— 重复。
    #   ⚠ 一处**对不上**，值得查：逐桩断面 **335 行** vs `earthwork_section` **332 行**。
    #   ⚠ Tmp调配过程表（562 行）是中间结果，不进。
    ".tsf": "土石方调配文件(*.TSF)",
    # ⚠ .tsf（土石方调配 2,695,168 B，Microsoft Access 数据库）实测同样
    #   「磁盘上有、.PRJ 里没提」，**尚未决定** —— 定了就加进来，机制已经通了。
}

#: **存在、但结构上解不开**的后缀 → 为什么。parse_status 记 "blocked"。
#  逐个实测过文件头，不是猜的：
#    .gtm   魔数 `HB  HINT40_GROUP_DTM_VER6`，之后即二进制（数模组索引）
#    .bdm   第 1 行 `HB  HINTCAD5.84_HDMSJ_SHUJU`，**之后是 zlib 流**（实测偏移 16 起，
#           解出 53903 字节二进制结构；且流被截断，eof=False）
#    .hdmsj 第 1 行与 .bdm **逐字节相同**（同一个魔数），之后同样是二进制
#           —— 两个不同用途的文件共用一个魔数，光看魔数分不开，必须看后缀
#    .dtm   二进制数模（三维地形模型本体）
#    .tsf   文件头 `.Standard Jet DB` —— **Microsoft Access 数据库**，不是文本
#  → 这些**不是"适配器还没写"**，是"按现有手段读不了"。两者混为一谈会让人去写
#    一个永远写不出来的适配器。故单列一类，并在 parse_note 里写清实测依据。
#: **软件自身的参数**文件（不是工程数据）→ 为什么。这类文件即使出现在工程目录里，
#  也不该进 design_file 台账 —— 它们描述的是"软件怎么画图"，不是"这条路是什么"。
_SYSTEM_PARAM_SUFFIX = {
    ".cys": "软件系统参数（图层／标注样式／图框／填充图案，首行 HINTSOFT_HD_SYS_*）",
}

_BLOCKED_SUFFIX = {
    ".gtm":   "二进制数模组索引（魔数 HINT40_GROUP_DTM_VER6），非文本",
    ".bdm":   "魔数行后为 zlib 压缩的二进制结构，需专有工具",
    ".hdmsj": "与 .bdm 共用魔数，其后为二进制结构，需专有工具",
    ".dtm":   "二进制三维数模本体，非文本",
    # ⚠⚠ `.tsf` 原来在这里，理由是「Microsoft Access 数据库，**需 ODBC/Jet 引擎**」。
    #     **那条理由是错的**（2026-09-22 推翻）。实测：纯 Python 就能读出来 ——
    #     `uv run --with access-parser` 直接解出 **20 张表**，表名和列名**全是中文**
    #     （项目／项目分段／逐桩断面／竖曲线／土石系数／土石含量／取土坑／弃土坑／
    #      构造物／断链／调配过程表／过程／统计扩展／土方调配扩展记录／Tmp调配过程表…），
    #     数据也全对（构造物：桥 273~333 m、隧道 930~1800 m）。
    #     → 「需专有引擎」不成立，它不是**结构上**读不了，只是**适配器还没写**。
    #       故从 blocked 移到 pending（见 `_LEDGER_EXTRA_SUFFIX`）。
    #     ⚠ 这与 `.dq` 是**同一个缺陷形状、方向相反**：.dq 是我误判成"文本/pending"，
    #       .tsf 是我误判成"读不了/blocked"。两次都是**没去读就下了结论**。
    # ⚠ .dq 一度被我按"文本"归进 pending —— **错了**。只看前 32 字节只看到魔数行，
    #   第 2 行起就是**裸二进制**（不是 zlib、不是 base64）。实测：9 行里有 5 行
    #   正好 386 字节，且有重复片段 → **定长记录的结构化二进制**（加密不会有重复）。
    #   纬地官方在技术支持里也说明：道路系统读的是 **.dqd**，
    #   「挡墙设计系统完成挡墙设计后，**无需转为 dq 格式**」
    #   → .dq 是挡墙系统自己的内部格式，.dqd 才是交换格式。故归 blocked。
    ".dq":    "挡墙系统内部二进制格式（定长 386 字节记录）；交换格式是 .dqd，本工程未导出",
}


def _basename(rel_path: str | None) -> str | None:
    if not rel_path:
        return None
    # `.PRJ` 里是 Windows 风格 `.\毕设.pm` / `..\052201341…HDM`，两种分隔符都要切
    return rel_path.replace("\\", "/").rsplit("/", 1)[-1] or None


def plan_project(prj: Mapping[str, Any], *, project_dir: Any = None) -> dict[str, Any]:
    """`.PRJ` 解析结果 → 档案五表的待写行。**纯函数，不碰数据库。**

    ``project_dir`` 给了就去目录里找同名后缀的实际文件，写进 ``design_file.remark``
    —— 因为实测发现 **`.PRJ` 声明的文件名与磁盘上的并不一样**
    （声明 ``.\\毕设.pm``，磁盘是 ``052201341刘其立道路毕设平面线形文件.pm``）。
    导出时被改过名。这个差异必须记下来，否则以后按台账找文件会找不到。
    """
    pj = prj["project"]
    segs = prj["segments"]
    # 分段数 > 1 时路段名要带序号，否则两个路段同名、没法分辨
    multi = len(segs) > 1

    proj_row = {
        "project_uid": pj.get("project_uid"),
        "project_name": pj["project_name"],
        "project_type": pj.get("project_type"),
        "station_interval_m": pj.get("station_interval_m"),
        "earthwork_method": pj.get("earthwork_method"),
        "designer": pj.get("designer"),
        "design_org": pj.get("design_org"),
        "design_stage": pj.get("design_stage"),
        "source_file": prj.get("source_file"),
        "remark": _project_remark(prj),
    }

    # ★ `road_line.line_code` 是 NOT NULL UNIQUE，而 `.PRJ` **没有路线代码**。
    #   不用 UUID 前缀之类的假码，直接把项目名当线码，并在 remark 里写明这是代用 ——
    #   编一个看起来像代码的东西，比写清楚"这里没有代码"危险得多。
    seg0 = segs[0]
    line_row = {
        "line_code": pj["project_name"],
        "line_name": pj["project_name"],
        "admin_region": seg0.get("road_region"),
        "road_class": seg0.get("road_grade"),
        "design_speed": int(seg0["design_speed_kmh"]) if seg0.get("design_speed_kmh") else None,
        "design_load": None,                    # `.PRJ` 无此字段，不猜
        "lane_count": seg0.get("lane_count"),
        # ⚠ `lane_width_m` 是**单车道宽**，而 `.PRJ` 的 `307路幅宽度 = 10.000` 是
        #   路幅总宽 —— 不是一回事。所以这里**不填**，总宽进 section_design_attr。
        #   把 10.000 填进单车道宽是典型的静默错标：值本身没错，含义错了。
        "lane_width_m": None,
        "manage_org": None,
        "remark": (f"来源：纬地工程 {prj.get('source_file') or ''}"
                   f"｜项目ID {pj.get('project_uid') or '（缺）'}"
                   f"｜line_code 以项目名代（.PRJ 无路线代码）"),
    }

    sections, attrs = [], []
    for seg in segs:
        name = (f"{pj['project_name']} 分段{seg['seq']}" if multi else pj["project_name"])
        sections.append({
            "section_name": name,
            "start_station_text": station_text(seg["start_station_m"]),
            "end_station_text": station_text(seg["end_station_m"]),
            "start_station_km": round(seg["start_station_m"] / 1000.0, 6),
            "end_station_km": round(seg["end_station_m"] / 1000.0, 6),
            "length_m": round(seg["length_m"], 1),
            "direction": None,
            "pavement_type": None,              # `.PRJ` 无路面类型，不猜
            "climate_zone": None,
            "_seg_seq": seg["seq"],
            "remark": f"来源 .PRJ 分段 {seg['seq']}",
        })
        attrs.append({
            "_seg_seq": seg["seq"],
            "road_grade": seg.get("road_grade"),
            "design_speed_kmh": (int(seg["design_speed_kmh"])
                                 if seg.get("design_speed_kmh") else None),
            "cross_section_form": seg.get("cross_section_form"),
            "roadway_width_m": seg.get("roadway_width_m"),
            "carriageway_crossfall_pct": seg.get("carriageway_crossfall_pct"),
            "shoulder_crossfall_pct": seg.get("shoulder_crossfall_pct"),
            "median_width_m": seg.get("median_width_m"),
            "max_superelev_pct": seg.get("max_superelev_pct"),
            "superelev_rotate_mode": seg.get("superelev_rotate_mode"),
            "superelev_gradient_mode": seg.get("superelev_gradient_mode"),
            "widening_mode": seg.get("widening_mode"),
            "widening_gradient_mode": seg.get("widening_gradient_mode"),
            "source_file": prj.get("source_file"),
        })

    files, skipped = _plan_design_files(prj, project_dir=project_dir)
    return {
        "tables": {"design_project": [proj_row], "road_line": [line_row],
                   "road_section": sections, "section_design_attr": attrs,
                   "design_file": files,
                                      # ★ v0.5 L 节：土石方压实系数（.tsftxt）。
                                      #   为什么在**工程级**这一步、而不是 plan()/load() 那一步：
                                      #   它锚的是 `design_project_id`（全工程一组系数，没有桩号），
                                      #   而 plan()/load() 是**按路段**导入的（签名里只有 section_id）。
                                      #   硬塞进按路段那一步会错位 —— 同一工程导 3 个路段就会写 3 次。
                                      #   与 design_file 同构：出行时不带 design_project_id，
                                      #   由 ensure_project 在事务里用 pid 补上。
                                      "earthwork_factor": _plan_earthwork_factor(project_dir)},
        "skipped_files": skipped,
    }


def _plan_design_files(prj: Mapping[str, Any],
                       *, project_dir: Any = None) -> tuple[list[dict], list[str]]:
    """``[文件名]`` → ``design_file`` 行。返回值第二项是被跳过的（附原因）。"""
    on_disk: dict[str, str] = {}
    scanned = False
    if project_dir is not None:
        d = pathlib.Path(project_dir)
        if d.is_dir():
            scanned = True
            for p in d.iterdir():
                if p.is_file():
                    on_disk.setdefault(p.suffix.lower(), p.name)

    rows, skipped = [], []
    for f in prj.get("files") or []:
        code, rel = f.get("kind_code"), f.get("rel_path")
        if not rel:
            continue                        # 工程声明了槽位但没用（实测 30 条里有 12 条）
        if not code:
            # `design_file.file_kind_code` 是 NOT NULL，而实测 .PRJ 里
            # 「涵洞数据文件(*.hda)」「涵洞系统参数文件(*.cys)」两行**没有键号**。
            # 编一个码就是造假，故跳过并上报。
            #
            # ★ 但这两行的**原因不同**，不能合成一句（2026-09 查清）：
            #   · .cys —— 首行 `HINTSOFT_HD_**SYS**_1026`，内容是尺寸标注样式(ZDIMAPP)、
            #     图框([TITLE] 1:[SCALE])、填充图案(ANSI31)…… **软件的系统参数**，
            #     不是工程数据。纬地自己的说明也写：「参数设置文件：即安装目录下
            #     '系统设置'文件夹中的'系统参数.cys'，记录着图层等控制参数数据」。
            #     → 它**本就不该进工程台账**，与字段号无关。
            #   · .hda —— 首行 `HINTSOFT_HD_**PRJ**_1045`、有 `BEGIN_CUL`(涵洞)、
            #     桩号 + GUID、`[涵洞…]` 组名、`2009 100 1 0 5232.274…` 字段号+值
            #     —— 与 .PRJ **同族**，是**工程数据**。
            #     ⚠⚠ 这里原来写的是「这是 .PRJ 的疏漏，**将来给了号就该进来**」——
            #        **那句话是错的**（2026-09-22 更正）。给了号它也进不来：
            #        它缺的不是号，是**适配器**；而适配器做不出来的原因是
            #        **字段含义没有文档** —— 实测该文件是 10 个块、每块几十个纯数字
            #        字段（`[基本参数] 0 17 0` / `[涵身参数] 1 2 1` / … ），
            #        没有任何自描述。含义写在《纬地涵洞设计系统教程》**§24.13.2**
            #        （纬地官方技术支持原文：「"涵洞数据文件(.hda)" 的格式说明详见
            #        24.13.2 节」），而那是随商业软件发行的文档，公开网络上取不到。
            #        → 真正的原因：**能读、是工程数据、但字段语义无文档**。
            #          猜字段含义比不做更糟（会往库里灌一批看着有、其实含义错的数据）。
            #        ⚠ 这与 `_BLOCKED_SUFFIX`（二进制/专有格式，**读不了**）是
            #          **两种不同的原因**，不能合并 —— 诊断要说真正的那一个。
            _suffix = ("." + f["kind_name"].rsplit(".", 1)[-1].rstrip(")").lower()
                       if "." in f["kind_name"] else "")
            if _suffix in _SYSTEM_PARAM_SUFFIX:
                skipped.append(
                    f"{f['kind_name']}（{_SYSTEM_PARAM_SUFFIX[_suffix]}—— "
                    "本就不属于工程台账，与字段号无关）")
            elif _suffix in _LEDGER_DECLARED_CODELESS:
                # ★ 2026-09-22：这类**进台账**（file_kind_code=NULL）。原来一律跳过，
                #   依据是"NOT NULL 满足不了"—— 那条约束已在迁移 ⑨② 去掉，
                #   所以理由不再成立。见 _LEDGER_DECLARED_CODELESS 上的完整依据。
                _st, _nm = _LEDGER_DECLARED_CODELESS[_suffix]
                rows.append({
                    "file_kind_code": None,   # ★ NULL = 纬地自己没给码
                    "file_kind_name": f["kind_name"],
                    "file_name": _basename(rel) or rel,
                    "rel_path": rel,
                    "coverage_from_station_km": None,
                    "coverage_to_station_km": None,
                    "parse_status": _st,
                    "parse_note": _nm,
                    "remark": ("`.PRJ`〔文件名〕段里**声明了**它（有名字有路径），"
                               "但**没有键号** —— 它是纬地**涵洞系统**(HintHD) 的"
                               "工程文件，按路径手工挂进项目管理器，故无号"),
                })
                continue
            else:
                skipped.append(f"{f['kind_name']}（.PRJ 未给字段号，且未登记进台账）")
            continue
        declared = _basename(rel)
        suffix = ("." + declared.rsplit(".", 1)[-1].lower()) if declared and "." in declared else ""
        actual = on_disk.get(suffix)
        note = None
        if suffix in _IMPLEMENTED_SUFFIX:
            status, note = "ok", f"适配器已实现（{_IMPLEMENTED_SUFFIX[suffix]}）"
        elif suffix in _BLOCKED_SUFFIX:
            # ★ 与 pending 分开：这不是"还没写适配器"，是"按现有手段读不了"。
            status, note = "blocked", f"结构上不可解析：{_BLOCKED_SUFFIX[suffix]}"
        else:
            status, note = "pending", "适配器尚未实现该段解析"
        if scanned and actual is None:
            # ★ 只有**确实去看过**（给了 project_dir 且目录存在）才能说 absent。
            #   「没去看」和「看了没有」是两件事 —— 前者报 absent 就是撒谎。
            #   四态含义各不同，混成一句会让人去写一个永远写不出来的适配器：
            #     ok      = 适配器已实现
            #     blocked = 存在，但结构上读不了（二进制／需专有工具）
            #     pending = 有源、可解析，只是适配器还没写
            #     absent  = **去看过了，源里根本没有这个文件**
            status = "absent"
            # 说明**整个换掉**，不能把上面那句「适配器尚未实现」接在后面 ——
            # 那样会自相矛盾（文件都不在，还谈什么适配器）。
            note = "目录内未找到该后缀的文件（工程声明了槽位，导出时未带上）"
        elif actual is not None and actual != declared:
            note = (note or "") + f"｜实际磁盘文件名 {actual}（工程声明的是 {declared}，导出时改过名）"
        rows.append({
            "file_kind_code": code,
            "file_kind_name": f["kind_name"],
            "file_name": declared or rel,
            "rel_path": rel,
            "coverage_from_station_km": None,   # 单文件覆盖范围需逐文件解析，此处不猜
            "coverage_to_station_km": None,
            "parse_status": status,
            "parse_note": note,
            "remark": None,
        })

    # ★ 台账 = **`.PRJ` 声明的 ∪ 磁盘上实际存在的**（v0.5 迁移 ⑨②）。
    #   后半句是补的。上面那个循环只看 `.PRJ`，于是"磁盘上有、`.PRJ` 没提"的文件
    #   在库里一个字都没有 —— 实测本工程有 3 个（.dtm/.tsf/.prj）。
    #
    #   ⚠ 只有**确实去看过**（`scanned`）才能补：没扫描目录时磁盘上有什么是未知的，
    #     此时补行就是编（和 `absent` 那条同一个道理：「没去看」≠「看了没有」）。
    if scanned:
        declared = {r["file_kind_code"] and (r["file_name"].rsplit(".", 1)[-1].lower()
                                             if "." in r["file_name"] else "")
                    for r in rows}
        declared = {d for d in declared if d}
        for suffix, kind_name in sorted(_LEDGER_EXTRA_SUFFIX.items()):
            if suffix.lstrip(".") in declared:
                continue                      # `.PRJ` 已经声明过了，上面那行就是它
            found = sorted(p.name for p in pathlib.Path(project_dir).iterdir()
                           if p.is_file() and p.suffix.lower() == suffix)
            for name in found:
                note = ("适配器已实现（%s）" % _IMPLEMENTED_SUFFIX[suffix]
                        if suffix in _IMPLEMENTED_SUFFIX
                        else _LEDGER_EXTRA_NOTE.get(suffix))
                rows.append({
                    "file_kind_code": None,   # ★ NULL = 纬地自己没给码（它没声明这个文件）
                    "file_kind_name": kind_name,
                    "file_name": name,
                    "rel_path": None,
                    "coverage_from_station_km": None,
                    "coverage_to_station_km": None,
                    "parse_status": ("ok" if suffix in _IMPLEMENTED_SUFFIX
                                     else "blocked" if suffix in _BLOCKED_SUFFIX
                                     else "pending"),
                    "parse_note": note,
                    "remark": ("磁盘上有、.PRJ〔文件名〕段里**没有声明**这个文件"
                               "（不是漏读：`.PRJ` 逐行看过，确实没有它）"),
                })
    return rows, skipped


def _plan_earthwork_factor(project_dir: Any) -> list[dict]:
    """扫 ``project_dir`` 找 `.tsftxt`（`.tsf` 的转换文本）→ ``earthwork_factor`` 行。

    ⚠ **找不到就返回空列表，不报错** —— 大多数工程没有 .tsf，缺它是**正常**的。
    这与"有文件却解析失败"是两回事：后者会抛 :class:`SourceInvalid` 冒到调用方，
    **不会被这里吞掉**。

    ⚠ 行里**不含** ``design_project_id`` —— 由 :func:`ensure_project` 在事务里补。
    """
    if not project_dir:
        return []
    d = pathlib.Path(project_dir)
    if not d.is_dir():
        return []
    hits = sorted(p for p in d.iterdir()
                  if p.is_file() and p.suffix.lower() == ".tsftxt")
    if not hits:
        return []
    # 与 build_ir 同一条规矩：同一段出现多个文件是**异常**（通常是把两套工程混了）。
    # 选第一个是确定性的，但**确定性不等于正确** —— 所以要说出来，不能默默取。
    if len(hits) > 1:
        raise SourceInvalid(
            "目录里有 %d 个 .tsftxt 文件：%s —— 一套工程每段只有一个，"
            "出现多个通常是把两套工程的文件混在了一起，请先清理。"
            % (len(hits), "、".join(h.name for h in hits)))
    text, _enc = base.read_text_any(hits[0])
    if not tsf_mod.detect(text):
        raise SourceInvalid(f"魔数不匹配，可能不是 .tsftxt：{hits[0].name}",
                            file=hits[0].name)
    out = tsf_mod.parse(text, file=hits[0].name)
    rows = out[tsf_mod.PAYLOAD_KEY]
    # 一个工程一组系数 —— 源里有多行就是源的问题，取第一行会**静默丢数据**。
    if len(rows) != 1:
        raise SourceInvalid(
            f"表「{tsf_mod.TABLE}」应有 1 行（全工程一组系数），实为 {len(rows)} 行 —— "
            f"本表 UNIQUE(design_project_id)，多行必然冲突，故在这里就说清楚。",
            file=hits[0].name)
    return [dict(rows[0], remark=f"来源：{hits[0].name}（纬地 HintTF .tsf 转换文本）")]
def _project_remark(prj: Mapping[str, Any]) -> str:
    bits = []
    if prj.get("save_time"):
        bits.append(f"工程存盘 {prj['save_time']}")
    ib = prj.get("ignored_blobs") or {}
    if ib:
        bits.append("未解析的二进制块 " + "／".join(f"{k}×{v}" for k, v in sorted(ib.items())))
    um = prj.get("unmapped") or {}
    if um:
        bits.append(f"未映射字段 {len(um)} 个（图表字体/路堤宽度等，无对应列）")
    return "｜".join(bits)[:2000] or None


def ensure_project(prj: Mapping[str, Any], dao: Any, *,
                   project_dir: Any = None, writer: str = "M2",
                   dry_run: bool = False) -> dict[str, Any]:
    """落档案与路段，返回 ids。**幂等**：按 ``project_uid`` / ``line_code`` 复用已有行。

    这一步必须在几何之前跑 —— 几何表用 FK 锚在 ``road_section.id`` 上。
    """
    planned = plan_project(prj, project_dir=project_dir)
    t = planned["tables"]
    report: dict[str, Any] = {
        "planned": {k: len(v) for k, v in t.items()},
        "skipped_files": planned["skipped_files"],
        "project_id": None, "line_id": None, "section_ids": [], "dry_run": dry_run,
    }
    if dry_run:
        return report

    # ★ 事务**之前**先预读已有路段。
    #
    # 为什么不在事务里查：`road_section` **没有任何 UNIQUE 约束**可用作冲突键
    # （`design_project_id` 与 `section_name` 都是普通列），所以 `on_conflict`
    # 这条路走不通，只能"先查后插"。而 `TxnWriter` 只有写接口（没有公开的读），
    # 混用 `dao.query*` 又是**另一条连接**、读不到本事务未提交的行 ——
    # 真放到事务里查，第一次调用查不到（对），重放也查不到（**错**，于是插重复路段，
    # 而 `insert_returning` 会返回那个新 id，下一轮几何就挂到重复路段上）。

    # 挪到事务外、靠 `project_uid` 先定位项目，两种情况就都对了。
    #
    # 残留竞态：两个进程同时首次导入同一项目，可能各插一条同名路段。
    # 导入是单进程批处理，此处不处理；要严格就得给 road_section 加
    # UNIQUE (design_project_id, section_name) —— 那是契约变更（②表结构）。
    uid = t["design_project"][0].get("project_uid")
    existing_sections: dict[str, Any] = {}
    if uid:
        pre = dao.query_one("SELECT id FROM design_project WHERE project_uid = %(u)s",
                            {"u": uid})
        if pre:
            for r in dao.query("SELECT id, section_name FROM road_section "
                               "WHERE design_project_id = %(p)s", {"p": pre["id"]}):
                existing_sections[r["section_name"]] = r["id"]

    with dao.write_txn(writer=writer) as tx:
        # 四处都用 on_conflict 走幂等 —— 包括 road_line：它的 line_code 本身就是
        # UNIQUE，天然是业务键。
        # ⚠ 绝对不要在 `tx` 里改用 `dao.execute_write(...)` 去补写同一行：
        #   那是**另一条连接上的另一个事务**，会撞在本事务未提交的行锁上直接挂住。
        #   同一个逻辑写操作必须始终留在同一个 tx 里。
        pid = tx.insert_returning("design_project", t["design_project"][0],
                                  on_conflict=("project_uid",))
        report["project_id"] = pid

        line_row = dict(t["road_line"][0])
        line_row["line_code"] = line_row["line_code"] or f"PRJ-{pid}"
        lid = tx.insert_returning("road_line", line_row, on_conflict=("line_code",))
        report["line_id"] = lid

        for sec, attr in zip(t["road_section"], t["section_design_attr"]):
            sec_row = {k: v for k, v in sec.items() if not k.startswith("_")}
            attr_row = {k: v for k, v in attr.items() if not k.startswith("_")}
            sec_row["line_id"] = lid
            sec_row["design_project_id"] = pid
            sid = existing_sections.get(sec_row["section_name"])
            if sid is None:
                sid = tx.insert_returning("road_section", sec_row)
            report["section_ids"].append({"seq": sec["_seg_seq"], "section_id": sid})
            attr_row["section_id"] = sid
            tx.insert("section_design_attr", [attr_row], on_conflict=("section_id",))

        for row in t.get("earthwork_factor", []):
            # 冲突键就是 UNIQUE(design_project_id)：一个工程一组系数，重导覆盖。
            tx.insert("earthwork_factor", [dict(row, design_project_id=pid)],
                      on_conflict=("design_project_id",))
        
        for row in t["design_file"]:
            # ★ 冲突键含 file_name（v0.5 迁移 ⑨②）：没码的行（file_kind_code IS NULL）
            #   靠**文件名**做身份 —— 否则重导时 ON CONFLICT 对 NULL 行永不触发，会插重复。
            #   ⚠ 列序必须与 DDL 的 UNIQUE 完全一致，否则 PG 报
            #     "no unique or exclusion constraint matching the ON CONFLICT specification"。
            tx.insert("design_file", [dict(row, design_project_id=pid)],
                      on_conflict=("design_project_id", "file_kind_code", "file_name"))

    return report


__all__ = ["LOADABLE_TABLES", "ARCHIVE_TABLES", "LoadError", "plan", "verify", "load",
           "plan_project", "ensure_project",
           "station_text", "is_integer_station", "station_type"]

