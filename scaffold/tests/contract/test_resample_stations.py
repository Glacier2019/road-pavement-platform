#!/usr/bin/env python3
"""契约② FR-014：由要素**重采样**出桩号 —— 规则与源文件逐点对齐。

跑法（在 scaffold/ 下）：
    uv run --quiet --with "psycopg[binary,pool]==3.2.3" \\
        python tests/contract/test_resample_stations.py

═══════════════════════════════════════════════════════════════════════════
这条测试守的是什么
═══════════════════════════════════════════════════════════════════════════

FR-014 的原话：系统 MUST 由要素**重采样**出 `station_interval_m` 下的采样点；
采样密度 MUST 可配置，MUST NOT 把 20 m / 332 点烧死进数据。

改前：`station_interval_m` 被导入、被持久化、被一个测试读回来断言 `== 20.0`
—— 然后**没有任何代码用它生成任何东西**。典型「有份值，没人用」。

本组钉住三件事：

  ① ★ **重建能力**：按 20 m 重采样，MUST 重新得到设计文件 `.STA` 里那 332 个桩号。
     这是**唯一的硬回归** —— 它证明「由要素算」和「源文件给的」是同一件事，
     而不是两套各说各话的数字。

  ② ★ **特征点与间隔无关**：线元起终点（34）和交点曲中点（8）**不随间隔变**。
     这条最容易被天真实现踩掉：把整张表按 5 m 重新生成，曲线起终点就没了。

  ③ **拒错不静默**：间隔 ≤ 0、结果超 limit，都 MUST 报错。

═══════════════════════════════════════════════════════════════════════════
规则是从哪来的（不是推演，是反推 + 逐点核对）
═══════════════════════════════════════════════════════════════════════════

实测 `.STA`（桩号序列文件，332 行）给出的桩号集

    == 20 m 整桩 (291) ∪ 线元起终点 (34) ∪ 交点曲中点 (8)

332 对 332，**331 个精确相等**，剩 1 个差 **1 mm**（PI7 的曲中点：
重算 `JD − (2T−L)/2` 用库里存的 `tangent_len_m` / `curve_len_m`，
比 HintCAD 内部算的少几位）。所以下面的 `TOL_MM` 是 1 mm，不是 0。

曲中点公式：`QZ = JD − (2T − L) / 2`，其中 `2T − L` 是**切曲差** q。
实测 8 个交点的 (加桩 − JD) 与 q 之比**逐条都是 2.000**，
这才敢断定那 8 个加桩是曲中点 —— 量级吻合不足为凭，比例吻合才是。
"""

from __future__ import annotations

import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve()
ROOT = _HERE.parents[2]
sys.path.insert(0, str(_HERE.parent))
# ★ 运行器只 export 了 `modules/_shared`（见 run_contract_tests.sh L35），
#   `rpdao` 不在那里面。本仓的约定是**测试文件自己**把 M3 插进 sys.path ——
#   与 test_station_baseline.py L31 同一写法。
if str(ROOT / "modules" / "M3-rpdao") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules" / "M3-rpdao"))

from _env import dsn as _env_dsn  # noqa: E402

PASS = 0
FAIL = 0

#: 毕设工程所在路段（`road_section` id=6，K0+000 → K5+805.421）。
SECTION_ID = 6

#: 设计文件的桩号间隔（`design_project.station_interval_m`）。
DESIGN_INTERVAL_M = 20.0

#: 允许的差（km）。1 mm —— 见模块 docstring 里 PI7 曲中点那段。
TOL_KM = 1e-6

#: 采样点的三个类别。**前两个是特征点，MUST 与间隔无关。**
KINDS = ("grid", "element_bound", "curve_mid")
FEATURE_KINDS = ("element_bound", "curve_mid")


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {name}")
    else:
        FAIL += 1
        print(f"  [!!] {name}")
        if detail:
            for line in str(detail).splitlines():
                print(f"       {line}")


