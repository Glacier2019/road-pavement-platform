#!/usr/bin/env python3
"""契约测试：**两份手工维护的清单，必须有人校。**

★★ 本文件守的是一个**性质**，不是一个功能：

    凡是「A 声明了要去消费 B」的连接，如果那份清单是**手写的**，
    就必须有一条检查在读它 —— 否则**新增一个 B 不产生任何信号**。

本仓有两份这样的清单，形状完全一样：

    ┌─ §1  运行器清单   run_contract_tests.sh 的 TESTS=()  ←→  tests/**/*.py
    └─ §2  initdb.d 挂载表  docker-compose.skeleton.yml    ←→  sql/*.sql

两份都**没有任何完整性检查**。于是：

  · 测试文件可以存在、被提交、被文档引用，而**没有任何一行会跑它**；
  · sql 文件可以存在、被提交、被别处引用，而**不进全新装**，
    且**没有东西说得清这是有意的还是漏的**。

★★★ 起因（2026-10-13 实测，不是推演）
--------------------------------------------------------------------------------
`tests/contract/test_geometry_solver.py` —— 002 就写好、25 条断言、
docstring 里写着「注释不会失败」的那份 —— **没有登记在运行器里**。
引用它的全是文档。补登记之后又发现更深一层：它读的是
``os.environ["PG_DSN"]`` 而运行器**从不传 PG_DSN**，所以第 5 组永远 `[SKIP]`，
**而第 5 组的 `return fails` 把第 6/7/8 组一并吞掉** ——
八组只跑一半，报告写「全部通过」。

本文件要做的是让**下一个孤儿在提交时就红**，而不是等一年后有人顺手 grep。

★ 这份清单为什么不是靠「约定」维持的
--------------------------------------------------------------------------------
上一轮（宪法 1.1.1）刚吃过一次同样的亏：`plan-template.md` 的门禁表位置
只有一个不列原则的占位符，所以**宪法加了原则、没有任何 plan 会产生新行**，
而这一切**不会报错**。

⇒ 结论是一样的：**一份没人读的清单，等于不存在。**
  所以本文件同时守住「清单完整」和「清单里的例外必须写明理由」两件事。
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]          # scaffold/
REPO = ROOT.parent

RUNNER = ROOT / "run_contract_tests.sh"
COMPOSE = ROOT / "docker-compose.skeleton.yml"

# ---------------------------------------------------------------------------
# 允许表：**不在清单里，但这是有意的** —— 每一条都 MUST 写明理由。
#
# ★ 写法照抄 test_spec_kit_consistency.py 的 _LEGACY_NO_PRINCIPLE_ROWS：
#   名字 → 理由。理由为空 = 这条不算数（§3 会红）。
# ★ 这张表**只许缩短**：§3 拿它跟一份记下来的预算比。
# ---------------------------------------------------------------------------

# 测试文件不在运行器里，且**不应该**在（要活环境、或是共享模块）。
_TESTS_NOT_IN_RUNNER: dict[str, str] = {
    "tests/e2e/quickstart_probe.py":
        "要一个**真跑起来的栈**（console:8024、库、MQTT、容器）。契约运行器的"
        "契约是「离线」，把它塞进去只会得到一行**跳过** —— 而一个要活在真实环境里、"
        "环境不在就跳过的检查，在「全绿」里的读数和「没跑」是一样的。"
        "改由 run_e2e_smoke.sh 跑。见 tests/README.md §e2e。",
}

# sql 文件不在 initdb.d 里，且**不应该**在。
_SQL_NOT_MOUNTED: dict[str, str] = {
    "98_seed_sim_device.sql":
        "★ 它挂靠的路段是**导入流程**建的，容器初始化时还不存在 —— 挂上去只会"
        "**静默插零条**（比不挂更糟：它看起来像做了事）。"
        "见 modules/M2-ingest/README.md「登记脚本…它不挂」。",
    "99_seed_crs_39band.sql":
        "★ 同上一类。它更新的是 `design_project` 里那一行**导入来的**项目 ——"
        "而 `design_project` 在 90_seed_skeleton.sql 里**根本没出现**"
        "（实测 `grep -c` = 0），全新安装的库里**没有这一行**，"
        "所以它同样只会静默更新 0 行。"
        "★★ 这段理由原先**只写在 .sql 文件自己的注释里**，"
        "而没有任何东西会去读它 —— 正是本文件要修的那种失联。",
}

# 允许表规模的预算：**只许缩短**。加了条目必须同时改这里，那一下就是"停一停"。
_TEST_ALLOW_BUDGET = 1
_SQL_ALLOW_BUDGET = 2

# 共享模块（下划线开头）与缓存目录不是测试，不参与 §1。
_NOT_A_TEST = re.compile(r"(^|/)__pycache__/|(^|/)_[^/]*\.py$")

_RUNNER_ROW = re.compile(r'^\s*"([^"|]+)\|([^"|]+)\|([^"|]+)\|([^"|]+)\|([^"]+)"\s*$')
_MOUNT_LINE = re.compile(r'\./sql/([\w.]+\.sql)\s*:\s*/docker-entrypoint-initdb\.d/')


ENV_EXAMPLE = ROOT / ".env.example"

# ★★★★★ 2026-10-13（丁）：compose 引用了 24 个键，而 `.env.example` 只声明 18 个。
#   差的 6 个是 M4/M5/M7/M8/M9/M10 各自的宿主端口（GOVERNANCE/FUSION/SEMANTIC/
#   APPS/CONSOLE/AGENT_PORT），它们在 2026-09-15 的 b9b9df2 里**一次性**进了 compose，
#   而 `.env.example` 自 2026-09-14 的 9935616 起**再没被打开过**。
#
#   ★★ 为什么两年都没人发现：那 6 个全都写了 `:-` 默认值。**不声明照样跑**，
#      所以缺失**不产生任何信号**。带默认值的缺失，从构造上就是隐形的。
#   ★★★ 而 `.env.example` 被 12 份文档引用（含全部 6 个模块的 README，
#      每份都写「首次 cp .env.example .env」）—— **文档在教人用它，它却答不全。**
#
#   ⇒ 这是本文件守的那个性质的**第三份清单**：compose 是需求方，
#     `.env.example` 是说明书，两份都是手写的，中间**没有任何东西在读**。
#
# ★★★★★ 匹配必须**整键**，不能子串 —— 这是实测踩到的坑：
#   子串匹配 `CONSOLE_PORT` 会**顺带命中** `${MINIO_CONSOLE_PORT:-9003}`，
#   而后者明明是**已声明**的，于是会把一个正确的键报成缺失（反向误报）。
#   `(?=[:\}])` 这个边界钉住的就是这件事；§4b 有元测试专门守它。
_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?=[:\}])")
_ENV_DECL = re.compile(r"^([A-Z_][A-Z0-9_]*)\s*=", re.M)


def env_referenced(compose_text: str) -> set[str]:
    """compose 里 `${X}` / `${X:-…}` / `${X:?…}` 引用的键名（整键）。"""
    return set(_ENV_REF.findall(compose_text))


def env_declared(example_text: str) -> set[str]:
    """`.env.example` 里声明的键名。"""
    return set(_ENV_DECL.findall(example_text))


def env_undeclared(compose_text: str, example_text: str) -> list[str]:
    """★ 纯函数：compose 要了、但说明书没写的键。§4b 要喂它捏造的输入。"""
    return sorted(env_referenced(compose_text) - env_declared(example_text))


def _read(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


def registered_files(registry_text: str) -> set[str]:
    """从运行器脚本里取出每一行登记的测试文件路径。"""
    out: set[str] = set()
    for line in registry_text.splitlines():
        m = _RUNNER_ROW.match(line)
        if m:
            out.add(m.group(5).strip())
    return out


def mounted_sql(compose_text: str) -> set[str]:
    """从 compose 里取出真的挂进 initdb.d 的 sql 文件名。"""
    return set(_MOUNT_LINE.findall(compose_text))


def test_files() -> list[str]:
    """tests/ 下所有**测试**文件，仓库相对路径，斜杠统一。"""
    out = []
    for p in sorted((ROOT / "tests").rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if _NOT_A_TEST.search("/" + rel):
            continue
        out.append(rel)
    return out


def sql_files() -> list[str]:
    return sorted(p.name for p in (ROOT / "sql").glob("*.sql"))


def unregistered(all_files: list[str], registry_text: str) -> list[str]:
    """★ 纯函数：给定「文件全集 + 清单文本」，报出清单里没有的。
    做成函数是为了 §1b 能喂它**捏造的**输入 —— 否则元测试无从下手。
    """
    reg = registered_files(registry_text)
    allow = set(_TESTS_NOT_IN_RUNNER)
    return [f for f in all_files if f not in reg and f not in allow]


def unmounted(all_sql: list[str], compose_text: str) -> list[str]:
    """★ 纯函数，同 §1b 的道理。"""
    mounted = mounted_sql(compose_text)
    allow = set(_SQL_NOT_MOUNTED)
    return [s for s in all_sql
            if s not in mounted and "_migrate_" not in s and s not in allow]


def main() -> int:
    fails = 0
    checks = 0

    def check(ok: bool, name: str, detail: str = "") -> bool:
        nonlocal fails, checks
        checks += 1
        print(("  [OK] " if ok else "  [!!] ") + name + (f" —— {detail}" if detail else ""))
        if not ok:
            fails += 1
        return ok

    # =====================================================================
    print("=== 1) 运行器清单：每个测试文件，要么被登记、要么被显式豁免 ===")
    reg_text = _read(RUNNER)
    reg = registered_files(reg_text)
    files = test_files()
    check(len(reg) > 0, "运行器清单解析成功（不是空表）", f"{len(reg)} 行")
    check(len(files) > 0, "tests/ 下扫到了测试文件", f"{len(files)} 个")

    miss = unregistered(files, reg_text)
    check(not miss, "没有「没人跑」的测试文件",
          "全部有主" if not miss else "★ 孤儿：" + "、".join(miss))

    # ★ 反向：登记了但文件不在 —— 那是另一个方向的失联（改名后最容易漏）。
    ghost = sorted(r for r in reg if not (ROOT / r).is_file())
    check(not ghost, "登记表里没有指向不存在文件的死条目",
          "无" if not ghost else "★ 死条目：" + "、".join(ghost))

    # ---- 1b) 元测试：这个检测器**真的能红** -----------------------------
    print("\n=== 1b) ★元测试：拿掉一行登记，§1 必须报出来 ===")
    # 故意从清单文本里抹掉一行（不改磁盘上的文件），再喂给同一个纯函数。
    victim = "tests/contract/test_geometry_solver.py"
    check(victim in reg, "被拿来做实验的那行确实在清单里（否则实验无意义）", victim)
    doctored = "\n".join(l for l in reg_text.splitlines() if victim not in l)
    check(len(registered_files(doctored)) == len(reg) - 1,
          "捏造后的清单少了一行（实验确实生效了）",
          f"{len(registered_files(doctored))} vs {len(reg)}")
    red = unregistered(files, doctored)
    check(victim in red,
          "★ 拿掉那一行之后，它被报成孤儿了（检测器不是摆设）",
          "、".join(red) if red else "★ 仍然报「无孤儿」 —— 检测器形同虚设")

    # ---- 1c) 豁免表里的条目必须真的存在，且理由非空 ---------------------
    print("\n=== 1c) 豁免表本身要被检查（否则它会变成垃圾桶）===")
    for rel, why in _TESTS_NOT_IN_RUNNER.items():
        check((ROOT / rel).is_file(), f"豁免的 {rel} 真的存在",
              "（改名后最容易漏）" if not (ROOT / rel).is_file() else "")
        check(len(why.strip()) >= 20, f"豁免 {rel} 写了理由", f"{len(why.strip())} 字")

    # =====================================================================
    print("\n=== 2) initdb.d 挂载表：每个 sql，要么挂载、要么是迁移/种子且写明理由 ===")
    cmp_text = _read(COMPOSE)
    mounted = mounted_sql(cmp_text)
    sq = sql_files()
    check(len(mounted) > 0, "挂载表解析成功", f"{len(mounted)} 个：" + "、".join(sorted(mounted)))
    check(len(sq) > 0, "sql/ 下扫到了文件", f"{len(sq)} 个")

    um = unmounted(sq, cmp_text)
    check(not um, "没有「来路不明」的未挂载 sql",
          "每一个未挂载的都有归属" if not um
          else "★ 未挂载且无理由：" + "、".join(um))

    # ★ 反向：挂了但文件不在。
    mg = sorted(m for m in mounted if not (ROOT / "sql" / m).is_file())
    check(not mg, "挂载表里没有指向不存在文件的死条目",
          "无" if not mg else "★ 死条目：" + "、".join(mg))

    # ★ 权限：宿主文件对容器内的 postgres 用户必须可读（§18h 守的是同一个坑，
    #   这里再钉一次是因为**迁移/种子文件被换掉时最容易丢权限**）。
    bad_perm = sorted(m for m in mounted
                      if (ROOT / "sql" / m).is_file()
                      and ((ROOT / "sql" / m).stat().st_mode & 0o044) != 0o044)
    check(not bad_perm, "挂载的 sql 对容器可读（组/其他可读位在位）",
          "无" if not bad_perm else "★ 权限不足：" + "、".join(bad_perm))

    # ---- 2b) 元测试 -----------------------------------------------------
    print("\n=== 2b) ★元测试：拿掉一行挂载，§2 必须报出来 ===")
    keep = "25_crs_two_level_guard.sql"
    check(keep in mounted, "被拿来做实验的那个挂载确实在（否则实验无意义）", keep)
    doctored_c = "\n".join(l for l in cmp_text.splitlines() if keep not in l)
    check(len(mounted_sql(doctored_c)) == len(mounted) - 1,
          "捏造后的挂载表少了一项",
          f"{len(mounted_sql(doctored_c))} vs {len(mounted)}")
    red2 = unmounted(sq, doctored_c)
    check(keep in red2,
          "★ 拿掉那条挂载之后，它被报成来路不明了（检测器不是摆设）",
          "、".join(red2) if red2 else "★ 仍然报「无异常」 —— 检测器形同虚设")

    # ★ 还有一条方向更刁的元测试：把一个**种子**加进豁免表之外、又不挂载，
    #   必须报出来。否则"种子"会变成一个绕过 §2 的后门。
    doctored_s = "\n".join(l for l in cmp_text.splitlines() if "90_seed_skeleton" not in l)
    red3 = unmounted(sq, doctored_s)
    check("90_seed_skeleton.sql" in red3,
          "★ 把一个**种子**的挂载拿掉，也必须报出来（种子不是后门）",
          "、".join(red3) if red3 else "★ 没报出来")

    # ---- 2c) `_migrate_` 前缀是**人写的声明**，本文件只保证边界说得清 ----
    print("\n=== 2c) `_migrate_` 的边界：能查什么、查不了什么（写清楚，不假装）===")
    mig = [s for s in sq if "_migrate_" in s]
    seed = [s for s in sq if "_seed_" in s]
    check(len(mig) > 0, "sql/ 下确实有迁移文件（否则这条规则是空转）", f"{len(mig)} 个")
    # ★ 把数字**打出来**：豁免不是偷偷发生的，它是一次可见的事实陈述。
    #   ★★ 注意这是**划分**，不是三个可以重叠的集合 —— `90_seed_skeleton.sql`
    #   既是种子、又已挂载，所以"种子"那一格必须扣掉已挂载的，否则会重复计数。
    #   等式取 `==` 而不是 `<=`：三类**恰好**覆盖 sql/ 全部，
    #   多一个少一个都会红。
    seed_only = [s for s in seed if s not in mounted]
    check(len(mounted) + len(mig) + len(seed_only) == len(sq),
          "三类**恰好**覆盖 sql/ 全部（挂载 ＋ 迁移 ＋ 未挂载的种子 == 总数）",
          f"{len(mounted)} ＋ {len(mig)} ＋ {len(seed_only)} == {len(sq)}"
          + ("" if len(mounted) + len(mig) + len(seed_only) == len(sq)
             else "  ★ 有文件落在这三类之外，或者被重复计数"))

    # ★★ 这里曾经写过一条**错的**规则：「每个 _migrate_ 文件里必须有结构变更语句」。
    #    实测立刻报红 —— 而红的是**规则**，不是文件：
    #      `90_migrate_v05_batch_remark.sql` 头部自己写着
    #      「依赖：无（**纯数据订正，不动表结构、不动任何列**）」。
    #      ⇒ **`_migrate_` 完全可以是纯数据迁移。那条规则编码了一个假前提。**
    #    又试了两条替代，实测**都不成立**：
    #      · 头部声明「目标库/幂等/依赖」三行 —— 14 个迁移里只有 1 个齐备；
    #      · 头部自述「迁移 NN」 —— 只有 6/14，种子 0/3；
    #      · 有没有统一执行器 —— **没有任何执行器**（迁移是人手工跑的）。
    #
    #    ⇒ **「该是迁移还是种子」没有可靠的机器判据**（它取决于文件做什么、
    #      以及它挂靠的数据在全新装上存不存在）。**与其编一条会误报的规则，
    #      不如把边界写清楚**：`_migrate_` 前缀是**人的声明**，本文件只保证
    #      「没有写 `_migrate_` 的东西，一律盯着」。
    #
    #    ★ 但有一条**真的**成立，而且与文件名无关：**一个 sql 文件总得做点什么。**
    #      一个什么都不做的文件，无论叫什么都是缺陷。
    #      （`98/99` 那两个种子在全新装上会「静默插零条」，正是这条的反面教材；
    #       但它们**在活库上确实有效**，所以不能拿"全新装无效"当判据 ——
    #       只能把它们作为"要看得见的例外"登记在 §2 的豁免表里。）
    _ACTS = ("ALTER", "CREATE", "INSERT", "UPDATE", "DELETE", "DROP",
             "DO ", "SELECT", "GRANT", "COMMENT ON", "TRUNCATE")
    dead = []
    for s in sq:
        up = _read(ROOT / "sql" / s).upper()
        body = "\n".join(l for l in up.splitlines() if not l.strip().startswith("--"))
        if not any(k in body for k in _ACTS):
            dead.append(s)
    check(not dead, "每个 sql 文件都**真的做点什么**（至少一条可执行语句，与文件名无关）",
          f"{len(sq)} 个都有动作" if not dead else "★ 什么都不做：" + "、".join(dead))

    # =====================================================================
    print("\n=== 3) 两张豁免表**只许缩短**（加条目要停下来想一想）===")
    check(len(_TESTS_NOT_IN_RUNNER) <= _TEST_ALLOW_BUDGET,
          "测试豁免表的条数没有增长",
          f"{len(_TESTS_NOT_IN_RUNNER)} ≤ 预算 {_TEST_ALLOW_BUDGET}")
    check(len(_SQL_NOT_MOUNTED) <= _SQL_ALLOW_BUDGET,
          "sql 豁免表的条数没有增长",
          f"{len(_SQL_NOT_MOUNTED)} ≤ 预算 {_SQL_ALLOW_BUDGET}")
    check(all(v.strip() for v in _TESTS_NOT_IN_RUNNER.values()),
          "测试豁免表没有空理由的条目")
    check(all(v.strip() for v in _SQL_NOT_MOUNTED.values()),
          "sql 豁免表没有空理由的条目")
    # ★ 理由必须指向证据（文件/章节），不能只是一句"这是有意的"。
    weak = [k for k, v in {**_TESTS_NOT_IN_RUNNER, **_SQL_NOT_MOUNTED}.items()
            if not re.search(r"\.(md|py|sh|yml|sql)|§|见 |README", v)]
    check(not weak, "每条豁免的理由都**指向了证据**（不是一句「这是有意的」）",
          "无" if not weak else "★ 理由太薄：" + "、".join(weak))

    # =====================================================================
    print("\n=== 4) `.env.example` 说明书：compose 要的每个键，说明书都得写 ===")
    ex_text = _read(ENV_EXAMPLE)
    ref_keys = env_referenced(cmp_text)
    decl_keys = env_declared(ex_text)
    check(len(ref_keys) > 0, "compose 的 ${…} 引用解析成功（不是空表）",
          f"{len(ref_keys)} 个键")
    check(len(decl_keys) > 0, ".env.example 的声明解析成功", f"{len(decl_keys)} 个键")

    miss_env = env_undeclared(cmp_text, ex_text)
    check(not miss_env, "compose 引用的键，`.env.example` 里全都写了",
          f"两边各 {len(ref_keys)} / {len(decl_keys)} 个，完全对齐" if not miss_env
          else "★ 说明书漏了：" + "、".join(miss_env)
               + "　（不声明也跑得起来，因为有 `:-` 默认值 —— 所以它不会自己响）")

    # ★ 反向：说明书写了但没人引用 —— 那是另一个方向的失联（键改名后最容易漏）。
    stale_env = sorted(decl_keys - ref_keys)
    check(not stale_env, "`.env.example` 里没有没人引用的死键",
          "无" if not stale_env else "★ 死键：" + "、".join(stale_env))

    # ---- 4b) ★元测试：这个检测器**真的能红**，而且**不会反向误报** ------
    print("\n=== 4b) ★元测试：捏造一个未声明的键，§4 必须报出来 ===")
    doctored_cmp = cmp_text.replace("PG_DB: road_pavement",
                                    "PG_DB: road_pavement\n      NEVER_DECLARED_PORT: 9999",
                                    1)
    if "NEVER_DECLARED_PORT" not in doctored_cmp:
        # 上面的替换依赖具体写法，换一种更稳的捏造：直接拼一个引用进去。
        doctored_cmp = cmp_text + "\n# ${NEVER_DECLARED_PORT:-9999}\n"
    red_env = env_undeclared(doctored_cmp, ex_text)
    check("NEVER_DECLARED_PORT" in red_env,
          "★ 捏造一个未声明的键之后，它被报出来了（检测器不是摆设）",
          "、".join(red_env) if red_env else "★ 仍然报「全对齐」 —— 检测器形同虚设")

    # ★★ 4b-2) 反向元测试：**子串陷阱** —— `MINIO_CONSOLE_PORT` 已声明，
    #    绝不能因为键名里含 `CONSOLE_PORT` 就被报成缺失。
    #    这条元测试钉住的是「整键匹配」这件事本身；它红了说明匹配退化成子串了。
    check("MINIO_CONSOLE_PORT" in decl_keys,
          "前置：MINIO_CONSOLE_PORT 确实是已声明的（否则下一条无意义）")
    check("MINIO_CONSOLE_PORT" not in red_env,
          "★★ 子串陷阱：MINIO_CONSOLE_PORT 没被误报成缺失（整键匹配成立）",
          "未被误报" if "MINIO_CONSOLE_PORT" not in red_env
          else "★ 反向误报：一个正确的键被报成缺失 —— 匹配退化成子串了")

    print("")
    if fails:
        print(f"失败 {fails} 项")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
