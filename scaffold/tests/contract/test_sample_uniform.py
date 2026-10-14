"""契约② FR-014：**采样**与**桩号**是两回事

════════════════════════════════════════════════════════════════════════
这条测试守的是什么
════════════════════════════════════════════════════════════════════════

一句话：**别把「采样」说成「桩号」。**

    stations()        = 设计给的桩号（`.STA` 导入），**不等间隔**。毕设 332 个。
    sample_uniform()  = 按间隔取样，**等间隔**，**用时才算、不落表**。
    线元终点桩号       = `alignment_element.end_station_km` **那一列属性**，
                        直接读表就有 —— **不在采样返回值里**。

★ 这三样东西曾经被我搅在一个返回值里（`grid` / `element_bound` / `curve_mid`），
  理由是「20 m 下三合一恰好等于 `.STA` 的 332 点」。那是**巧合** ——
  它成立只因为这份设计的 `station_interval_m` 正好是 20 m。
  已收掉（见 `工程决策记录.md` D-014、D-015）。

════════════════════════════════════════════════════════════════════════
「.STA 的 332 点」是什么 —— 这是关于**源文件的事实**，不是采样契约
════════════════════════════════════════════════════════════════════════

实测（`section_id=6` 毕设）：

    .STA 的 332 个桩号  =  20 m 整桩 (291)  ∪  线元终点 (33)  ∪  曲中点 (8)
                          其中 291 在整桩上，41 不在

    非整桩 41 = 线元终点 33 + 曲中点 8          ← 严丝合缝
    （线元的**起点** 0.000 落在整桩上，所以不额外贡献）

这说的是**纬地当年怎么生成 `.STA`**，不是**采样该怎么做**。
本测试把它当**事实**来钉（§4），不当采样契约。

════════════════════════════════════════════════════════════════════════
跑法
════════════════════════════════════════════════════════════════════════

    cd scaffold
    export PYTHONPATH="$PWD/modules/_shared"
    uv run --quiet --with "psycopg[binary,pool]==3.2.3" \
        python tests/contract/test_sample_uniform.py

★ `rpdao` 不在运行器 export 的 `modules/_shared` 里 —— 本文件自己把
  `modules/M3-rpdao` 插进 `sys.path`（与 `test_station_baseline.py` 同一写法）。

需要真库。没有 `.env` 也没有 `PG_DSN` 时**跳过**；`.env` 在但 DSN 解析不出来
时**报失败**（配置坏了不是跳过）。
"""

from __future__ import annotations

import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve()
ROOT = _HERE.parents[2]
sys.path.insert(0, str(_HERE.parent))
# ★ 运行器只 export 了 `modules/_shared`（见 run_contract_tests.sh L35），
#   `rpdao` 不在那里面。本仓的约定是**测试文件自己**把 M3 插进 sys.path。
if str(ROOT / "modules" / "M3-rpdao") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules" / "M3-rpdao"))

from _env import ROOT as _ENV_ROOT  # noqa: E402
from _env import dsn as _env_dsn, env as _env_env  # noqa: E402

SECTION_ID = 6
TOL_KM = 1e-6          # 1 mm —— 重算曲中点的舍入，见 §4
IMPORTED_TOTAL = 332   # ★ section_id=6 的 .STA 行数，**不是平台级常量**
GRID_20M = 291         # ★ 同上：0 → 5.800 每 20 m 一个点
OFFGRID = 41           # ★ 同上：332 − 291
ELEMENT_ENDS = 33      # ★ 同上：33 个线元各一个终点
CURVE_MIDS = 8         # ★ 同上：8 个交点

PASS = 0
FAIL = 0


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
# 纯函数判据 —— 不碰库，所以元测试可以喂假数据
# ══════════════════════════════════════════════════════════════════════════

