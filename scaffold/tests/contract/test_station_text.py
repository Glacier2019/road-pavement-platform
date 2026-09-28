"""契约② / FR-009~FR-011：**桩号文本与数值可互相还原**（002-US3）。

三条需求：

  FR-009  文本 ↔ 数值双向还原；不一致则失败并**指出具体行**
  FR-010  校验覆盖**全部**记录，不得只抽样；且 MUST 有元测试证明坏数据被拒
  FR-011  保留桩号类型（整桩/加桩/端点），**加桩 MUST NOT 被规整**

★ 这一组的主线是 **FR-010 那句「不得只抽样」**。
  「抽 10 条都对」和「332 条都对」在测试报告里长得一样 ——
  都是「通过」。所以这里的分母**从数据里数出来**，不写死。

跑法：
    cd scaffold
    uv run --quiet --with pyyaml tests/contract/test_station_text.py
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
from adapters.weidi import sta                                     # noqa: E402
from adapters.errors import SourceInvalid                          # noqa: E402

FX = ROOT / "tests" / "fixtures" / "design_import"
FULL = ROOT / "tests" / "fixtures" / "design_import" / "weidi_sta_full_332.STA"

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


def main() -> int:
    # ★ 全文 332 条的那个 fixture —— FR-010 要的是**全部**，不是摘录。
    #   找不到就直接失败，不静默改用摘录：那会让「覆盖全部」变成「覆盖 30 条」。
    if not FULL.exists():
        print(f"  [!!] 缺全量 fixture {FULL.name} —— 无法验证「覆盖全部记录」"
              "（改用摘录会让 FR-010 变成空话）")
        return 1
    text = FULL.read_text(encoding="utf-8")
    pts = sta.parse(text, file=FULL.name)["points"]

    print("第 1 组  FR-009：双向还原，逐条")
    n = len(pts)
    check("★ 全量 fixture 确实有 332 条（分母从数据里数出来，不写死）",
          n == 332, f"实为 {n}")
    bad: list[str] = []
    for i, pt in enumerate(pts, start=2):
        try:
            sta.roundtrip(pt, file=FULL.name, line_no=i)
        except SourceInvalid as exc:
            bad.append(f"行{i}: {exc}")
    check(f"★★ FR-010：**全部** {n} 条逐一往返成功（不是抽 10 条）",
          not bad, f"失败 {len(bad)} 条：{bad[:3]}")

    print("")
    print("第 2 组  与 design_import.station_text **逐值一致**（两个实现不漂）")
    diffs = [(pt["station_m"], sta.station_to_text(pt["station_m"]),
              di.station_text(pt["station_m"]))
             for pt in pts
             if sta.station_to_text(pt["station_m"]) != di.station_text(pt["station_m"])]
    check("★★ 两处实现 332 条逐值相同（重复实现由测试钉住，不靠人记得同步）",
          not diffs, f"漂了 {len(diffs)} 处：{diffs[:3]}")

    print("")
    print("第 3 组  FR-011：桩号类型（加桩不得被规整）")
    first, last = pts[0]["station_m"], pts[-1]["station_m"]
    kinds = [sta.station_type_of(p["station_m"], first=first, last=last) for p in pts]
    n_int = kinds.count("integer")
    n_jz = kinds.count("jiazi")
    n_end = kinds.count("endpoint")
    check("★★ 实测分型 = 整桩 290 + 加桩 40 + 端点 2",
          (n_int, n_jz, n_end) == (290, 40, 2),
          f"实为 整桩 {n_int} + 加桩 {n_jz} + 端点 {n_end}")
    jz = [p["station_m"] for p, k in zip(pts, kinds) if k == "jiazi"]
    check("★★ 加桩**确实存在**（否则下一条是空转）", len(jz) == 40, f"{len(jz)} 个")
    check("★★ 40 个加桩**没有一个**是 20 m 整数倍（没被规整）",
          all(abs(v % 20.0) > 1e-9 and abs(20.0 - v % 20.0) > 1e-9 for v in jz),
          f"样本 {jz[:4]}")
    # 加桩全部紧跟一个整桩 —— 曲线特征点的形态
    _idx = [i for i, k in enumerate(kinds) if k == "jiazi"]
    check("★ 40 个加桩**全部**紧跟一个整桩（曲线特征点的形态）",
          all(i > 0 and kinds[i - 1] == "integer" for i in _idx),
          f"不满足的位置：{[i for i in _idx if not (i > 0 and kinds[i-1] == 'integer')][:5]}")

    print("")
    print("第 4 组  ★★ 元测试：坏数据真的会被拒（FR-010 明文要求）")

    def rejects(name: str, fn, expect: str = "") -> None:
        global PASS, FAIL
        try:
            fn()
            FAIL += 1
            print(f"  [!!] {name} —— **没拦住**")
        except SourceInvalid as exc:
            if expect and expect not in str(exc):
                FAIL += 1
                print(f"  [!!] {name} —— 拦住了但理由不对：{exc}")
            else:
                PASS += 1
                print(f"  [OK] {name} —— {str(exc)[:78]}")

    rejects("元测试：文本 `K0+545.999` 配数值 545.874（差 125 mm）被拒",
            lambda: sta.roundtrip({"station_m": 545.874,
                                  "station_text_source": "K0+545.999"}))
    rejects("元测试：桩号文本 `K0+54` 缺位被拒（宽松正则会把放它进来）",
            lambda: sta.text_to_station("K0+54"))
    rejects("元测试：桩号文本 `0+545.874` 缺 K 被拒",
            lambda: sta.text_to_station("0+545.874"))
    rejects("元测试：桩号文本 `K0-545.874` 用负号被拒",
            lambda: sta.text_to_station("K0-545.874"))
    rejects("元测试：缺 station_m 字段被拒",
            lambda: sta.roundtrip({"seq_no": 1}))
    rejects("元测试：报错**带行号**（FR-009 明文「指出具体行」）",
            lambda: sta.text_to_station("K0+54", file="x.STA", line_no=42),
            expect="42")

    print("")
    print("第 5 组  边界：合法但不常见的桩号要能过")
    for s, want in (("K0+000", 0.0), ("K0+000.000", 0.0),
                    ("K4+635.000", 4635.0), ("K12+345.678", 12345.678),
                    ("K0+545.874", 545.874)):
        got = sta.text_to_station(s)
        check(f"  {s} → {want} m", abs(got - want) < 1e-6, f"得 {got}")
    check("★ 6 位口径：数值 → 文本 → 数值 仍然一致（库精度的那个口径）",
          abs(sta.text_to_station(sta.station_to_text(5805.421, places=6),) - 5805.421) < 1e-6)
    # ★ places 只认 3 / 6：传 7 必须报错，而不是静默按 3 位或按 7 位算。
    #   静默的话，调用方以为拿到了 7 位精度，实际拿到的是别的位数。
    _rt = False
    try:
        sta.station_to_text(1.0, places=7)
    except ValueError:
        _rt = True
    check("★ places 传非法值时报错（不是静默按 3 位处理）", _rt)

    print("")
    print(f"通过 {PASS} ｜ 失败 {FAIL}")
    print("全部通过" if FAIL == 0 else "有失败项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())