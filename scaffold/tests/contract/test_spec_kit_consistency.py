#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宪法 ↔ 模板 ↔ plan 的一致性守卫（原则 V 用在**流程自己**身上）。

════════════════════════════════════════════════════════════════════════════
这个套件为什么存在
════════════════════════════════════════════════════════════════════════════

2026-10-13 重跑 005 的 Constitution Check 时发现：那条「**新增原则需进检查表**」
的影响面**根本没有被读到**。查下去是三个连在一起的问题：

  ① 宪法的标题层级不一致：原则 I–V 用 `###`，1.1.0 新加的原则 VI 用 `##`。
     ⇒ 按 `###` 找原则的东西**看不见第六条**。
  ② `plan-template.md` 的检查表位置只写着
     `[Gates determined based on constitution file]` ——
     **一个不列原则的占位符**。所以「新加一条原则」不会在任何 plan 里产生新行。
  ③ **没有任何套件读 `.specify/`**（实测 `scaffold/tests/` 里零命中）。

★★★ 合起来就是原则 V 说的那种东西：「一个永远不会失败的检查，比没有检查更糟」——
    只不过这里的「检查」是**一份没人读的检查表**。
    而它比没有检查更坏：它让人以为已经考虑过了。

════════════════════════════════════════════════════════════════════════════
写这个套件时踩到的第一件事 —— **检查者不许有被检查者的盲区**
════════════════════════════════════════════════════════════════════════════

最自然的写法是 `re.compile(r"^### ([IVX]+)\. ")`。★ 但它会**在 VI 隐形的那段时间里全绿** ——
因为「看不见第六条」正是当时那个 bug。检查者复刻了被检查者的盲区，
于是它证明的不是「六条都在」，而是「按我的找法找到的都在这」。

⇒ 两条措施：
  · 找法写成 `#{2,4}`（层级放宽），**先找全，再单独判层级一致**。
  · `§1b` 的元测试**故意把层级改坏** —— 断言「仍然找得到六条，
    但 `一致` 那一项必须变红」。★ 这条同时证明了层级检查不是摆设。

════════════════════════════════════════════════════════════════════════════
关于 001–004：显式登记，不静默豁免
════════════════════════════════════════════════════════════════════════════

001–004 的 `plan.md` 里 Constitution Check **表体是自由格式**，没有逐原则的行 ——
它们写于模板不列原则的年代，**不能拿今天的模板去判它们罚**，
但那也**不等于它们合规**。所以：登记、写清日期与理由、并**断言这份名单只许缩短**。
★★ 静默跳过四份历史 plan，就是这个仓库反复栽的那一类错
   （「一个只扫一个文件名的名单」）。这里不重复它。