def audit_uniform(rows: list[dict], interval_m: float,
                  lo: float, hi: float) -> list[str]:
    """采样结果的判据。返回问题清单，空 = 通过。

    ★ 只判**等间隔采样**该有的性质。**不**判「有没有特征点」——
      特征点不是采样的事（线元终点在 `alignment_element` 里）。
    """
    problems: list[str] = []
    vals = [round(float(r["station_km"]), 9) for r in rows]
    lo, hi = round(lo, 9), round(hi, 9)
    step = interval_m / 1000.0

    # ---- ① 非空 ---------------------------------------------------------
    if not vals:
        problems.append("采样结果为空 —— 一个空网格不是「这段路没有点」，"
                        "多半是范围写错了")
        return problems

    # ---- ② 升序、无重复 --------------------------------------------------
    if any(b <= a for a, b in zip(vals, vals[1:])):
        dup = [b for a, b in zip(vals, vals[1:]) if b <= a]
        problems.append(f"采样点未严格升序或重复：{dup[:4]}")

    # ---- ③ ★ 等间隔（这是本方法**唯一**的实质承诺）------------------------
    if len(vals) >= 2:
        bad = [(vals[i], round(vals[i + 1] - vals[i], 9))
               for i in range(len(vals) - 1)
               if abs((vals[i + 1] - vals[i]) - step) > 1e-9]
        if bad:
            problems.append(
                f"采样不等间隔（应为 {step} km）：{bad[:4]}（共 {len(bad)} 处）")

    # ---- ④ ★ 起点落在区间起点上 ------------------------------------------
    if abs(vals[0] - lo) > TOL_KM:
        problems.append(f"首点 {vals[0]} 不在区间起点 {lo} 上")

    # ---- ⑤ ★ 铺满：末点之后**不足一个间隔** ------------------------------
    #   ★★ 这条是红验证补出来的，不是一开始想到的。
    #     我注入的变异是「把点数写死按 20 m 算」（步长仍用 interval）。
    #     它**没被抓住** —— 因为「等间隔」只约束**相邻两点的距离**，
    #     对「铺到哪里」一个字都没说。一个从头开始、越铺越短的网格
    #     完全满足等间隔。判据缺了「铺满」。
    if vals[-1] + step < hi - 1e-9:
        problems.append(
            f"没铺满区间：末点 {vals[-1]}，区间终点 {hi}，间隔 {step} km —— "
            "差得超过一个间隔，说明只生成了开头一段。"
            "（等间隔只约束相邻距离，对覆盖范围不置一词）")

    # ---- ⑥ 不许越界 ------------------------------------------------------
    out = [v for v in vals if v < lo - TOL_KM or v > hi + TOL_KM]
    if out:
        problems.append(f"有点落在区间之外 [{lo}, {hi}]：{out[:4]}")

    # ---- ⑦ ★ 末点 MUST NOT 越过区间终点（等间隔的性质，不是丢尾巴）--------
    if vals[-1] > hi + TOL_KM:
        problems.append(f"末点 {vals[-1]} 越过区间终点 {hi}")

    return problems


# ══════════════════════════════════════════════════════════════════════════
# 库连接
# ══════════════════════════════════════════════════════════════════════════

def _dsn() -> str | None:
    """DSN。★ 解析复用 `_env`，**不自己再写一份**。

    ★★ 我第一版就是自己写了一份，而且把路径拼成了
      `ROOT / "scaffold" / ".env"` —— 而 `ROOT` 已经是 `scaffold/` 了。
      文件不存在 ⇒ `_dsn()` 返回 None ⇒ **静默跳过**，
      屏幕上打出「通过 0 ｜ 失败 0」，**看着像通过**。
      ⇒ 这正是 `_env.py` 开头警告的那件事：本仓已经有四份副本，
        我是**第五份**；而且我这一份**从来没跑过真库那半**。
      ★ 教训：**一份「从没运行过」的检查，和「通过」在屏幕上是同一个样子。**

    ★ 本函数只做一件 `_env` 不做的事：**区分「本机确实没有 .env」与
      「.env 在但读不全」**。前者跳过，后者**报失败** ——
      配置坏了不是「没有数据库」，跳过会让一道坏掉的配置永远看不见。
    """
    d = _env_dsn()
    if d:
        return d
    if (_ENV_ROOT / ".env").is_file() and _env_env():
        raise RuntimeError(
            f"{_ENV_ROOT / '.env'} 存在，但拼不出 DSN —— "
            "这是配置问题，不是「没有数据库」。跳过会让这道门永远不响。")
    return None


