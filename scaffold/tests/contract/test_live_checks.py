#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
非契约检查的清单与运行器 —— 把「在我这台机器上能跑」变成「在别人那台机器上也能跑」。

钉住三件事：

  1) **清单本身立得住**：四字段、名字唯一、命令指的文件真的在。

  2) ★ **活栈检查不许把地址写死**，且**必须把「没跑成」和「没过」分开**。
     这两条是把检查交给别人跑的前提，也正是它们此前最不一致的地方 ——
     同一个目录下，一个读环境变量、两个写死；退出码一个用 2、两个用 1。

  3) ★★ **运行器的三态实测**：临时清单 + 三个假检查（退 0 / 1 / 2），
     看运行器是不是真的报「通过 / 失败 / 没跑成」、退出码是不是 0 / 1 / 2。
     —— 读源码证明不了这个，跑一遍才行。

为什么要有这道门：
  test_console.py 的第 0 组管「页面解析得过」。这一道管「检查本身站得住」。
  两件都是**一类缺陷**的门，不是某一个 bug 的门。

★ 本组的边界（如实说）：
  「地址没写死」用的是**行级**判定 —— 出现地址字面量的那一行，要么同
  行有 CHECK_BASE_URL（即它是那个表达式的兜底值），要么它是注释行。
  这不是完整的 JS 解析，能抓住真实写法，抓不住刻意绕过。
  另一半（真的换地址跑得通）由第 5 组**实测**补上。

跑法（在 scaffold/ 下）：
    export PYTHONPATH="$PWD/modules/_shared${PYTHONPATH:+:$PYTHONPATH}"
    uv run python tests/contract/test_live_checks.py
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]      # scaffold/
MANIFEST = ROOT / "checks.tsv"
RUNNER = ROOT / "run_checks.sh"

_fails = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global _fails
    mark = "✓" if cond else "✗"
    line = f"  {mark} {name}"
    if detail and not cond:
        line += f"　{detail}"
    print(line)
    if not cond:
        _fails += 1


def section(title: str) -> None:
    print(f"\n{title}")


# ============================================================================
section("=== 0) 靶子存在（后面每条判断的前提）===")

ok("清单在", MANIFEST.is_file(), str(MANIFEST))
ok("运行器在", RUNNER.is_file(), str(RUNNER))

_node = shutil.which("node")
# ★ 缺 node 判红，不跳过 —— 与 test_console.py 第 0 组同一个立场：
#   装不了而悄悄放行，就是本仓反复栽的那类假绿。
ok("环境里有 node（运行器要它跑 .mjs 检查）", _node is not None, "PATH 里找不到 node")

if not MANIFEST.is_file():
    print("\n结果：清单都不在，后面的判断无从谈起 ✗")
    sys.exit(1)

# ---------------------------------------------------------------- 读清单
# 字段以竖线分隔；空字段要保得住，所以不用 TAB（原因见清单里的注释）。
_rows: list[tuple[int, str, str, str, str]] = []
_bad_shape: list[str] = []
for _i, _raw in enumerate(MANIFEST.read_text(encoding="utf-8").splitlines(), 1):
    _s = _raw.strip()
    if not _s or _s.startswith("#"):
        continue
    _parts = [p.strip() for p in _raw.split("|")]
    if len(_parts) != 4:
        _bad_shape.append(f"第 {_i} 行有 {len(_parts)} 个字段（应为 4）")
        continue
    _rows.append((_i, *_parts))

ok("清单每行都是四字段", not _bad_shape, "；".join(_bad_shape[:3]))
ok("清单里有检查（不是空表）", len(_rows) >= 1, f"读到 {len(_rows)} 条")

# ============================================================================
section("=== 1) 清单本身立得住 ===")

_names = [r[1] for r in _rows]
_dups = sorted({n for n in _names if _names.count(n) > 1})
ok("名字唯一", not _dups, f"重名：{_dups}")

_missing_cmd = [r[1] for r in _rows if not r[4]]
ok("每条都有命令", not _missing_cmd, f"没有命令：{_missing_cmd}")

