#!/usr/bin/env python3
"""spec 里的「实测声明」必须和实物对得上。

为什么要有这一组
----------------
`specs/003-local-terrain-viewer/spec.md` 里有一张表，表头写着
「已有数据（**实库实测**，不是规划）」，6 条带数字的声明：

    CRG u 范围 | 0.000 / 5805.400 | m
    CRG v 范围 | -4.2500 / 4.2500 | m
    网格步长   | 0.1 / 0.1       | m
    v 通道数   | 86 / 86         | 个
    曲面高程范围 | 53.9791 / 83.8131 | m   ← 2026-10-16 修正，原写 54.0641 / 83.8141
    最大曲率   | 0.0039 / 0.0039 | 1/m

第二块（几何）另有 7 条。两块合计 13 条。

**此前没有任何东西读过它们。** 全仓 `tests/` 里提到 `limits` 的文件数 = 0；
唯一读 `spec.md` 的是一致性测试，它一条数值都不比。

后果已经发生了：2026-10-15 修好横坡之后，曲面下界从 54.0641 掉到 53.9985
（差 65.6 mm），而**全套 31 个门全绿**。声明烂掉了，没人知道。

这一组钉的就是这件事：**声明必须有核对者，或者有具名的豁免。**

本组是**声明 vs 实物**的比对，不是「实现做了什么就该是什么」。
`limits` 块是**实测记录**，不是需求 —— 所以实物变了，声明就该跟着改，
本组的职责是**让那次修改被迫发生、并且看得见**，而不是自己把数字改绿。

离线、纯本地。不连库、不联网、不写盘。
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]      # …/scaffold
REPO = ROOT.parent                                      # 仓库根
SPECS = REPO / "specs"
ARTIFACTS = REPO / "artifacts" / "sim"
MANIFEST = ARTIFACTS / "manifest.json"

FAILED: list[str] = []
CHECKED: list[str] = []          # ★ 记录「真的比对过」的条目，供 §0 用
_CALLED: set[str] = set()        # ★ 记录「resolver 真的被调用过」的名字


def check(ok, msg: str, detail: str = "") -> bool:
    if not ok:
        FAILED.append(msg + (f" —— {detail}" if detail else ""))
    return bool(ok)


# ------------------------------------------------------------------ 解析
def limits_blocks(text: str) -> list[list[str]]:
    """抽出所有 ```limits 围栏里的行。"""
    blocks, cur, inside = [], [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("```"):
            if inside:
                blocks.append(cur)
                cur, inside = [], False
            elif s[3:].strip().lower() == "limits":
                inside = True
            continue
        if inside and s:
            cur.append(s)
    if inside:
        blocks.append(cur)
    return blocks


def parse_rows(text: str) -> list[tuple[str, list[float], int]]:
    """返回 [(名称, [声明值…], 小数位数), …]。

    行格式：`名称 | 值 | 单位`，值可以是 `a / b`。
    小数位数从**声明文本**取 —— 容差由声明精度定，不由实测残差定。
    """
    rows = []
    for blk in limits_blocks(text):
        for line in blk:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 2:
                continue
            name, raw = parts[0], parts[1]
            vals, decs = [], []
            for tok in raw.split("/"):
                tok = tok.strip()
                m = re.fullmatch(r"[-+]?\d+(?:\.\d+)?", tok)
                if not m:
                    break
                vals.append(float(tok))
                decs.append(len(tok.split(".")[1]) if "." in tok else 0)
            else:
                if vals:
                    rows.append((name, vals, min(decs)))
    return rows


# ------------------------------------------------------------------ 实物
def _manifest() -> dict:
    if not MANIFEST.is_file():
        raise FileNotFoundError(f"清单不在：{MANIFEST}（已跟踪，不该缺）")
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def oracle_crg(name: str) -> list[float]:
    """CRG 的四条。锚 = **已跟踪**的清单 crg 块（CRG 本体是 gitignore 的）。"""
    _CALLED.add(name)
    c = _manifest()["crg"]
    return {
        "CRG u 范围": [c["u_start_m"], c["u_end_m"]],
        "CRG v 范围": [c["v_right_m"], c["v_left_m"]],
        "网格步长": [c["u_increment_m"], c["v_increment_m"]],
        "v 通道数": [float(c["v_channels"]), float(c["v_channels"])],
    }[name]


def oracle_crg_z(name: str) -> list[float]:
    """曲面高程范围。锚 = 清单 crg 块的**全分辨率**极值。

    ★ 为什么不用两个 .bin：它们是抽稀网格（u 步长 4.0 / 1.0 m），
      最低点不一定落在采样桩号上。实测 4 m 网格给 53.9985，
      全分辨率给 53.9791 —— 拿网格核声明会**核错东西**。
    """
    _CALLED.add(name)
    c = _manifest()["crg"]
    return [c["z_min_m"], c["z_max_m"]]