def main() -> int:
    print("契约② FR-014：采样 ≠ 桩号")
    print("=" * 72)

    try:
        dsn = _dsn()
    except RuntimeError as e:
        print(f"  [!!] {e}")
        print(f"\n通过 {PASS} ｜ 失败 {FAIL + 1}")
        return 1
    if not dsn:
        print("  [--] 没有 .env 也没有 PG_DSN —— 跳过（本组打真库）")
        print(f"\n通过 {PASS} ｜ 失败 {FAIL}")
        return 0

    from rpdao import WriteDao
    from rpdao.repo import build_repository

    dao = WriteDao(dsn, app_name="contract:fr014-sampling")
    dao.open()
    try:
        ge = build_repository("GE", dao)
        return _run(ge)
    finally:
        dao.close()


def _run(ge) -> int:
    # ── 第 1 组  前置 ─────────────────────────────────────────────────────
    print("\n第 1 组  前置：路段、桩号、线元都在（谓词没写飘）")
    sec = ge.section(SECTION_ID)
    check("路段 6 存在", bool(sec))
    lo = float(sec["start_station_km"])
    hi = float(sec["end_station_km"])
    print(f"       桩号范围 {lo} → {hi}")

    imported = sorted(round(float(r["station_local_km"]), 6)
                      for r in ge.stations(SECTION_ID))
    check(f"导入的桩号数 == {IMPORTED_TOTAL}（.STA 逐点导入）",
          len(imported) == IMPORTED_TOTAL, f"实际 {len(imported)}")

    elems = ge.alignment(SECTION_ID)
    ends = sorted({round(float(e["end_station_km"]), 6)
                   for e in (elems.get("elements") or elems.get("alignment_element") or [])})
    check(f"线元终点桩号数 == {ELEMENT_ENDS}（读 alignment_element 那一列）",
          len(ends) == ELEMENT_ENDS, f"实际 {len(ends)}")

    # ── 第 2 组 ★ 采样是等间隔的 ──────────────────────────────────────────
    print("\n第 2 组  ★ 采样：等间隔、铺满、不越界")
    got: dict[float, list[float]] = {}
    for iv in (20.0, 10.0, 5.0, 1.0):
        rows = ge.sample_uniform(SECTION_ID, iv)
        vals = [round(float(r["station_km"]), 9) for r in rows]
        got[iv] = vals
        p = audit_uniform(rows, iv, lo, hi)
        check(f"audit 通过 @ {iv:g} m（{len(vals)} 点）", not p, f"实际 {p}")

    # ── 第 3 组 ★★ 采样 ≠ 桩号（本文件存在的理由）──────────────────────────
    print("\n第 3 组  ★★ 采样 ≠ 桩号：两个数不一样，而且必须不一样")
    check(f"20 m 采样 = {GRID_20M} 点（等间隔）",
          len(got[20.0]) == GRID_20M, f"实际 {len(got[20.0])}")
    check(f"stations() = {IMPORTED_TOTAL} 点（设计给的，不等间隔）",
          len(imported) == IMPORTED_TOTAL, f"实际 {len(imported)}")
    check("★ 两者 MUST NOT 相等 —— 相等就说明又把「采样」当成了「桩号」",
          len(got[20.0]) != len(imported),
          f"采样 {len(got[20.0])} ｜ 桩号 {len(imported)}")

    grid20 = {round(v, 6) for v in got[20.0]}
    off = [v for v in imported if v not in grid20]
    check(f"★ 桩号里不在 20 m 整桩上的 == {OFFGRID}（这就是「不等间隔」）",
          len(off) == OFFGRID, f"实际 {len(off)}")

    # ── 第 4 组  ★ 关于**源文件**的事实（不是采样契约）─────────────────────
    print("\n第 4 组  ★ .STA 的构成 —— 这是源文件的事实，不是采样契约")
    qz = sorted({round(float(r["station_km"]) - (2 * float(r["tangent_len_m"])
                                                 - float(r["curve_len_m"])) / 2.0 / 1000.0, 6)
                 for r in _pi_rows(ge)
                 if r.get("tangent_len_m") is not None
                 and r.get("curve_len_m") is not None})
    check(f"曲中点（QZ = JD − (2T − L)/2）数 == {CURVE_MIDS}",
          len(qz) == CURVE_MIDS, f"实际 {len(qz)}")

    rule = grid20 | set(ends) | set(qz)
    only_rule = sorted(rule - set(imported))
    only_imp = sorted(set(imported) - rule)
    check("★ 整桩 ∪ 线元终点 ∪ 曲中点 == .STA 的桩号集（332 对 332）",
          len(rule) == len(imported) == IMPORTED_TOTAL,
          f"规则 {len(rule)} ｜ 导入 {len(imported)}")
    # ★ 必须 `round(..., 9)` 再比 —— 实测 `abs(5.130565 - 5.130566)` 在二进制
    #   浮点下是 `1.0000000000287557e-06`，**比 1e-6 大**，于是「差 1 mm」
    #   会被判成「超过 1 mm」。
    #   ⇒ 判据是「≤ 1 mm」，不是「≤ 1 mm 减去浮点尾巴」。
    _gaps = [round(abs(a - b), 9) for a in only_rule for b in only_imp]
    check("★ 差异 ≤ 1 mm（重算 QZ 的舍入，不是结构差异）",
          len(only_rule) <= 1 and len(only_imp) <= 1
          and all(g <= TOL_KM for g in _gaps),
          f"规则独有 {only_rule[:3]}\n导入独有 {only_imp[:3]}\n差距 {_gaps}")

    # ── 第 5 组  ★ 线元终点是**表里的一列**，不在采样返回值里 ──────────────
    print("\n第 5 组  ★ 线元终点桩号：读表就有，不该由采样函数合成")
    # ★ 采样集 MUST **恰好**等于等间隔网格 —— 一个点都不多。
    #   如果又把线元终点/曲中点塞回采样结果，这里就会多出点来。
    #
    #   ★ 这条我第一版写成了
    #     `all(e in sample_keys or e not in sample_keys for e in ends)` ——
    #     **同义反复，永远为真**，什么也没判。
    #     一个永远为真的判据比没有判据更糟（宪法 Principle V）。
    step20 = 20.0 / 1000.0
    expect = {round(lo + k * step20, 6) for k in range(len(got[20.0]))}
    actual = {round(v, 6) for v in got[20.0]}
    check("★ 采样集 == 纯等间隔网格（没额外塞进线元终点/曲中点）",
          actual == expect,
          f"多出 {sorted(actual - expect)[:4]} ｜ 少了 {sorted(expect - actual)[:4]}")
    check("★ 线元终点桩号读表就有，不依赖采样",
          len(ends) == ELEMENT_ENDS and all(e >= 0 for e in ends),
          f"实际 {len(ends)} 个，如 {ends[:3]}")
    check("★ 采样行只有 station_km 一个键（没有 kind 之类的分类标签）",
          all(set(r.keys()) == {"station_km"}
              for r in ge.sample_uniform(SECTION_ID, 20.0)),
          f"实际键 {sorted(set(k for r in ge.sample_uniform(SECTION_ID, 20.0) for k in r))}")

    # ── 第 6 组  拒错不静默 ───────────────────────────────────────────────
    print("\n第 6 组  拒错不静默")
    for bad in (0, -5, 0.0, True):
        try:
            ge.sample_uniform(SECTION_ID, bad)
            check(f"间隔 {bad!r} MUST 报错", False, "没报错")
        except ValueError:
            check(f"间隔 {bad!r} 报错", True)
    try:
        ge.sample_uniform(SECTION_ID, 5.0, from_km=2.0, to_km=1.0)
        check("反区间 MUST 报错", False, "没报错")
    except ValueError:
        check("反区间（from >= to）报错", True)
    try:
        ge.sample_uniform(SECTION_ID, 1.0, limit=100)
        check("超 limit MUST 报错（不许静默截断）", False, "没报错")
    except ValueError as e:
        check("超 limit MUST 报错（不许静默截断）", "超过 limit" in str(e),
              f"实际 {str(e)[:60]}")
    check("未知路段返回空而不是抛异常（查询≠校验）",
          ge.sample_uniform(999999, 20.0) == [])

    # ── 第 7 组  元测试：判据必须真的会红（假数据自带，不吃环境）───────────
    print("\n第 7 组  元测试：判据必须真的会红（假数据自带，不吃环境）")
    # ★ 教训一：假数据 MUST 自己造。上一道门（列注释）里我让元测试从环境里
    #   挑受害者，结果探针按字典序排最前被挑走，报出一个与判据无关的失败。
    # ★ 教训二：对照组 MUST 自己先通过。我第一版的「应当通过」那组假数据
    #   **自己违反了判据**（两个 grid 点相隔 0.06 而间隔是 0.02），
    #   元测试当场抓到的是**我的假数据**，不是判据。
    clean = [{"station_km": round(0.0 + k * 0.02, 6)} for k in range(6)]
    check("7a 对照：干净假数据 MUST 通过",
          audit_uniform(clean, 20.0, 0.0, 0.10) == [],
          f"实际 {audit_uniform(clean, 20.0, 0.0, 0.10)}")

    uneven = [{"station_km": v} for v in (0.0, 0.02, 0.05, 0.06)]
    p = audit_uniform(uneven, 20.0, 0.0, 0.06)
    check("7b 不等间隔 MUST 被抓到",
          any("不等间隔" in x for x in p), f"实际 {p}")

    short = [{"station_km": v} for v in (0.0, 0.02, 0.04)]
    p = audit_uniform(short, 20.0, 0.0, 1.0)
    check("7c 没铺满 MUST 被抓到（红 B 补的那条）",
          any("没铺满" in x for x in p), f"实际 {p}")

    p = audit_uniform([], 20.0, 0.0, 1.0)
    check("7d 空结果 MUST 被抓到", any("为空" in x for x in p), f"实际 {p}")

    p = audit_uniform([{"station_km": v} for v in (0.02, 0.04, 0.06)],
                      20.0, 0.0, 0.06)
    check("7e 首点不在起点 MUST 被抓到",
          any("首点" in x for x in p), f"实际 {p}")

    p = audit_uniform([{"station_km": v} for v in (0.0, 0.02, 0.02, 0.04)],
                      20.0, 0.0, 0.04)
    check("7f 重复点 MUST 被抓到",
          any("升序" in x or "重复" in x for x in p), f"实际 {p}")

    p = audit_uniform([{"station_km": v} for v in (0.0, 0.02, 0.09)],
                      20.0, 0.0, 0.06)
    check("7g 越界 MUST 被抓到",
          any("之外" in x or "越过" in x for x in p), f"实际 {p}")

    # ── 第 8 组  真库实测数字（留痕，供回归时对照）────────────────────────
    print("\n第 8 组  真库实测数字（留痕，供回归时对照）")
    for iv in (20.0, 10.0, 5.0, 1.0):
        v = got[iv]
        print(f"       {iv:>4g} m → {len(v):>6} 点 ｜ "
              f"{v[0]:.6f} → {v[-1]:.6f} ｜ 路段终点 {hi} "
              f"{'（正好落上）' if abs(v[-1]-hi) <= TOL_KM else '（等间隔未落上，正常）'}")
    print(f"       stations() → {len(imported)} 点（不等间隔，{len(off)} 个不在 20 m 整桩上）")

    print(f"\n通过 {PASS} ｜ 失败 {FAIL}")
    if FAIL:
        print("有失败项")
        return 1
    print("全部通过")
    return 0


