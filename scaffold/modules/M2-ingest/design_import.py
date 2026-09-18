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

from typing import Any, Mapping, Sequence

from adapters import geom

# 落库器只写这几张表。白名单是刻意的：**新增映射必须在这里显式登记**，
# 免得一个 IR 段的增删悄悄改变写入范围。
LOADABLE_TABLES = ("station_sequence", "alignment_pi", "alignment_element",
                   "profile_grade_point", "profile_ground_point",
                   "superelev_transition")

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
            #   交点桩号 = ZH + 切线长，是**派生量**，故 DDL 里没有它的列；
            #   但验算要用，所以在这里带上。
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
    }
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

        # ⑥ 超高过渡变化点：锚 section_id，与其余 GE 表相同。
        #    六个横坡的 None 是**源文件 9999「忽略此数据」**，原样写 NULL，不填 0
        #    （见 _plan_superelev_transitions 的说明：填 0 会把"不约束"变成
        #    "约束为平坡"，下游会算出一条源文件里没有的过渡曲线）。
        if tables["superelev_transition"]:
            report["written"]["superelev_transition"] = tx.insert(
                "superelev_transition", tables["superelev_transition"],
                on_conflict=("section_id", "transition_seq"))

        # ⑦ 批次登记
        tx.insert("data_import_batch", [batch], on_conflict=("batch_no",))

    return report


def _default_desc(ir: Mapping[str, Any]) -> str:
    files = [f.get("file_name") or "" for f in (ir.get("source") or {}).get("files", [])]
    return "＋".join(f for f in files if f) or "design-import"


def _batch_remark(ir: Mapping[str, Any], planned: Mapping[str, Any],
                  verdict: Mapping[str, Any]) -> str:
    """批次的备注：几何等级 / 交点来源 / 缺口与告警。

    ⚠ 几何等级与缺口**没有独立列可放** —— ``data_import_batch`` 是采集批次表，
    不含这几个字段。放在 remark 里意味着**不可查询**（只能看全文）。
    若以后要按等级挑批次，需要给该表加工单加列；此处不擅自扩表。
    """
    gaps = ir.get("gaps") or []
    parts = [f"几何等级 {ir.get('geometry_level')}",
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
                  "section_design_attr", "design_file")

#: 已实现适配器的后缀 → 该文件可解析。用于 design_file.parse_status。
_IMPLEMENTED_SUFFIX = {".sta": "station_sequence", ".jd": "alignment_pi",
                       ".pm": "alignment_element", ".prj": "design_project",
                       ".dmx": "profile_ground_point", ".zdm": "profile_grade_point",
                       ".sup": "superelev_transition"}


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
                   "design_file": files},
        "skipped_files": skipped,
    }


def _plan_design_files(prj: Mapping[str, Any],
                       *, project_dir: Any = None) -> tuple[list[dict], list[str]]:
    """``[文件名]`` → ``design_file`` 行。返回值第二项是被跳过的（附原因）。"""
    on_disk: dict[str, str] = {}
    if project_dir is not None:
        import pathlib
        d = pathlib.Path(project_dir)
        if d.is_dir():
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
            skipped.append(f"{f['kind_name']}（.PRJ 未给字段号，无法满足 NOT NULL）")
            continue
        declared = _basename(rel)
        suffix = ("." + declared.rsplit(".", 1)[-1].lower()) if declared and "." in declared else ""
        actual = on_disk.get(suffix)
        note = None
        if suffix in _IMPLEMENTED_SUFFIX:
            status, note = "ok", f"适配器已实现（{_IMPLEMENTED_SUFFIX[suffix]}）"
        else:
            status, note = "pending", "适配器尚未实现该段解析"
        if actual is None:
            note = (note or "") + "｜目录内未找到该后缀的文件"
        elif actual != declared:
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
    return rows, skipped


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

        for row in t["design_file"]:
            tx.insert("design_file", [dict(row, design_project_id=pid)],
                      on_conflict=("design_project_id", "file_kind_code"))

    return report


__all__ = ["LOADABLE_TABLES", "ARCHIVE_TABLES", "LoadError", "plan", "verify", "load",
           "plan_project", "ensure_project",
           "station_text", "is_integer_station", "station_type"]

