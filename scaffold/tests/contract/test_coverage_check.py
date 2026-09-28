"""契约② / FR-005~FR-008：**覆盖区间校验**（工单 #3 之后的 002-US2）。

四条需求，四条断言：

  FR-005  记录每个文件**实际解析出**的桩号覆盖区间 → 写进 design_file
  FR-006  声明区间 vs 实际区间不符时报出：文件、声明、实际、差值（米）
  FR-007  失败时该文件**零行入库**（原子性）
  FR-008  MUST NOT 补值/外推/默认填充

★ 这一组最容易写成永远通过，因为「没有覆盖区间」和「覆盖区间正确」
  在断言里长得一模一样（都是"没报错"）。所以每条都配了**证伪**：
  · FR-005：必须断言**算出来了非空区间**，而不是断言「没抛异常」；
  · FR-006：必须断言**越界真的被报出来**，且报出的差值是对的；
  · FR-008：必须断言**算不出的文件仍是 None**，而不是 0。

跑法：
    cd scaffold
    uv run --quiet --with pyyaml tests/contract/test_coverage_check.py
"""

from __future__ import annotations

import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve()
ROOT = _HERE.parents[2]
for _up in _HERE.parents:
    if (_up / "modules" / "M2-ingest").is_dir():
        ROOT = _up
        break
if str(ROOT / "modules" / "M2-ingest") not in sys.path:
    sys.path.insert(0, str(ROOT / "modules" / "M2-ingest"))

import design_import as di                                          # noqa: E402
from adapters import base                                           # noqa: E402
from adapters.weidi import sta as sta_mod, jd as jd_mod, pm as pm_mod, prj as prj_mod  # noqa: E402