def _pi_rows(ge) -> list[dict]:
    """读交点的**桩号**与切线/曲线长，用来算曲中点。

    ★ 为什么不用 `alignment()["pis"]`：`PIS_SQL` 取了
      `tangent_len_m` / `curve_len_m`，**但没取 `station_km`**
      （实测列清单：pi_seq, pi_type, x_coord, y_coord, radius_m,
        spiral_ls1_m, spiral_ls2_m, spiral_a1, spiral_a2,
        prev_tangent_len_m, tangent_len_m, tangent_len2_m,
        arc_len_m, curve_len_m, deflection_deg, external_m）。
      我第一版就是走 `alignment()` 的，`r.get("station_km")` 全为 None
      ⇒ 筛完 0 行 ⇒ 曲中点算出来 **0 个**，而不是 8 个。

      ★ 我**没有**顺手给 `PIS_SQL` 补上 `station_km` —— 那是 M6 `/v1/`
        共用的查询，改它要连带核 API 契约，属于另一件事。
        本测试只读它需要的那一列。
    """
    return ge._dao.query(
        "SELECT station_km, tangent_len_m, curve_len_m FROM alignment_pi "
        "WHERE section_id = %(sid)s::bigint AND station_km IS NOT NULL "
        "AND tangent_len_m IS NOT NULL AND curve_len_m IS NOT NULL",
        {"sid": SECTION_ID})


if __name__ == "__main__":
    sys.exit(main())