_no_desc = [r[1] for r in _rows if not r[2]]
ok("每条都写了「证明什么」", not _no_desc, f"没写说明：{_no_desc}")

_missing_file: list[str] = []
for _ln, _n, _d, _need, _cmd in _rows:
    _target = _cmd.split()[0]
    _full = pathlib.Path(_target) if _target.startswith("/") else (ROOT.parent / _target)
    if not _full.is_file():
        _missing_file.append(f"{_n} → {_target}")
ok("每条命令指的文件真的在", not _missing_file, "；".join(_missing_file[:3]))

# ============================================================================
section("=== 2) ★ 活栈检查不许把地址写死 ===")
# 规则：出现地址字面量的那一行，要么**同行**有 CHECK_BASE_URL（它是那个表达式的
#       兜底值，允许），要么它是**注释行**。其余一律判红。
_ADDR = re.compile(r"127\.0\.0\.1:8024|localhost:8024")
_ENV = "CHECK_BASE_URL"


def _hardcoded(src: str) -> list[str]:
    """返回「把地址写死」的行（行号 + 内容）。"""
    bad = []
    for i, ln in enumerate(src.splitlines(), 1):
        if not _ADDR.search(ln):
            continue
        s = ln.strip()
        if s.startswith("//") or s.startswith("*") or s.startswith("#"):
            continue                      # 注释里提一句地址，可以
        if _ENV in ln:
            continue                      # 兜底值：`process.env.CHECK_BASE_URL || "…"`
        bad.append(f"{i}: {s[:70]}")
    return bad


_live = [(n, c) for (_l, n, _d, need, c) in _rows if need]
ok("清单里有活栈检查（否则本组空转）", len(_live) >= 1, f"带前置的 {len(_live)} 条")

_live_src: dict[str, str] = {}
for _n, _cmd in _live:
    _t = _cmd.split()[0]
    _f = pathlib.Path(_t) if _t.startswith("/") else (ROOT.parent / _t)
    if _f.is_file():
        _live_src[_n] = _f.read_text(encoding="utf-8")

for _n, _src in _live_src.items():
    _bad = _hardcoded(_src)
    ok(f"{_n} 的地址没写死（只从 {_ENV} 来）", not _bad, "；".join(_bad[:2]))

for _n, _src in _live_src.items():
    ok(f"{_n} 确实读 {_ENV}", _ENV in _src, f"源码里找不到 {_ENV}")

# ============================================================================
section("=== 3) ★ 「没跑成」必须与「没过」分开（实测，不是 grep）===")
# 退出码约定：0 过了 / 1 没过 / 2 没跑成。活栈检查必须有退 2 的那条路。
#
# ★★ 这一组原先只 grep 源码里「有没有 process.exit(2)」，被变异证明戳穿了：
#    把**真正**那条前置守卫删掉，文件里另一处无关的 exit(2)（"找不到内联 script"）
#    仍然让本组全绿。凡是只问「这个字符串在不在」的判断，都挡不住这个。
#    ⇒ 改成**真跑**：把上游地址指向一个没人听的端口，看它退不退 2。
#      这是这条约定唯一的直接证据。
#
# ★ 本组不碰真栈：端口 1 上什么都没有，连接立刻被拒，全程不出本机。
_DEAD = "http://127.0.0.1:1"
import os as _os3

for _n, _cmd in _live:
    _t = _cmd.split()[0]
    _f = pathlib.Path(_t) if _t.startswith("/") else (ROOT.parent / _t)
    if not _f.is_file():
        ok(f"{_n} 上游不可达时退 2", False, f"找不到 {_f}")
        continue
    try:
        _env = {**_os3.environ, "CHECK_BASE_URL": _DEAD}
        _env.pop("GEO_BASE", None)
        _p = subprocess.run(["node", str(_f)], capture_output=True, text=True,
                            timeout=120, env=_env)
    except subprocess.TimeoutExpired:
        ok(f"{_n} 上游不可达时退 2", False, "跑超时了 —— 它没有快速失败")
        continue
    ok(f"{_n} 上游不可达时退 2（说「我没跑成」，不是「没过」）",
       _p.returncode == 2, f"实得退出码 {_p.returncode}；{(_p.stdout + _p.stderr)[-120:]}")
    _said = _p.stdout + _p.stderr
    ok(f"{_n} 并且措辞也说「没跑成」", "没跑成" in _said, f"它说的是：{_said[-120:]}")