# ══════════════════════════════════════════════════════════════════════════
# 纯函数：本组**全部**判据都在这里。main() 只负责取数、喂进来、打印。
# ★ 这样 §7 才能喂假数据 —— 元测试不必连库。
# ══════════════════════════════════════════════════════════════════════════
def audit(rows: list[dict], interval_m: float,
          sec_start: float, sec_end: float) -> list[str]:
    """检查一批重采样结果，返回问题清单（空 = 通过）。

    `rows` 每项形如 `{"station_km": <float>, "kind": <str>}`。
    """
    problems: list[str] = []
    if not rows:
        return ["重采样返回空 —— 路段范围内一个点都没有"]

    vals = [round(float(r["station_km"]), 9) for r in rows]
    kinds = [str(r["kind"]) for r in rows]
    lo, hi = round(sec_start, 9), round(sec_end, 9)
    step = interval_m / 1000.0

    # ---- ① 类别必须都在白名单内，不许冒出没见过的 kind --------------------
    unknown = sorted(set(kinds) - set(KINDS))
    if unknown:
        problems.append(
            f"出现了未知的 kind：{unknown}。`kind` 只有 {list(KINDS)} 三种 ——"
            "多出来的会被下游按未知类别处理，多半是静默丢弃")

    # ---- ② ★ 特征点必须存在（这是「不许把曲线起终点丢掉」的判据）---------
    for k in FEATURE_KINDS:
        if k not in kinds:
            problems.append(
                f"缺少特征点类别 {k!r} —— 本次结果的类别是 {sorted(set(kinds))}。"
                "特征点是不随间隔变的，缺了它说明整张表被按网格重生成过，"
                "曲线起终点的评价会失真")

    # ---- ③ 去重：同一个桩号不许出现两次 -----------------------------------
    if len(vals) != len(set(vals)):
        dup = sorted({v for v in vals if vals.count(v) > 1})
        problems.append(f"桩号有重复：{dup[:8]}（共 {len(vals) - len(set(vals))} 个）")

    # ---- ④ 排序：MUST 递增 -----------------------------------------------
    if vals != sorted(vals):
        problems.append("桩号不是递增的 —— 下游按序切段会切错")

    # ---- ⑤ ★ 网格部分是等间隔的 -----------------------------------------
    grid = sorted(round(float(r["station_km"]), 9)
                  for r in rows if str(r["kind"]) == "grid")
    if len(grid) >= 2:
        bad = [(grid[i], grid[i + 1] - grid[i])
               for i in range(len(grid) - 1)
               if abs((grid[i + 1] - grid[i]) - step) > 1e-9]
        if bad:
            problems.append(
                f"网格部分不等间隔（应为 {step} km）：{bad[:4]}"
                f"（共 {len(bad)} 处）")

    # ---- ⑧ ★ 网格 MUST **铺满**整个路段，不许只覆盖开头一段 ----------------
    #   ★★ 这一条是**红验证补出来的**，不是一开始想到的。
    #     我注入的变异是「把网格点**数**写死按 20 m 算」（步长仍用 interval）。
    #     它**没被抓住** —— 因为「5 m 档」会变成
    #     「5 m 步长，但只铺到 1.45 km」，等间隔 ✓、端点 ✓、特征点 ✓，全部照过。
    #     判据缺了「铺满」这一条。
    #   ⇒ 教训：等间隔只约束**相邻两点的距离**，对**覆盖到哪里**一个字都没说。
    #     一个「从头开始、越来越短」的网格完全满足等间隔。
    if grid and grid[-1] + step < hi - 1e-9:
        problems.append(
            f"网格没有铺满路段：最后一个网格点 {grid[-1]}，路段终点 {hi}，"
            f"间隔 {step} km —— 差得超过一个间隔，说明网格只生成了开头一段。"
            "（等间隔只约束相邻距离，对覆盖范围不置一词）")

    # ---- ⑥ ★ 路段端点必须进来 --------------------------------------------
    if abs(vals[0] - lo) > TOL_KM:
        problems.append(
            f"缺路段起点：最小桩号 {vals[0]}，路段起点 {lo}")
    if abs(vals[-1] - hi) > TOL_KM:
        problems.append(
            f"缺路段终点：最大桩号 {vals[-1]}，路段终点 {hi}。"
            "★ 路段终点通常**不是整桩**（毕设是 5805.421，20 m 网格只到 5800），"
            "所以它必须靠「线元终点」进来 —— 只留网格就会把它丢掉")

    # ---- ⑦ 不许越界 -------------------------------------------------------
    out = [v for v in vals if v < lo - TOL_KM or v > hi + TOL_KM]
    if out:
        problems.append(f"有点落在路段范围之外：{out[:6]}（共 {len(out)} 个）")

    return problems


