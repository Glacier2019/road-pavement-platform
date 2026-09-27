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
        if f.name == "gen_ddl_v03.py":
            continue
        for ln, txt in _code_lines(f):
            for rx, why in _FORBIDDEN:
                if rx.search(txt):
                    offenders.append(f.name + ":" + str(ln) + " " + why)
    ok("生成脚本里零硬编码表数/DDL 版本", not offenders, " | ".join(offenders[:5]))
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