"""

from __future__ import annotations

import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]      # scaffold/
REPO = pathlib.Path(__file__).resolve().parents[3]      # 仓库根

CONSTITUTION = REPO / ".specify" / "memory" / "constitution.md"
PLAN_TMPL = REPO / ".specify" / "templates" / "plan-template.md"
SPEC_TMPL = REPO / ".specify" / "templates" / "spec-template.md"
SPECS = REPO / "specs"

#: ★★ 001–004 的 Constitution Check 写于模板不列原则的年代
#:    （那时位置里只有 `[Gates determined based on constitution file]`）。
#:    表体是自由格式、没有逐原则的行 —— **不假装它们合规**，显式登记。
#:    ★ 冻结规则：**这份名单只许缩短**。某个 feature 补上了逐原则的行，
#:      就必须把它从这里删掉；`§4c` 会主动把「该删的」指出来。
#:    ★ 值=理由（会打印出来，所以必须说清而不是写「legacy」）。
_LEGACY_NO_PRINCIPLE_ROWS = {
    "001-empty-table-gap":
        "写于 .specify 引入之前的自由格式表体",
    "002-station-baseline-geometry":
        "写于 .specify 引入之前的自由格式表体",
    "003-local-terrain-viewer":
        "写于 .specify 引入之前的自由格式表体",
    "004-region-frame":
        "自由格式表体（本仓最早按 speckit 流程走的 feature，但那时模板还不列原则）",
}

_ROMAN = re.compile(r"^#{2,4}\s+([IVX]+)\.\s+(.+?)\s*$")
_ROMAN_IN_ROW = re.compile(r"^\|\s*\*\*([IVX]+)\.")


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def parse_principles(text: str) -> tuple[list[tuple[str, int, str]], dict]:
    """宪法 → [(罗马数字, 标题层级, 名称)]，以及元信息。

    ★ 只扫到 `## 接入约束` 为止（按**名字**截断，不按层级 ——
      层级本身正是可能出错的东西，不能拿它做边界）。
    """
    lines = text.splitlines()
    stop = len(lines)
    for i, ln in enumerate(lines):
        if ln.startswith("## ") and "接入约束" in ln:
            stop = i
            break
    out: list[tuple[str, int, str]] = []
    for ln in lines[:stop]:
        m = _ROMAN.match(ln)
        if m:
            out.append((m.group(1), len(ln) - len(ln.lstrip("#")), m.group(2)))
    info = {
        "levels": sorted({lv for _, lv, _ in out}),
        "consistent": len({lv for _, lv, _ in out}) <= 1,
    }
    return out, info


def rows_of_tables(text: str) -> list[str]:
    """抓手写表格里以罗马数字打头的**行标签**。"""
    return [m.group(1) for ln in text.splitlines()
            if (m := _ROMAN_IN_ROW.match(ln))]


def main() -> int:
    fails: list[str] = []

    def check(ok, msg, detail=""):
        print(f"  {'✓' if ok else '✗'} {msg}{('  ' + detail) if detail else ''}")
        if not ok:
            fails.append(msg)

    # ══════════════════════════════════════════════════ 1) 宪法：原则清单
    print("=== 1) 宪法：原则清单立得住 ===")
    con = _read(CONSTITUTION)
    pris, info = parse_principles(con)
    ids = [p[0] for p in pris]
    check(len(pris) >= 6,
          f"宪法里找到 {len(pris)} 条原则（≥6）",
          "、".join(f"{i}. {n[:12]}" for i, _, n in pris))
    check("VI" in ids,
          "★★ 原则 VI **在找法里看得见**（1.1.1 修的就是「它看不见」这件事）",
          f"找到：{ids}")
    check(info["consistent"],
          "★ 六条原则的**标题层级一致**（不一致会让按层级找的东西漏掉那一条）",
          f"出现的层级：{info['levels']}")

    # ---- 1b) ★ 元测试：把层级改坏，必须**仍然找得到六条**，但一致性要变红 ----
    # ★★ 这一条是写给「检查者的盲区」的：如果找法写成 `^### `，
    #    改坏层级之后第六条会**直接消失**，而那时候 `一致` 反而是 True ——
    #    于是在 VI 隐形的那段时间里，这个套件会全绿。
    _bad = con.replace("### VI. ", "## VI. ", 1)
    _bad_pris, _bad_info = parse_principles(_bad)
    check(len(_bad_pris) == len(pris) and "VI" in [p[0] for p in _bad_pris],
          "★★ 1b · ★元测试：把 `### VI.` 改回 `## VI.`，第六条**仍然找得到**"
          "（找法不能复刻被检查者的盲区）",
          f"找坏文本得到 {len(_bad_pris)} 条")
    check(not _bad_info["consistent"],
          "★★ 1b · ★元测试：同一份坏文本，**层级一致性必须变红**"
          "（否则层级检查是摆设）",
          f"坏文本层级：{_bad_info['levels']}")

    # ---- 1c) 版本号 ↔ 修订记录：不许只升号不记账，也不许记了不升 ----
    _vm = re.search(r"\*\*Version\*\*:\s*([0-9]+\.[0-9]+\.[0-9]+)", con)
    _ver = _vm.group(1) if _vm else None
    _entries = re.findall(r"^###\s+([0-9]+\.[0-9]+\.[0-9]+)\s+—", con, re.M)
    check(_ver is not None and _ver in _entries,
          "★ 1c · 头部版本号在修订记录里有对应条目（升了号不记账 = 没人知道改了什么）",
          f"头部 {_ver} ｜ 记录里有 {_entries}")

    # ══════════════════════════════════════════════════ 2) 模板：逐原则一行
    print("\n=== 2) 模板：新原则必须在新 plan 里产生新行 ===")
    tmpl = _read(PLAN_TMPL)
    _tmpl_ids = set(rows_of_tables(tmpl))
    _missing_t = [i for i in ids if i not in _tmpl_ids]
    check(not _missing_t,
          "★★ `plan-template.md` 的门禁表**逐条列出了宪法里的每一条原则**",
          f"模板有 {sorted(_tmpl_ids)}" +
          (f"｜★ 缺：{_missing_t}" if _missing_t else ""))
    check("[Gates determined based on constitution file]" not in tmpl,
          "★★ 那个不列原则的占位符**已被删掉**（它就是根因）",
          "占位符还在的话，新原则不会在任何 plan 里产生新行")

    # ---- 2b) ★ 元测试：从模板里删掉一条原则的行 → 必须报缺 ----
    _cut = re.sub(r"^\|\s*\*\*VI\..*$", "", tmpl, count=1, flags=re.M)
    _cut_ids = set(rows_of_tables(_cut))
    check("VI" not in _cut_ids,
          "★ 2b · ★元测试：把 VI 那一行从模板里删掉 → 缺项判定必须报出 VI"
          "（否则 §2 永远绿）",
          f"删后模板有 {sorted(_cut_ids)}")

    # ---- 2c) spec 模板也要有原则 VI 的入口 ----
    st = _read(SPEC_TMPL)
    check("通用商业化" in st and "VI" in st,
          "★ `spec-template.md` 有「通用性与商业化」一节（原则 VI 的三问）",
          f"{len(st)} 字符")

    # ══════════════════════════════════════════════════ 3) plan：逐原则一行
    print("\n=== 3) 各 feature 的 `plan.md`：Constitution Check 逐条覆盖 ===")
    plans = sorted(SPECS.glob("*/plan.md"))
    check(bool(plans), "找到 feature 的 plan.md", f"{len(plans)} 份")
    for p in plans:
        name = p.parent.name
        t = _read(p)
        got = set(rows_of_tables(t))
        miss = [i for i in ids if i not in got]
        if name in _LEGACY_NO_PRINCIPLE_ROWS:
            check(True,
                  f"· {name} —— **登记为历史格式**，不判罚",
                  _LEGACY_NO_PRINCIPLE_ROWS[name])
        else:
            check(not miss,
                  f"★★ {name} 的 Constitution Check 覆盖全部 {len(ids)} 条原则",
                  f"有 {sorted(got)}" + (f"｜★ 缺：{miss}" if miss else ""))

    # ---- 3b) ★ 清单完整性：每一份 plan 都有归属，没有「没被注意到」的 ----
    _known = {p.parent.name for p in plans}
    _orphan = sorted(_known - set(_LEGACY_NO_PRINCIPLE_ROWS) -
                     {p.parent.name for p in plans if set(rows_of_tables(_read(p)))})
    check(not _orphan,
          "★★ 3b · 清单完整性：每份 plan 要么逐原则覆盖、要么**在历史名单里**",
          f"没归属的：{_orphan}" if _orphan else f"查了 {len(_known)} 份 plan")

    # ---- 3c) ★ 历史名单只许缩短：该删的必须被指出来 ----
    _should_drop = []
    for nm in sorted(_LEGACY_NO_PRINCIPLE_ROWS):
        p = SPECS / nm / "plan.md"
        if p.exists() and set(rows_of_tables(_read(p))):
            _should_drop.append(nm)
    check(not _should_drop,
          "★★ 3c · 历史名单**只许缩短**：已补上逐原则行的 feature 必须从名单里删掉",
          f"该删的：{_should_drop}" if _should_drop else
          f"名单 {len(_LEGACY_NO_PRINCIPLE_ROWS)} 项，暂无该删的")
    _stale = [nm for nm in sorted(_LEGACY_NO_PRINCIPLE_ROWS)
              if not (SPECS / nm / "plan.md").exists()]
    check(not _stale,
          "★ 3c · 历史名单里没有指向已不存在 feature 的死项",
          f"死项：{_stale}" if _stale else "无死项")

    # ══════════════════════════════════════════════════ 4) 词汇：一致性
    print("\n=== 4) 交叉一致性 ===")
    # ★★ 同一份原则清单被三个地方引用（宪法 / 模板 / plan）。
    #    这里只断「**条数**与**编号**一致」，不断措辞 ——
    #    措辞会随 feature 变，编号不会。
    _names_in_tmpl = re.findall(r"^\|\s*\*\*[IVX]+\.\s*([^|*]+?)\s*\*\*", tmpl, re.M)
    check(len(_names_in_tmpl) >= len(ids),
          "★ 模板表里每一条原则都带了名字（不是光一个罗马数字）",
          f"模板 {len(_names_in_tmpl)} 个名字 ｜ 宪法 {len(ids)} 条")

    # ---- 4b) ★★ 模板里那条原则**展开的子条款数**必须与宪法一致 ----
    # ★★★ 2026-10-13 加 VI.4 时当场撞上的：`plan-template.md` 的 VI 行把原则
    #    展开成 `①②③`，宪法加了第 4 条之后这行就**过期了** —— 而 §2 只读行标签
    #    `VI`，**不读内容**，所以它**不会红**。
    #    ⇒ 又是一次「手工维护的展开，配一个只读标签的检查」。
    #    ★ 这一条就是原则 VI.4 的**首个通用落地**：模板与宪法是两份各自
    #      手工维护的东西，光「条数对得上」（§2）不构成证据，
    #      还要「**展开的内容**对得上」。
    _CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"

    def _vi_body(text: str) -> str:
        """取宪法里原则 VI 的正文（到下一个 `###`/`##` 标题为止）。"""
        m = re.search(r"^### VI\..*?$", text, re.M)
        if not m:
            return ""
        rest = text[m.end():]
        nxt = re.search(r"^(?:###|##) ", rest, re.M)
        return rest[:nxt.start()] if nxt else rest

    def _tmpl_row(text: str, pid: str) -> str:
        for ln in text.splitlines():
            if re.match(rf"^\|\s*\*\*{pid}\.\s", ln):
                return ln
        return ""

    _con_n = len(re.findall(r"^(\d+)\.\s", _vi_body(con), re.M))
    _row_vi = _tmpl_row(tmpl, "VI")
    _row_n = sum(1 for ch in _row_vi if ch in _CIRCLED)
    check(_con_n >= 3,
          "★ 4b · 前置：宪法原则 VI 的正文里数到了编号条款（否则下一条是空转）",
          f"宪法 VI 有 {_con_n} 条")
    check(_row_n == _con_n,
          "★★ 4b · 模板的 VI 行**展开的子条款数 == 宪法 VI 的子条款数**"
          "（只对标签不对内容的话，加了子条款也没人知道）",
          f"模板展开 {_row_n} 条 ｜ 宪法 {_con_n} 条")

    # ★ 元测试：把模板 VI 行的最后一条删掉 → 计数必须不等
    _cut_row = re.sub(r"[④⑤⑥⑦⑧⑨⑩]", "", _row_vi, count=1)
    _cut_n = sum(1 for ch in _cut_row if ch in _CIRCLED)
    check(_cut_n != _con_n,
          "★★ 4b · ★元测试：从模板 VI 行删掉一个子条款 → 计数必须变红"
          "（否则 §4b 是摆设）",
          f"删后 {_cut_n} vs 宪法 {_con_n}")

    # ══════════════════════════════════════════════════ 5) 不许把套件数写成判据
    print("\n=== 5) 规范性文档里不许出现写死的套件数 / 通过数 ===")
    # ★★★ 为什么这一条值得单列：本仓有一个**反复复发**的毛病 ——
    #    把某一个时刻的套件数写进文档，当成判据。实测过的复发点：
    #    宪法 §I（「当前 N 套件」）、宪法工作流第 1 条（「通过 N ｜…」）、
    #    005 plan 的 Testing 行、001 的 SC-005、001/004 的 quickstart 期望。
    #    ★★ 症状完全一样：**数字必然过期，而没有任何检查在读它**。
    #    所以判据只能是「失败 0 ｜ 跳过 0」，不是某个通过数。
    #
    # ★ 覆盖面**故意只限「规范性文档」**：宪法、模板、以及各 feature 的
    #   plan / spec / quickstart —— 这些是**面向未来**的规则与判据。
    # ★ 不扫 research.md / tasks.md / 工单-*.md / checklists/ / scaffold 的说明文档：
    #   那些是**带日期的历史记录**（「2026-10-11 实测通过 N」），
    #   把当时的事实改成今天的事实，等于**篡改记录**。
    #   ⇒ 这个取舍是刻意的，不是漏扫。
    _norm = [CONSTITUTION, PLAN_TMPL, SPEC_TMPL]
    _norm += sorted(SPECS.glob("*/plan.md"))
    _norm += sorted(SPECS.glob("*/spec.md"))
    _norm += sorted(SPECS.glob("*/quickstart.md"))
    _cnt = re.compile(r"\d+\s*套件|套件数\s*[:：]?\s*\d+|通过\s*\d+"
                      r"|契约测试\s*\d+/\d+|\d+/\d+\s*全绿")
    _offend: list[str] = []
    for p in _norm:
        for i, ln in enumerate(_read(p).splitlines(), 1):
            if _cnt.search(ln):
                _offend.append(f"{p.relative_to(REPO)}:{i} {ln.strip()[:60]}")
    check(not _offend,
          f"★ 5 · {len(_norm)} 份规范性文档里没有写死的套件数 / 通过数",
          ("★ 命中：" + " ｜ ".join(_offend[:4])) if _offend
          else f"扫了 {len(_norm)} 份，0 命中")

    # ---- 5b) ★★ 元测试：这个探测器**能**变红 -----------------------------
    # ★★★ 没有这一条，§5 就是一个「从来没红过的检查」—— 而它守的正是
    #     「写死的数字没人读」这件事。**守卫自己不能是没人读的那一个。**
    _fake = [
        "**机器怎么强制**：`run_contract_tests.sh` 总运行器（当前 21 套件，",
        "**期望**：`通过 21 ｜ 失败 0 ｜ 跳过 0`",
        "- **SC-005**：契约测试 12/12 保持全绿，新增判定配元测试。",
        "| 契约测试 | 14/14 全绿（`design-导入` 637 条断言） |",
    ]
    _caught = [s for s in _fake if _cnt.search(s)]
    check(len(_caught) == len(_fake),
          f"★★ 5b · ★元测试：那 {len(_fake)} 种**真实复发过的**写法，探测器全部抓得住",
          f"抓到 {len(_caught)}/{len(_fake)}" +
          (f"｜漏：{[s for s in _fake if not _cnt.search(s)]}" if len(_caught) != len(_fake) else ""))
    _clean = [
        "当前基线 **失败 0 ｜ 跳过 0**（★ 不在这里写通过数）",
        "实测 `SRID = 带号 + 4509`、`21/21 无反例`",   # ★ 不是套件数，不许误伤
        "`monitor_cross_section.lat/lon` 全空（0/3 行有值）",  # ★ 不是套件数
        "332/332 桩号落在单元链内",                    # ★ 不是套件数
    ]
    _false = [s for s in _clean if _cnt.search(s)]
    check(not _false,
          "★★ 5b · ★元测试：**不得误伤** —— 「21/21 无反例」「0/3 行有值」"
          "「332/332 桩号」都不是套件数",
          f"误伤：{_false}" if _false else "四条干净样例全部放行")

    print("\n" + "=" * 72)
    if fails:
        print(f"失败 {len(fails)} 项：")
        for f in fails:
            print(f"  · {f}")
        print("=" * 72)
        return 1
    print("全部通过")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