# 运行器那一侧：它必须把 2 认成跳过，而且**默认算不通过**。
# （这一条是 grep，但它有第 5 组 ② 的实测兜底 —— 见下。）
_runner_src = RUNNER.read_text(encoding="utf-8") if RUNNER.is_file() else ""
ok("运行器把 2 认成跳过", "nskip=$((nskip+1))" in _runner_src)
ok("★ 跳过默认算不通过（要显式 --allow-skip 才放行）",
   "ALLOW_SKIP" in _runner_src and "exit 2" in _runner_src)

# ============================================================================
section("=== 4) ★ 检查不许写死本机绝对路径 ===")
# 与地址写死同一类毛病。仓库根、家目录，写死了换台机器就不成立。
# ★ 例外：清单里的**命令**可以用绝对路径（那是允许装在 /opt 的）。
_HOME_PAT = re.compile(r"/data/cy/shujuku|/home/zhanghe")
_bad_home: list[str] = []
for _n, _src in list(_live_src.items()):
    for i, ln in enumerate(_src.splitlines(), 1):
        if not _HOME_PAT.search(ln):
            continue
        s = ln.strip()
        if s.startswith("//") or s.startswith("*"):
            continue
        _bad_home.append(f"{_n}:{i} {s[:70]}")
# 清单本身只许在注释里出现本机路径
for i, ln in enumerate(MANIFEST.read_text(encoding="utf-8").splitlines(), 1):
    if _HOME_PAT.search(ln) and not ln.strip().startswith("#"):
        _bad_home.append(f"checks.tsv:{i} {ln.strip()[:70]}")
ok("检查源码与清单里没有写死的本机路径", not _bad_home, "；".join(_bad_home[:3]))

# ============================================================================
section("=== 5) ★★ 运行器的三态：实测，不是读源码 ===")

if _node is None:
    ok("跑得动三态实测（需要 node）", False, "没有 node，本组无法实测")