def _dsn() -> str | None:
    """真库 DSN：优先环境变量，否则读 scaffold/.env。

    ★ 为什么要读 `.env` 而不是「没 PG_DSN 就跳过」：本组**全部**断言都打真库，
    跳过等于它从未运行过 —— 而它照样报「通过」。这正是设计套件一直在防的
    那类**假绿**（同 test_station_baseline / test_design_import 的约定）。

    ⚠️ 必须处理**行内注释**：本仓 `.env` 写的是
    `PG_PORT=55432          # 本机 5432 已被其它服务占用`，
    天真的 `split("=")` 会把注释一起吞进值里。
    """
    if os.environ.get("PG_DSN"):
        return os.environ["PG_DSN"]
    envf = ROOT / ".env"
    if not envf.exists():
        return None
    vals: dict[str, str] = {}
    for line in envf.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        vals[k.strip()] = v.split("#")[0].strip()
    need = ("PG_USER", "PG_PASSWORD", "PG_PORT", "PG_DB")
    if not all(vals.get(k) for k in need):
        return None
    return (f"postgresql://{vals['PG_USER']}:{vals['PG_PASSWORD']}"
            f"@127.0.0.1:{vals['PG_PORT']}/{vals['PG_DB']}")


def main() -> int:
    print("契约② FR-014：由要素重采样出桩号")
    print("=" * 72)

    envf = ROOT / ".env"
    dsn = _dsn()
    if not dsn:
        if envf.exists():
            # ★ 有 .env 却拿不到 DSN = 配置坏了，这是**失败**，不是跳过。
            check("能取到真库 DSN", False,
                  f"{envf} 存在，但解析不出 PG_USER/PG_PASSWORD/PG_PORT/PG_DB")
            print(""); print(f"通过 {PASS} ｜ 失败 {FAIL}")
            return 1
        print("  ⚠ 没有 .env，也没有 PG_DSN —— 本组必须打真库，跳过")
        print(""); print(f"通过 {PASS} ｜ 失败 {FAIL}")
        return 0

    from rpdao import WriteDao
    from rpdao.repo import build_repository

    dao = WriteDao(dsn, app_name="test-resample-stations")
    dao.open()
    ge = build_repository("GE", dao)

    try:
        # ══════════════════════════════════════════════════════════════
        print("第 1 组  前置：路段与要素都在（谓词没写飘）")
        # ══════════════════════════════════════════════════════════════
        sec = dao.query_one(
            "SELECT id, start_station_km, end_station_km "
            "FROM road_section WHERE id = %(sid)s", {"sid": SECTION_ID})
        if sec is None:
            check(f"路段 {SECTION_ID} 存在", False, "拿不到 road_section 那一行")
            print(""); print(f"通过 {PASS} ｜ 失败 {FAIL}")
            return 1
        sec_start = float(sec["start_station_km"])
        sec_end = float(sec["end_station_km"])
        check(f"路段 {SECTION_ID} 存在", True)
        print(f"       桩号范围 {sec_start} → {sec_end}")

        n_elem = dao.query_one(
            "SELECT count(*) AS n FROM alignment_element "
            "WHERE section_id = %(sid)s", {"sid": SECTION_ID})["n"]
        n_pi = dao.query_one(
            "SELECT count(*) AS n FROM alignment_pi "
            "WHERE section_id = %(sid)s AND tangent_len_m IS NOT NULL "
            "  AND curve_len_m IS NOT NULL", {"sid": SECTION_ID})["n"]
        check("线元有数据", n_elem > 0, f"alignment_element = {n_elem}")
        check("交点有 T/L（曲中点要算得出来）", n_pi > 0,
              f"带 tangent_len_m 且 curve_len_m 的 alignment_pi = {n_pi}")

        # ══════════════════════════════════════════════════════════════
        print()
        print("第 2 组  ★ 硬回归：20 m 重采样 MUST 重建出 .STA 给的 332 个桩号")
        # ══════════════════════════════════════════════════════════════
        imported = {round(float(r["station_local_km"]), 6)
                    for r in ge.stations(SECTION_ID, limit=20000)}
        rows20 = ge.resample_stations(SECTION_ID, DESIGN_INTERVAL_M)
        got20 = {round(float(r["station_km"]), 6) for r in rows20}
        check("导入的桩号数 == 332（.STA 逐点导入）",
              len(imported) == 332, f"实际 {len(imported)}")
        check("20 m 重采样点数 == 332", len(got20) == 332, f"实际 {len(got20)}")

        only_rule = sorted(got20 - imported)
        only_imp = sorted(imported - got20)
        # ★ 允许 1 mm 内的偏差 —— 不是「差不多就行」，是曲中点重算的舍入，
        #   见模块 docstring。逐点距离 MUST ≤ 1 mm。
        max_gap = 0.0
        for v in only_rule:
            near = [abs(v - w) for w in only_imp]
            max_gap = max(max_gap, min(near) if near else 9.9)
        # ★ 先 round 到 9 位再比：`abs(5.130565 - 5.130566)` 在浮点里是
        #   1.0000000000287557e-06，直接 `<= 1e-6` 会**假红**。
        #   1 mm 就是 1 mm —— 判据是「≤ 1 mm」，不是「≤ 1 mm 减去浮点尾差」。
        check("★ 与 .STA 逐点对齐（差异 MUST ≤ 1 mm）",
              round(max_gap, 9) <= TOL_KM,
              f"规则独有 {only_rule}\n导入独有 {only_imp}\n最大最近距离 {max_gap:.6f} km")

        # ══════════════════════════════════════════════════════════════
        print()
        print("第 3 组  ★ 特征点与间隔无关（换密度 MUST NOT 丢掉曲线起终点）")
        # ══════════════════════════════════════════════════════════════
        feats: dict[float, set] = {}
        for iv in (20.0, 5.0, 1.0):
            rs = ge.resample_stations(SECTION_ID, iv)
            feats[iv] = {round(float(r["station_km"]), 9)
                         for r in rs if str(r["kind"]) in FEATURE_KINDS}
        check("20 / 5 / 1 m 三档的特征点集合完全相同",
              feats[20.0] == feats[5.0] == feats[1.0],
              f"20m={len(feats[20.0])} 5m={len(feats[5.0])} 1m={len(feats[1.0])}")
        check("特征点 = 线元起终点 34 + 曲中点 8 = 42",
              len(feats[20.0]) == 42, f"实际 {len(feats[20.0])}")

        # ══════════════════════════════════════════════════════════════
        print()
        print("第 4 组  全部判据（纯函数 audit）在三档间隔上都通过")
        # ══════════════════════════════════════════════════════════════
        for iv in (20.0, 5.0, 1.0):
            rs = ge.resample_stations(SECTION_ID, iv)
            probs = audit(rs, iv, sec_start, sec_end)
            check(f"audit 通过 @ {iv:g} m（{len(rs)} 点）", not probs,
                  "\n".join(probs))

        # ══════════════════════════════════════════════════════════════
        print()
        print("第 5 组  拒错不静默")
        # ══════════════════════════════════════════════════════════════
        for bad in (0, -5, 0.0):
            try:
                ge.resample_stations(SECTION_ID, bad)
                check(f"间隔 {bad} MUST 报错", False, "它没报错，静默返回了")
            except ValueError:
                check(f"间隔 {bad} 报错", True)

        # ★ 超 limit：MUST 报错，MUST NOT 静默截断。
        #   截断吃掉的是尾部 —— 而尾部正是特征点和路段终点所在。
        try:
            ge.resample_stations(SECTION_ID, 0.1, limit=100)
            check("结果超 limit MUST 报错（不许静默截断）", False,
                  "它静默截断了 —— 拿残缺网格算出来的结果看起来是正常的")
        except ValueError as e:
            check("结果超 limit MUST 报错（不许静默截断）",
                  "超过 limit" in str(e), f"报错了但措辞意外：{e}")

        # 未知路段：拿不到 b 那一行 → 结果为空（不报错，由调用方判断）
        rs_unknown = ge.resample_stations(999999, 20.0)
        check("未知路段返回空而不是抛异常（查询≠校验）", rs_unknown == [],
              f"实际 {rs_unknown[:3]}")

        # ══════════════════════════════════════════════════════════════
        print()
        print("第 6 组  元测试：判据必须真的会红（假数据自带，不吃环境）")
        # ══════════════════════════════════════════════════════════════
        # ★ 教训：元测试的假数据 MUST 自己造。上一道门（列注释）里我让
        #   元测试从环境里挑受害者，结果我注入的探针按字典序排最前被挑走，
        #   报出一个与判据无关的失败。
        #
        # ★★ 第二次教训（就在本组第一次跑的时候）：我造的这组「**应当通过**」
        #    的对照数据，**自己违反了判据** —— 两个 `grid` 点相隔 0.06 而间隔
        #    是 0.02，`audit` 正确地报「网格不等间隔」。元测试当场抓到的是
        #    **我的假数据**，不是判据。所以：特征点 MUST 放在**非整桩**位置上，
        #    否则它会打断网格的等间隔性。
        clean = [
            {"station_km": 0.000000, "kind": "grid"},
            {"station_km": 0.010000, "kind": "curve_mid"},      # 非整桩
            {"station_km": 0.020000, "kind": "grid"},
            {"station_km": 0.040000, "kind": "grid"},
            {"station_km": 0.050000, "kind": "element_bound"},  # 非整桩
            {"station_km": 0.060000, "kind": "grid"},
        ]
        check("6a 对照：干净假数据 MUST 通过",
              audit(clean, 20.0, 0.0, 0.06) == [],
              f"实际 {audit(clean, 20.0, 0.0, 0.06)}")

        # 6b ★ 抽掉曲中点 —— 正是「把整张表按网格重生成」的后果
        no_qz = [r for r in clean if r["kind"] != "curve_mid"]
        p = audit(no_qz, 20.0, 0.0, 0.06)
        check("6b 缺曲中点 MUST 被抓到",
              any("curve_mid" in x for x in p), f"实际 {p}")

        # 6c ★ 抽掉线元起终点
        no_eb = [r for r in clean if r["kind"] != "element_bound"]
        p = audit(no_eb, 20.0, 0.0, 0.06)
        check("6c 缺线元起终点 MUST 被抓到",
              any("element_bound" in x for x in p), f"实际 {p}")

        # 6d 网格不等间隔
        bad_grid = [
            {"station_km": 0.000000, "kind": "grid"},
            {"station_km": 0.010000, "kind": "element_bound"},
            {"station_km": 0.020000, "kind": "grid"},
            {"station_km": 0.050000, "kind": "grid"},   # ← 该是 0.040
            {"station_km": 0.060000, "kind": "curve_mid"},
        ]
        p = audit(bad_grid, 20.0, 0.0, 0.06)
        check("6d 网格不等间隔 MUST 被抓到",
              any("不等间隔" in x for x in p), f"实际 {p}")

        # 6e 缺路段终点
        no_end = [
            {"station_km": 0.000000, "kind": "grid"},
            {"station_km": 0.020000, "kind": "curve_mid"},
            {"station_km": 0.040000, "kind": "grid"},
            {"station_km": 0.050000, "kind": "element_bound"},
        ]
        p = audit(no_end, 20.0, 0.0, 0.060421)
        check("6e 缺路段终点 MUST 被抓到",
              any("终点" in x for x in p), f"实际 {p}")

        # 6f 未知 kind
        weird = clean + [{"station_km": 0.080000, "kind": "swamp"}]
        p = audit(weird, 20.0, 0.0, 0.08)
        check("6f 未知 kind MUST 被抓到",
              any("未知的 kind" in x for x in p), f"实际 {p}")

        # 6g 空结果
        p = audit([], 20.0, 0.0, 0.06)
        check("6g 空结果 MUST 被抓到", bool(p), "空结果被当成通过了")

        # 6h 越界
        outb = [
            {"station_km": 0.000000, "kind": "grid"},
            {"station_km": 0.020000, "kind": "curve_mid"},
            {"station_km": 0.099000, "kind": "element_bound"},  # 超出 0.06
        ]
        p = audit(outb, 20.0, 0.0, 0.06)
        check("6h 越界 MUST 被抓到",
              any("之外" in x for x in p), f"实际 {p}")

        # 6i ★ 网格只铺了开头一段 —— 就是红 B 那个变异漏过去的情形。
        #    等间隔 ✓、端点 ✓、特征点 ✓，唯独没铺满。
        short_grid = [
            {"station_km": 0.000000, "kind": "grid"},
            {"station_km": 0.020000, "kind": "grid"},
            {"station_km": 0.040000, "kind": "grid"},
            {"station_km": 0.050000, "kind": "element_bound"},
            {"station_km": 0.060000, "kind": "curve_mid"},
        ]
        # 间隔 20 m、路段到 1 km —— 网格只到 0.04，差得远
        p = audit(short_grid, 20.0, 0.0, 1.0)
        check("6i 网格没铺满路段 MUST 被抓到（红 B 补的那条）",
              any("没有铺满" in x for x in p), f"实际 {p}")

        # ══════════════════════════════════════════════════════════════
        print()
        print("第 7 组  真库实测数字（留痕，供回归时对照）")
        # ══════════════════════════════════════════════════════════════
        for iv in (20.0, 5.0, 1.0):
            rs = ge.resample_stations(SECTION_ID, iv)
            grid_n = sum(1 for r in rs if r["kind"] == "grid")
            print(f"       {iv:>5g} m → 共 {len(rs):>5} 点 "
                  f"（grid {grid_n} ｜ element_bound 34 ｜ curve_mid 8）")
    finally:
        dao.close()

    print("")
    print(f"通过 {PASS} ｜ 失败 {FAIL}")
    print("全部通过" if FAIL == 0 else "有失败项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