RESOLVERS = {
    "CRG u 范围": oracle_crg,
    "CRG v 范围": oracle_crg,
    "网格步长": oracle_crg,
    "v 通道数": oracle_crg,
    "曲面高程范围": oracle_crg_z,
}

# ★ 豁免必须**具名**并写明理由。凡是在 spec 里出现、却既没有核对者
#   也不在这张表里的声明，本组一律判红 —— 这样新加一条声明而不加核对者
#   是不可能的，而「悄悄没人核」这条老路被堵死。
#
# ★ 这批的共性：**只有活库能给**。导出器按设计不读库、不联网
#   （见 export_viewer_mesh.py 文档头），所以离线没有锚。
#   2026-10-16 已用活库逐条人工核对过一次，**七条全部为真** ——
#   但它们不是被门守着的，只是这一轮我顺手看过。这一步要么补锚，
#   要么就承认它是口子。现在承认。
_EXCUSED_DB = (
    "只有活库能给；导出器按设计不读库、不联网，所以离线没有锚。"
    "★ 这是一个具名的口子：2026-10-16 人工核过一遍为真，但**没有门守着**。"
)
EXCUSED = {
    "最大曲率": _EXCUSED_DB,
    "线形单元": _EXCUSED_DB,
    "其中直线": _EXCUSED_DB,
    "其中缓和曲线": _EXCUSED_DB,
    "其中圆曲线": _EXCUSED_DB,
    "圆曲线半径": _EXCUSED_DB,
    "起终点高差": _EXCUSED_DB,
    "平面外接矩形": _EXCUSED_DB,
}


# ------------------------------------------------------------------ 比对
def compare(name: str, declared: list[float], actual: list[float], dec: int) -> bool:
    """容差 = 声明精度的一个量子（10^-dec）。声明写了几位，就要求对到几位。"""
    tol = 10.0 ** (-dec)
    ok = True
    for i, (d, a) in enumerate(zip(declared, actual)):
        if abs(d - a) > tol:
            ok = check(False, f"「{name}」第 {i + 1} 个值不符",
                       f"声明 {d} vs 实物 {a}，差 {a - d:+.6g}（容差 {tol:g}）")
    if ok:
        CHECKED.append(name)
    return ok


# ------------------------------------------------------------------ 分组
def sec0():
    """★ 宪法 §V：门必须确认自己的靶子真的在。"""
    text = (SPECS / "003-local-terrain-viewer" / "spec.md").read_text(encoding="utf-8")
    rows = parse_rows(text)
    check(len(rows) >= 6, "003 的 limits 块应解析出至少 6 条声明", f"得 {len(rows)}")
    check(len(limits_blocks(text)) >= 1, "003 应有 limits 块")

    # 任何一条声明都必须有归宿
    orphan = [n for n, _, _ in rows if n not in RESOLVERS and n not in EXCUSED]
    check(not orphan, "每条声明都要么有核对者、要么在豁免表里具名", f"无人核：{orphan}")

    # 全 spec 扫一遍，不只 003
    all_rows = []
    for spec in sorted(SPECS.glob("*/spec.md")):
        all_rows += parse_rows(spec.read_text(encoding="utf-8"))
    check(len(all_rows) >= len(rows),
          "全部 spec 的声明数不少于 003 的", f"{len(all_rows)} vs {len(rows)}")
    orphan_all = [n for n, _, _ in all_rows if n not in RESOLVERS and n not in EXCUSED]
    check(not orphan_all, "全部 spec 里没有无人核的声明", f"{orphan_all}")


def sec1():
    """逐条把声明和实物比。"""
    text = (SPECS / "003-local-terrain-viewer" / "spec.md").read_text(encoding="utf-8")
    for name, declared, dec in parse_rows(text):
        if name in EXCUSED:
            continue
        fn = RESOLVERS.get(name)
        if fn is None:
            check(False, f"「{name}」没有核对者", "应在 RESOLVERS 或 EXCUSED 里")
            continue
        compare(name, declared, fn(name), dec)


def sec2():
    """★ 元测试：比对函数必须能抓出错误声明。"""
    # 数值不对 ⇒ 必须红
    saved = list(FAILED)
    before = len(FAILED)
    compare("CRG u 范围", [0.0, 5805.400], [0.0, 5805.421], 3)
    check(len(FAILED) > before, "改错的声明必须被抓出来")
    FAILED[:] = saved

    # 数值正确 ⇒ 必须绿
    saved = list(FAILED)
    before = len(FAILED)
    compare("CRG v 范围", [-4.25, 4.25], [-4.25, 4.25], 4)
    check(len(FAILED) == before, "正确的声明不该被抓")

    # ★ 精度要求必须来自声明，不是写死的
    saved = list(FAILED)
    before = len(FAILED)
    # 同两个数，声明只写 1 位小数 ⇒ 容差 0.1 ⇒ 该项应通过
    compare("网格步长", [0.1, 0.1], [0.12, 0.1], 1)
    check(len(FAILED) == before, "容差应随声明精度放宽（1 位小数 ⇒ 0.1）")
    # 写 3 位小数 ⇒ 容差 0.001 ⇒ 同样的差应被抓
    before = len(FAILED)
    compare("网格步长", [0.100, 0.100], [0.120, 0.100], 3)
    check(len(FAILED) > before, "容差应随声明精度收紧（3 位小数 ⇒ 0.001）")
    FAILED[:] = saved