else:
    with tempfile.TemporaryDirectory(prefix="live-checks-") as _td:
        _d = pathlib.Path(_td)
        # 三个假检查：只做一件事 —— 用指定退出码退出。
        for _fn, _rc, _msg in (("pass.mjs", 0, "  假的：过了"),
                               ("fail.mjs", 1, "  假的：没过"),
                               ("skip.mjs", 2, "○ 假的：没跑成")):
            (_d / _fn).write_text(
                f'console.log("{_msg}");\nprocess.exit({_rc});\n', encoding="utf-8")

        def _manifest(entries: list[tuple[str, str, str]]) -> pathlib.Path:
            _m = _d / f"m{len(entries)}{abs(hash(tuple(entries))) % 1000}.tsv"
            _m.write_text(
                "\n".join(f"{n} | 假检查 | {need} | {_d / fn}"
                          for n, need, fn in entries) + "\n", encoding="utf-8")
            return _m

        def _run(entries, *extra):
            _m = _manifest(entries)
            _p = subprocess.run(
                ["bash", str(RUNNER), *extra],
                capture_output=True, text=True, timeout=120,
                env={**_env_base(), "CHECK_MANIFEST": str(_m)},
            )
            return _p

        def _env_base():
            import os
            return dict(os.environ)

        _ALL = [("假过", "", "pass.mjs"),
                ("假败", "", "fail.mjs"),
                ("假跳", "console", "skip.mjs")]

        _p = _run(_ALL)
        ok("① 三个一起跑：退出码 1（有失败就报失败）", _p.returncode == 1,
           f"实得 {_p.returncode}")
        ok("① 报出「没跑成」这个词", "没跑成" in _p.stdout, _p.stdout[-160:])
        ok("① 三种态各自计数", "通过 1" in _p.stdout and "失败 1" in _p.stdout
           and "跳过 1" in _p.stdout, _p.stdout[-160:])

        _p = _run([("假过", "", "pass.mjs"), ("假跳", "console", "skip.mjs")])
        ok("② 只有过 + 跳：退出码 2（跳过默认算不通过）", _p.returncode == 2,
           f"实得 {_p.returncode}")

        _p = _run([("假过", "", "pass.mjs"), ("假跳", "console", "skip.mjs")],
                  "--allow-skip")
        ok("③ 同上但显式放行：退出码 0", _p.returncode == 0, f"实得 {_p.returncode}")

        _p = _run([("假过", "", "pass.mjs")])
        ok("④ 只跑通过那条：退出码 0", _p.returncode == 0, f"实得 {_p.returncode}")

        _p = _run([("假败", "", "fail.mjs")])
        ok("⑤ 只跑失败那条：退出码 1", _p.returncode == 1, f"实得 {_p.returncode}")

        _p = _run([("假过", "", "pass.mjs")], "--list")
        ok("⑥ --list 真的列清单", "假过" in _p.stdout and _p.returncode == 0,
           _p.stdout[-160:])

        # ★★ 元测试：把「没跑成」改判成「通过」，第 2 组/第 5 组必须能认出来。
        #    做法：造一份假清单，指向一个**退 0 但自称没跑成**的检查 ——
        #    这种「嘴上说没跑、身体报成功」的东西，运行器只认退出码，
        #    所以它会报「通过」。这正是为什么要靠**退出码**而不是靠**输出文字**。
        #    这里反向钉住：退出码 2 的必须被判跳过，绝不能被算进通过数。
        _p = _run([("假跳", "console", "skip.mjs")])
        ok("元测试：只跑「退 2」那条，通过数必须是 0",
           "通过 0" in _p.stdout, _p.stdout[-160:])
        ok("元测试：它必须落在「跳过」而不是「通过」",
           "跳过 1" in _p.stdout and _p.returncode == 2, _p.stdout[-160:])

        # ★★ 元测试：把地址写死的源码必须被第 2 组认出来。
        _fake_hard = _d / "hardcoded.mjs"
        _fake_hard.write_text(
            'const BASE = "http://127.0.0.1:8024";\nprocess.exit(0);\n',
            encoding="utf-8")
        _bad = _hardcoded(_fake_hard.read_text(encoding="utf-8"))
        ok("元测试：写死地址的源码会被第 2 组认出来", len(_bad) == 1, f"认出 {len(_bad)} 行")

        # 元测试的另一面：兜底写法**不该**被误判。
        _ok_line = 'const BASE = process.env.CHECK_BASE_URL || "http://127.0.0.1:8024";'
        ok("元测试：兜底写法不被误判", not _hardcoded(_ok_line), "误判了兜底写法")
        ok("元测试：注释里提一句地址不被误判",
           not _hardcoded('// 原先写死 127.0.0.1:8024'), "误判了注释")

# ============================================================================
section("=== 6) 清单和运行器说得一致 ===")
if _node is not None:
    import os as _os
    _p = subprocess.run(["bash", str(RUNNER), "--list"],
                        capture_output=True, text=True, timeout=60,
                        env={**_os.environ, "CHECK_MANIFEST": str(MANIFEST)})
    _listed = [n for n in _names if n in _p.stdout]
    ok("--list 把清单里每一条都列出来", len(_listed) == len(_names),
       f"列了 {len(_listed)} / {len(_names)}")
    ok("--list 退出码 0", _p.returncode == 0, f"实得 {_p.returncode}")

print("\n结果：" + ("全部通过 ✓" if _fails == 0 else f"{_fails} 条不过 ✗"))
sys.exit(1 if _fails else 0)