FX = ROOT / "tests" / "fixtures" / "design_import"

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {name}" + (f" —— {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [!!] {name}" + (f" —— {detail}" if detail else ""))
    return ok


def _fixture_ir() -> dict:
    """由**真 fixture** 拼一份 IR。不手搓字典 —— 手搓的字段名会和适配器漂。"""
    sta_p = sta_mod.parse((FX / "weidi_sta_excerpt.STA").read_text(encoding="utf-8"),
                          file="a.STA")["points"]
    jd_p = jd_mod.parse((FX / "weidi_jd_excerpt_3cp.JD").read_text(encoding="utf-8"),
                        file="a.JD")["control_points"]
    pm_p = pm_mod.parse((FX / "weidi_pm_excerpt_4units.pm").read_text(encoding="utf-8"),
                        file="a.pm")["elements"]
    return base.make_ir(
        vendor="weidi", origin="file", files=[], capabilities=(),
        segments={"station_sequence": sta_p, "alignment_pi": jd_p,
                  "alignment_element": pm_p})


def main() -> int:
    ir = _fixture_ir()

    print("第 1 组  FR-005：实际覆盖区间**算出来了**")
    cov = di.coverage_of_files(ir)
    check("★ 非空：至少算出 3 个后缀的区间（否则下面全是空转）",
          len(cov) >= 3, f"算出 {sorted(cov)}")
    check("★ .STA 区间 = 0.000 ~ 545.874 m（与 fixture 实测一致）",
          ".sta" in cov and abs(cov[".sta"][0]) < 1e-6 and abs(cov[".sta"][1] - 545.874) < 1e-6,
          f"{cov.get('.sta')}")
    check("★ .pm 区间 = 0.000 ~ 718.682 m",
          ".pm" in cov and abs(cov[".pm"][1] - 718.68225418) < 1e-6,
          f"{cov.get('.pm')}")
    check(".jd 区间 = 0.000 ~ 1531.817 m",
          ".jd" in cov and abs(cov[".jd"][1] - 1531.81656009) < 1e-6,
          f"{cov.get('.jd')}")

    print("")
    print("第 2 组  FR-008：算不出的**保持 None**，不补 0")
    check("★★ 未解析的后缀不出现在区间字典里（不是 (0,0)）",
          ".dmx" not in cov and ".ctr" not in cov,
          f"出现了会是「补值」，正是 FR-008 禁止的：{sorted(cov)}")
    check("★★ 区间为空的段返回 None（不是 (0,0)）",
          di.station_range_of_segment(ir, "profile_ground_point") is None,
          f"{di.station_range_of_segment(ir, 'profile_ground_point')}")

    print("")
    print("第 3 组  FR-006：声明 vs 实际不符时报出四样东西")
    # 相符 → 不报
    ok_decl = {".sta": (0.0, 0.545874), ".pm": (0.0, 0.71868225418)}
    bad = di.check_declared_coverage(ir, ok_decl)
    check("声明与实际相符时不报（不狼来了）", not bad, str(bad[:2]))

    # 不符 → 报，且四样齐全
    wrong_decl = {".sta": (0.0, 0.600)}
    bad2 = di.check_declared_coverage(ir, wrong_decl)
    check("★★ 声明 0.600 而实际 0.545874 时**报出来**", bool(bad2), str(bad2))
    if bad2:
        msg = bad2[0]
        check("  └ 报出**文件名/后缀**", ".sta" in msg, msg)
        check("  └ 报出**声明区间**", "600.000" in msg, msg)
        check("  └ 报出**实际区间**", "545.874" in msg, msg)
        check("  └ 报出**差值（米）**", "+54.126" in msg or "-54.126" in msg, msg)

    print("")
    print("第 4 组  ★★ 元测试：第 3 组的断言真的会红吗")
    # 把实际区间挪 1 m 以上，必须报
    just_over = {".sta": (0.0, 545.874 + 1.5)}
    check("★★ 元测试：差 1.5 m（超容差 1 m）**必须**报",
          bool(di.check_declared_coverage(ir, just_over)),
          "没报 —— 说明容差写太宽，或根本没比")
    # ★ 容差写在函数默认值里（tol_m=1.0），测试不重写它 —— 重写就测不到默认值。
    # 差 0.5 m 属于容差内。
    # 单位坑：declared 是 km（列名 coverage_*_station_km），而实际区间算出来是 m。
    #   所以下面的容差用米表达时要除 1000。
    #   本文第一版写成 545.874 + 0.5 —— 那是差 500 米，
    #   于是「容差内不报」这条变成假红：它报了，而我以为是容差写错了。
    #   单位写错的测试比没测试更坏 —— 它会把后来人引向错的代码。
    just_under = {".sta": (0.0, (545.874 + 0.5) / 1000.0)}
    check("★ 元测试：差 0.5 m（容差 1 m 内）**不**报（容差不是 0）",
          not di.check_declared_coverage(ir, just_under),
          "报了 —— 说明容差实际上是 0，正常差异会被误报")
    # 声明了但一个桩号都没解析出来 —— 最危险的一种
    ghost = {".dmx": (0.0, 5.0)}
    g = di.check_declared_coverage(ir, ghost)
    check("★★ 元测试：声明了却**一个桩号都没解析出**必须报（最危险的一种）",
          bool(g) and "未解析出任何桩号" in g[0], str(g))

    print("")
    print("第 5 组  FR-005 落点：台账行真的带上了区间")
    prj_o = prj_mod.parse(prj_mod.decode((FX / "weidi_prj_excerpt.PRJ").read_bytes()),
                          file="a.PRJ")
    p0 = di.plan_project(prj_o, project_dir=None)
    p1 = di.plan_project(prj_o, project_dir=None, ir=ir)
    _z0 = [r for r in p0["tables"]["design_file"] if r["file_name"].endswith(".pm")]
    _z1 = [r for r in p1["tables"]["design_file"] if r["file_name"].endswith(".pm")]
    check("前置：台账里确实有 .pm 这一行（否则下面两条是空转）",
          bool(_z0) and bool(_z1), f"{len(_z0)} / {len(_z1)}")
    if _z0 and _z1:
        check("★★ 不给 ir 时区间仍是 None（保持「不猜」）",
              _z0[0]["coverage_from_station_km"] is None,
              f"{_z0[0]['coverage_from_station_km']}")
        check("★★ 给了 ir 时区间**被填上**（且单位是 km，不是 m）",
              _z1[0]["coverage_from_station_km"] == 0.0
              and _z1[0]["coverage_to_station_km"] is not None
              and 0.71 < _z1[0]["coverage_to_station_km"] < 0.72,
              f"{_z1[0]['coverage_from_station_km']} ~ {_z1[0]['coverage_to_station_km']}")
        _other = [r for r in p1["tables"]["design_file"]
                  if r["file_name"].endswith(".3DR")]
        check("★★ FR-008：IR 里没有的后缀**仍是 None**（没被填成 0）",
              all(r["coverage_from_station_km"] is None for r in _other),
              f"{[(r['file_name'], r['coverage_from_station_km']) for r in _other]}")

    print("")
    print("第 6 组  FR-007：verify 报错时 load 一行都不写")
    ir_bad = dict(ir, declared_coverage={".sta": (0.0, 9.999)})
    # ★ 必须给**真的** planned：空字典会 KeyError("pi_source") ——
    #   那是测试自己写错，不是 verify 有问题。
    _planned = di.plan(ir_bad, section_id=1)
    verdict = di.verify(ir_bad, _planned)
    check("★★ 越界的 IR 经 verify 一定产生 errors", bool(verdict["errors"]),
          str(verdict["errors"][:1]))
    # ★ 原子性靠的是 load() 里 "先 verify 再开事务" 的顺序。
    #   这里**静态**验证那个顺序，而不是跑一次真库 —— 因为顺序反了会让
    #   「写了一半再报错」，那正是 FR-007 要防的，而真库测试只能证明"最终一致"。
    _src = (ROOT / "modules" / "M2-ingest" / "design_import.py").read_text(encoding="utf-8")
    _i_err = _src.find("raise LoadError", _src.find("def load("))
    _i_txn = _src.find("with dao.write_txn", _src.find("def load("))
    check("★★ FR-007：load() 里 raise LoadError 在 write_txn **之前**（先验后写）",
          _i_err != -1 and _i_txn != -1 and _i_err < _i_txn,
          f"raise@{_i_err} vs txn@{_i_txn}" if _i_err > _i_txn else "")

    print("")
    print(f"通过 {PASS} ｜ 失败 {FAIL}")
    print("全部通过" if FAIL == 0 else "有失败项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())