def sec3():
    """豁免表必须写明理由，且必须真的被用到过。"""
    for name, why in EXCUSED.items():
        check(len(why.strip()) >= 10, f"豁免「{name}」要写明理由", f"得 {why!r}")
    check(EXCUSED, "豁免表不该为空（为空说明有人把口子藏起来了）")


# ------------------------------------------------------------------ §4 曲面模型
# 003 spec 的「曲面模型」一节是**定义**，不是实测。它允许出现的小数只有
# 论文式号；任何常数都必须留在唯一来源（横断面测试）里。
_SURF_HEAD = "### 曲面模型：被观测的那个对象是什么"
_ALLOWED_DECIMALS = {"2.15", "2.16"}


def _surface_section(text: str) -> str | None:
    """截出「曲面模型」那一节：到下一个同级或更高级标题为止。"""
    lines = text.splitlines()
    depth = len(_SURF_HEAD) - len(_SURF_HEAD.lstrip("#"))
    for i, ln in enumerate(lines):
        if ln.strip() == _SURF_HEAD:
            out = []
            for nxt in lines[i + 1:]:
                s = nxt.lstrip()
                if s.startswith("#") and (len(s) - len(s.lstrip("#"))) <= depth:
                    break
                out.append(nxt)
            return "\n".join(out)
    return None


def _stray_decimal(sec: str) -> list[str]:
    return sorted(set(re.findall(r"\d+\.\d+", sec)) - _ALLOWED_DECIMALS)


def sec4():
    """曲面模型一节：定义可以写，常数不许复述。"""
    spec = (SPECS / "003-local-terrain-viewer" / "spec.md").read_text(encoding="utf-8")
    sec = _surface_section(spec)
    if not check(sec is not None, "003 spec 里应有「曲面模型」一节"):
        return

    # (a) 公式必须在。定义节里没有定义，那就只是一段散文。
    check("r(l, w) = r(l) + w" in sec, "曲面模型一节应写出直纹面公式 Σ")

    # (b) 不许放 limits 围栏 —— 定义不是实测，塞进围栏就得配核对者。
    check("```limits" not in sec, "曲面模型一节不许放 limits 围栏（定义不是实测）")

    # (c) ★ 核心：不许复述常数。复述一次就多一个会烂的副本，
    #     而副本烂掉时不会报错 —— 这正是 D-034 那一类。
    stray = _stray_decimal(sec)
    check(not stray, "曲面模型一节不许复述常数（唯一来源是横断面测试）",
          f"多出的小数：{stray}")

    # (d) 元测试：往那一节塞一个常数，(c) 必须立刻红。
    #     没有这一条，(c) 只是「我数过一遍」，不是门。
    mutated = spec.replace(_SURF_HEAD, _SURF_HEAD + "\n\n顺手记个 0.0621。", 1)
    check(mutated != spec, "元测试失效：找不到可注入的位置")
    stray2 = _stray_decimal(_surface_section(mutated) or "")
    check(bool(stray2), "元测试失效：塞进常数后竟然没被认出来")


def main() -> int:
    for fn in (sec0, sec1, sec2, sec3, sec4):
        before = len(FAILED)
        fn()
        mark = "✓" if len(FAILED) == before else "✗"
        print(f"  {mark} {fn.__name__:<6} {fn.__doc__.splitlines()[0]}")

    # ★ 每个核对者都必须真的被调用过。没有这一条，一个写错名字的
    #   resolver 会让某条声明永不比对，而输出看起来和全绿一样。
    never = [n for n in RESOLVERS if n not in _CALLED]
    check(not never, "每个核对者都应被调用过", f"从未调用：{never}")

    print()
    print(f"     已比对 {len(set(CHECKED))} 条；已具名豁免 {len(EXCUSED)} 条：")
    for name, why in EXCUSED.items():
        print(f"       · {name} —— {why[:60]}…")
    print()
    if FAILED:
        print(f"  ✗ 声明与实物不符：{len(FAILED)} 条")
        for m in FAILED:
            print(f"      - {m}")
        return 1
    print("  ✓ 声明与实物：全部相符")
    print("     §0 靶子存在 · §1 逐条比对 · §2 元测试 · §3 豁免具名 · §4 定义不复述常数")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
