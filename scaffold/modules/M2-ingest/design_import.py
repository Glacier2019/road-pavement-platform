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
LOADABLE_TABLES = ("station_sequence", "alignment_pi", "alignment_element")

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
def _plan_stations(ir: Mapping[str, Any], section_id: int) -> list[dict[str, Any]]:
    pts = ir["segments"].get("station_sequence") or []
    if not pts:
        return []
    first, last = pts[0]["station_m"], pts[-1]["station_m"]
    return [{
        "section_id": section_id,
        "station_seq_no": p["seq_no"],
        "station_local_km": round(p["station_m"] / 1000.0, 6),
        "station_absolute_km": None,        # 需路网基准，IR 里没有（见 station_text 注释）
        "station_text": station_text(p["station_m"]),
        "station_type": station_type(p["station_m"], first=first, last=last),
        "is_integer_station": is_integer_station(p["station_m"]),
    } for p in pts]


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


def plan(ir: Mapping[str, Any], *, section_id: int) -> dict[str, Any]:
    """IR → 待写行。**纯函数，不碰数据库**（故可离线测）。

    返回 ``{"tables": {表名: [行…]}, "pi_source": "derived"|"file"|None}``。
    """
    pi_derived = _plan_pi_from_elements(ir, section_id)
    pi_file = _plan_pi_from_file(ir, section_id)
    pi_rows, pi_source = (pi_derived, "derived") if pi_derived else \
                         ((pi_file, "file") if pi_file else ([], None))
    tables = {
        "station_sequence": _plan_stations(ir, section_id),
        "alignment_pi": pi_rows,
        "alignment_element": _plan_elements(ir, section_id),
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
         writer: str = "M2",
         dry_run: bool = False,
         strict: bool = True) -> dict[str, Any]:
    """把 IR 落进 GE 表。**多表同事务**：要么全成，要么全不成。

    * ``dry_run=True``：只做 plan + verify，**一行都不写**（预检页用这个）。
    * ``strict=True``：``verify`` 出 ``errors`` 就抛 :class:`LoadError`；否则降级为警告。

    写权由 ``WriteDao`` 守卫逐表核对（``catalog.TABLE_OWNER``），本模块无从绕过。
    """
    planned = plan(ir, section_id=section_id)
    verdict = verify(ir, planned)
    if verdict["errors"] and strict:
        raise LoadError("落库前检查未通过：\n  - " + "\n  - ".join(verdict["errors"]))

    tables = planned["tables"]
    counts = {t: len(rows) for t, rows in tables.items()}
    report: dict[str, Any] = {
        "batch_no": batch_no,
        "section_id": section_id,
        "geometry_level": ir.get("geometry_level"),
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
        # ① 桩号序列（一等实体）：其余表都以它/路段为锚
        for table in ("station_sequence",):
            if tables[table]:
                report["written"][table] = tx.insert(
                    table, tables[table], on_conflict=("section_id", "station_local_km"))

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

        # ④ 批次登记
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


__all__ = ["LOADABLE_TABLES", "LoadError", "plan", "verify", "load",
           "station_text", "is_integer_station", "station_type"]
