#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""契约⑤：交付物口径 —— 图件/文档里宣称的表数，必须与真源一致。

======================= 为什么需要这个测试 =======================

2026-09 实测：DDL v0.3（42 表）时代的图件和文档，在 DDL 走到 v0.5
（62 表）之后**没有任何东西会红** —— 图里印「物理 42 表」，实库 62。

差 20 张表，全绿。这是本仓库「反空转」原则最典型的反面案例：
**没有任何机制会失败，所以漂移不会被发现。**

根因：表数是**字面量**，散落在生成脚本与文档里。
改图 = 改字面量；验证 = 用眼睛看。两者都会漏。

======================= 本测试钉什么 =======================

  A. 生成脚本里不许再出现硬编码的表数 / DDL 版本
  A2. 生成脚本里声明的**域集合**必须等于真源 catalog.py 的域集合
      （A 的三条正则只防「写成字面量」，防不住「把整个旧模型结构化写死」——
       见工单 #4 与下方 _declared_domains 的说明）
  B. 文档里出现的表数口径，必须等于真源
  C. 元测试：造一份写着旧口径的假文档，B 的判据必须认出它
"""
from __future__ import annotations

import pathlib
import re
import sys

# 本文件在 scaffold/tests/contract/ 下：parents[0]=contract, [1]=tests, [2]=scaffold, [3]=仓库根
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "docpipe" / "scripts"))
import contract_truth as ct          # noqa: E402

_fails: list[str] = []


def ok(what: str, cond: bool, detail: str = "") -> None:
    mark = "  OK  " if cond else "  FAIL"
    line = mark + " " + what
    if detail and not cond:
        line += "  [" + detail + "]"
    print(line)
    if not cond:
        _fails.append(what)


_GEN = sorted((ROOT / "docpipe" / "scripts").glob("gen_*.py"))
_FORBIDDEN = [
    (re.compile(r"物理\s*\d+\s*表"), "硬编码物理表数"),
    (re.compile(r"10_ddl_v\d+\.\d+\.sql"), "硬编码 DDL 文件名"),
    (re.compile(r"EXPECTED_TABLES\s*=\s*\d+"), "硬编码期望表数"),
]

#: 按定义不适用「从真源读」的脚本。**每一条都必须写明理由**，
#: 且下面有元测试钉住名单长度 —— 它只能变短，不能悄悄变长。
#:
#: ⚠ 本常量是为修一个真实空洞而立的：原先这行豁免是**内联**在循环里的
#:   （if f.name == "gen_ddl_v03.py": continue），不可见、不可证伪 ——
#:   没有测试钉住「名单只有一个」，下次有人想放过一个有问题的脚本，
#:   加一行 continue 就行了。提成常量后，元测试能认出内联豁免。
_EXEMPT: dict[str, str] = {
    "gen_ddl_v03.py": "工单 #2 的一次性实施脚本，产物即今日 DDL；不改它",
    "gen_er.py": "设计文档版：记录当年的完整设计意图（含未落地的 FA/SA/SE 与 QU），"
                   "不是「过期的现状」。改内容等于销毁证据 —— 见工单 #4 §4.1。"
                   "文件头已加醒目标注。",
}

#: 真源域集合。取自 catalog.py（域分组唯一真源），**不写死** ——
#: 写死的话，本检查自己就会变成下一个「硬编码旧模型」。
def _truth_domains() -> set[str]:
    import importlib.util
    cat = ROOT / "scaffold" / "modules" / "M3-rpdao" / "rpdao" / "catalog.py"
    spec = importlib.util.spec_from_file_location("_cat_probe", cat)
    mod = importlib.util.module_from_spec(spec)
    # catalog.py 只依赖标准库与 NamedTuple，可直接执行
    spec.loader.exec_module(mod)
    return set(mod.DOMAINS)


#: 从脚本源码里取出**声明的域代号**。
#:
#: 为什么不用更严的「必须来自 catalog」：gen_er.py 这类「设计文档版」
#: 有意声明了尚未落地的域，那正是它要记录的东西。本检查抓的是
#: **声明与真源不一致却无人知晓**，而不是禁止声明历史域。
#: 不一致的处置方式（进豁免 / 改内容）由 _EXEMPT 与工单决定。
_DOMAIN_LITERAL = re.compile(r"'([A-Z][A-Z])':")


def _declared_domains(path: pathlib.Path) -> set[str]:
    """脚本里声明的域代号集合。空集 = 该脚本不表达域模型（适用性判断，非豁免）。"""
    return set(_DOMAIN_LITERAL.findall(path.read_text(encoding="utf-8", errors="replace")))


#: 内联豁免的形状：循环里按文件名 continue。提成 _EXEMPT 后，
#: 源码里再出现这种写法就该被元测试抓住。
_INLINE_SKIP = re.compile(r"f\.name\s*==\s*['\"]([^'\"]+)[^\n]*\n[^\n]*continue")


def _code_lines(path: pathlib.Path):
    """只取代码行：剥掉注释（注释里讲历史是正当的）。"""
    out = []
    for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if ln.lstrip().startswith("#"):
            continue
        out.append((i, ln.split("#")[0]))
    return out


#: 明确标注为「历史/当时口径」的行不判 —— 但**必须留下标记**，
#: 否则谁都说不清那个旧数是笔误还是有意保留。标记词是白名单的全部：
#: 写不出标记的行，就得改成真源口径。
_HIST_MARK = re.compile(r"当时|成稿时|v\d+\.\d+ 成稿|历史|演进|原为|旧口径|⚠")


def _scan_line(ln: str, truth: int, ver: str, rx_tbl, rx_ddl) -> bool:
    """这一行是否与真源口径冲突。B 与 C 共用同一判据 —— 避免两套口径。"""
    if not re.search(r"DDL|物理|真源|契约", ln):
        return False
    if _HIST_MARK.search(ln):
        return False
    for m in rx_tbl.finditer(ln):
        if int(m.group(1)) != truth and ("物理" in ln or "DDL" in ln):
            return True
    for m in rx_ddl.finditer(ln):
        if m.group(1) != ver.lstrip("v"):
            return True
    return False


#: 这些文档**按定义**就是历史记录（工单/QA 流水/版本变迁），
#: 它们的价值在于"当时是什么"，改掉反而销毁证据。
#: 判据：文件名含"工单"或"QA记录"，或正文自带版本变迁小节。
#: ⚠ 这是**白名单而非豁免**：白名单之外的文档一个都不放过，
#:   且下面有一条元测试钉住"白名单不许无限膨胀"。
_HISTORICAL = ("工单", "QA记录", "契约变更")


def _is_historical(path: pathlib.Path) -> bool:
    return any(k in path.name for k in _HISTORICAL)


def main() -> int:
    truth = ct.physical_tables()
    ver = ct.ddl_version()
    print("=== A) 生成脚本必须从真源读 ===")
    print("  真源：物理 " + str(truth) + " 表，DDL " + ver + "（" + ct.ddl_path_label() + "）")
    offenders = []
    for f in _GEN:
        if f.name in _EXEMPT:      # 显式豁免，见 _EXEMPT 的注释
            continue
        for ln, txt in _code_lines(f):
            for rx, why in _FORBIDDEN:
                if rx.search(txt):
                    offenders.append(f.name + ":" + str(ln) + " " + why)
    ok("生成脚本里零硬编码表数/DDL 版本", not offenders, " | ".join(offenders[:5]))
    # A2: 声明的域集合必须与真源一致 —— 这条抓的是「把整个旧模型结构化写死」，
    #     那类写法不含任何字面量总数，上面三条正则永远抓不到。
    truth_doms = _truth_domains()
    dom_offenders = []
    for f in _GEN:
        if f.name in _EXEMPT:
            continue
        declared = _declared_domains(f)
        if declared and declared != truth_doms:
            extra = sorted(declared - truth_doms)
            missing = sorted(truth_doms - declared)
            dom_offenders.append(f.name + " 多 " + str(extra) + " 少 " + str(missing))
    ok("生成脚本声明的域集合等于真源（" + str(len(truth_doms)) + " 域）",
       not dom_offenders, " | ".join(dom_offenders[:5]))
    wired = [f.name for f in _GEN if "contract_truth" in f.read_text(encoding="utf-8")]
    ok("至少 3 个生成脚本已接入 contract_truth", len(wired) >= 3, str(wired))

    print("")
    print("=== B) 文档里宣称的表数必须等于真源 ===")
    rx_tbl = re.compile(r"(?:物理\s*)?(\d+)\s*表")
    rx_ddl = re.compile(r"10_ddl_v(\d+\.\d+)\.sql")
    all_docs = [ROOT / "README.md"] + sorted((ROOT / "output").glob("*.md"))
    docs = [d for d in all_docs if not _is_historical(d)]
    skipped = [d for d in all_docs if _is_historical(d)]
    bad = []
    for doc in docs:
        txt = doc.read_text(encoding="utf-8", errors="replace")
        for ln in txt.splitlines():
            if _scan_line(ln, truth, ver, rx_tbl, rx_ddl):
                bad.append(doc.name + ": " + ln.strip()[:88])
    ok("扫描 " + str(len(docs)) + " 份现口径文档（README + output/*.md）", len(docs) > 8)
    ok("现口径文档表数全部等于真源", not bad, " | ".join(bad[:6]))
    # 白名单必须**真的只是历史记录**：占比过高说明有人在拿它当避风港
    ok("历史记录白名单未膨胀（<= 1/3 文档）",
       len(skipped) * 3 <= len(all_docs),
       "跳过 " + str(len(skipped)) + "/" + str(len(all_docs)) + "：" + str([d.name for d in skipped]))

    print("")
    print("=== C) 元测试：这些检查真的会红吗 ===")
    fake = "| sql | 契约② 真源：10_ddl_v0.3.sql（物理 42 表） |"
    hit = any(_scan_line(ln, truth, ver, rx_tbl, rx_ddl) for ln in fake.splitlines())
    ok("元测试：写着旧口径的文档会被认出（不是摆设）", hit)
    good = "契约② 真源：" + ct.ddl_path_label() + "（物理 " + str(truth) + " 表）"
    false_alarm = any(_scan_line(ln, truth, ver, rx_tbl, rx_ddl) for ln in good.splitlines())
    ok("元测试：正确口径不会被误报（不狼来了）", not false_alarm, good)
    # C3: 白名单只认历史记录 —— 一份普通的现口径文档不许被放过
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".md", prefix="架构与模块说明", delete=False) as tmp:
        probe = pathlib.Path(tmp.name)
    try:
        ok("元测试：白名单只认历史记录（现口径文档不会被放过）", not _is_historical(probe))
        ok("元测试：白名单认得工单与 QA 记录",
           _is_historical(pathlib.Path("x/契约变更工单-01.md")) and _is_historical(pathlib.Path("x/架构图-图件契约与QA记录.md")))
    finally:
        probe.unlink(missing_ok=True)

    # ---- A 的元测试（工单 #4 §3.3）：补上原先的覆盖缺口 ----
    # 断言 C 原有 4 条全都测文档侧，**断言 A 一条元测试都没有** ——
    # 没有任何测试证明「硬编码旧模型的脚本真的会被抓出来」。
    import tempfile as _tf
    truth_doms = _truth_domains()

    # A-1/A-2：用**真源域**加减构造，不写死 11/7 ——
    #         写死的话，将来域集合一变，元测试自己就失效了。
    def _mkdom(domset, prefix):
        body = "D = {}\n" + "".join("'" + d + "': 1,\n" for d in sorted(domset))
        t = _tf.NamedTemporaryFile(suffix=".py", prefix=prefix, mode="w", delete=False)
        t.write(body); t.close()
        return pathlib.Path(t.name)

    probe_big = _mkdom(truth_doms | {"ZZ", "YY"}, "gen_probe_big_")
    probe_ok = _mkdom(truth_doms, "gen_probe_ok_")
    # A-3：含内联豁免的假脚本 —— 提成 _EXEMPT 后这种写法必须被认出
    t3 = _tf.NamedTemporaryFile(suffix=".py", prefix="gen_probe_skip_", mode="w", delete=False)
    t3.write("for f in _GEN:\n    if f.name == " + repr("gen_sneaky.py") + ":\n        continue\n")
    t3.close()
    probe_skip = pathlib.Path(t3.name)
    try:
        d_big = _declared_domains(probe_big)
        ok("元测试-A1：声明域 ≠ 真源的脚本被认出（不是摆设）",
           bool(d_big) and d_big != truth_doms,
           "多出 " + str(sorted(d_big - truth_doms)))
        d_ok = _declared_domains(probe_ok)
        ok("元测试-A2：声明域 == 真源的脚本不被误报（不狼来了）",
           d_ok == truth_doms, str(sorted(d_ok)))
        m = _INLINE_SKIP.search(probe_skip.read_text(encoding="utf-8"))
        ok("元测试-A3：内联的文件名豁免被认出（工单 #4 §2.3 那个空洞）",
           m is not None and m.group(1) == "gen_sneaky.py",
           (m.group(1) if m else "未匹配"))
        # A-4：豁免名单不许增长 —— 防「用豁免换绿」。
        #      没有它，_EXEMPT 自己会变成下一个内联 continue。
        ok("元测试-A4：豁免名单未膨胀（只许变短）", len(_EXEMPT) <= 2,
           "当前 " + str(len(_EXEMPT)) + " 条：" + str(sorted(_EXEMPT)))
        # A-5：真源不写死 —— 检查代码本身不许出现域集合字面量。
        own = pathlib.Path(__file__).read_text(encoding="utf-8")
        hard = re.search(r"\{[^}]*'GE'[^}]*\}", own)
        ok("元测试-A5：真源域取自 catalog.py，未写死", hard is None,
           (hard.group(0)[:60] if hard else ""))
    finally:
        probe_big.unlink(missing_ok=True)
        probe_ok.unlink(missing_ok=True)
        probe_skip.unlink(missing_ok=True)

    print("")
    if _fails:
        print("失败 " + str(len(_fails)) + " 项：")
        for x in _fails:
            print("   - " + x)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())