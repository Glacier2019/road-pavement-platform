#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""契约② 补漏：**「按 DDL 全新装库」与「靠 migration 升级」必须得到同一套 schema**。

为什么要这一组
--------------
仓库里一直缺这个检查 —— 这一点写在 `sql/89_migrate_v05_hdm.sql` 的后记里。
缺口不是纸面上的，它有**实证代价**：

  · `cross_section_ground_point.side` 一度是 `char(1) check (side in ('L','R'))`，
    而另外 7 张表的 `side` 是 `varchar(8) check (side in ('left','right'))`。
  · 这个不一致**两种装库路径看到的不一样**：
      - 纯 DDL 探针库跑出来是 `varchar(8)`（DDL 已经被改对了）
      - 走 migration 升级的库一度是 `char(1) + L/R`
  · 而且没人发现 —— 因为**没有任何测试同时看两条路径**。

本组的不变量
------------
    DDL(全新装)  ==  DDL + migration(升级)

因为：`10_ddl_v0.5.sql` 是**全新装的真相**，migration 是**历史**。
migration 是给"已经装了旧版本的库"补课用的；对一个**已经是最新 DDL** 的库，
每条 migration 都应当是**空操作**。若不然，说明 DDL 与 migration 发散，
那么"新同事 clone 下来装"和"老库升级上来"会得到两套不同的 schema。

怎么测
------
在一个**一次性数据库**里做（做完就 drop，绝不碰 road_pavement）：

    ① create database rp_parity_<pid>
    ② 只跑 10_ddl_v0.5.sql            → 快照 A
    ③ 依次跑 85..91 的全部 migration  → 快照 B
    ④ 逐项比对 A 与 B
    ⑤ drop database

快照取三样，都用 PostgreSQL 自己的 `format_type` / `pg_get_constraintdef` /
`pg_get_indexdef` 渲染成文本，避免自己去解释 catalog：

    · 列：  表.列  类型  null 与否
    · 约束：表 约束名 定义
    · 索引：索引名 定义

非空转怎么保证
--------------
这一组**很容易写成永远通过**（比如快照取空了，两边都空 → 相等）。
所以除了比对本身，另加三条：

  1. 快照 A 必须**非空**（列数、约束数、索引数都给下限）；
  2. 表数必须等于 10_ddl 里的 `CREATE TABLE` 数（用另一条路径数出来，互相钉）；
  3. **元测试**：故意往 B 里插一条伪造差异（改一个列类型），比对**必须**变红。
     这一条证明比对函数真的在看内容，而不是"两个空集相等"。

跑法
----
    cd scaffold && uv run --quiet --with "psycopg[binary,pool]==3.2.3" \\
        tests/contract/test_ddl_migration_parity.py
"""

from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]          # scaffold/
SQL_DIR = ROOT / "sql"
DDL = SQL_DIR / "10_ddl_v0.5.sql"
PARTITIONS = SQL_DIR / "20_partitions.sql"

FAIL = 0
PASS = [0]
SKIP = [0]


def ok(name: str, cond: bool, detail: str = "") -> bool:
    global FAIL
    if cond:
        PASS[0] += 1
        print(f"  ✓ {name}" + (f"　{detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f"　{detail}" if detail else ""))
    return cond


def check(name: str, cond: bool, detail: str = "") -> bool:
    return ok(name, cond, detail)


# ── psql 通道 ───────────────────────────────────────────────────────────────
# 全部走 `docker exec rp-pg psql`，与仓库里其余打库测试一致：
# 库里那台 PG 在容器内，宿主机只映射了端口，用 psql 客户端最省事，
# 也避免"宿主机 psql 版本和容器不一致"这种假失败。

def _psql(sql: str, db: str, *, stop: bool = False, tuples: bool = True) -> tuple[int, str]:
    """在容器里跑一段 SQL。返回 (exit, stdout+stderr)。"""
    args = ["docker", "exec", "-i", "rp-pg", "psql", "-U", PG_USER, "-d", db]
    if stop:
        args += ["-v", "ON_ERROR_STOP=1"]
    if tuples:
        args += ["-tA"]
    args += ["-c", sql]
    p = subprocess.run(args, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _psql_file(path: pathlib.Path, db: str) -> tuple[int, str]:
    """跑一个 .sql 文件（含 BEGIN/COMMIT 的整段），带 ON_ERROR_STOP。"""
    with open(path, "rb") as fh:
        p = subprocess.run(
            ["docker", "exec", "-i", "rp-pg", "psql", "-U", PG_USER, "-d", db,
             "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"],
            stdin=fh, capture_output=True, text=True, timeout=600)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _env() -> dict[str, str]:
    vals: dict[str, str] = {}
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()          # ⚠ .env 里有行内注释
            if "=" in line:
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    return vals


_ENV = _env()
PG_USER = _ENV.get("PG_USER") or os.environ.get("PG_USER") or "rp"
PG_DB = _ENV.get("PG_DB") or os.environ.get("PG_DB") or "road_pavement"


# ── 快照 ────────────────────────────────────────────────────────────────────

_SNAP_COLS = r"""
select a.attrelid::regclass::text || '.' || a.attname || '  '
       || format_type(a.atttypid, a.atttypmod)
       || '  null=' || (not a.attnotnull)::text
from pg_attribute a
join pg_class c on c.oid = a.attrelid
join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public'
  and c.relkind in ('r','p')
  and a.attnum > 0
  and not a.attisdropped
  and c.relname <> 'spatial_ref_sys'
order by 1
"""

_SNAP_CONS = r"""
select conrelid::regclass::text || '  ' || conname || '  ' || pg_get_constraintdef(oid)
from pg_constraint
where connamespace = 'public'::regnamespace
order by 1
"""

_SNAP_IDX = r"""
select indexname || '  ' || indexdef
from pg_indexes
where schemaname = 'public'
order by 1
"""

_SNAP_TABLES = r"""
select c.relname
from pg_class c join pg_namespace n on n.oid = c.relnamespace
where n.nspname = 'public' and c.relkind in ('r','p')
  and c.relname <> 'spatial_ref_sys'
order by 1
"""


def _snapshot(db: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, sql in (("cols", _SNAP_COLS), ("cons", _SNAP_CONS),
                     ("idx", _SNAP_IDX), ("tables", _SNAP_TABLES)):
        rc, txt = _psql(sql, db)
        out[key] = [ln for ln in txt.splitlines() if ln.strip()] if rc == 0 else []
    return out


def _diff(a: dict[str, list[str]], b: dict[str, list[str]]) -> list[str]:
    """返回人类可读的差异行。两边都按同样的顺序取，所以集合差就够。"""
    lines: list[str] = []
    for key, label in (("tables", "表"), ("cols", "列"), ("cons", "约束"), ("idx", "索引")):
        sa, sb = set(a.get(key, [])), set(b.get(key, []))
        for x in sorted(sa - sb):
            lines.append(f"{label} 只在「全新装 DDL」里：{x}")
        for x in sorted(sb - sa):
            lines.append(f"{label} 只在「DDL+migration」里：{x}")
    return lines


def main() -> int:
    print("=" * 74)
    print("契约② 补漏  DDL 全新装  ↔  DDL + migration 升级（schema 必须逐项一致）")
    print("=" * 74)

    if not DDL.exists():
        print(f"  ✗ 找不到 {DDL}")
        return 1

    # 有哪些 migration：8x/9x 的 migrate_*.sql，按文件名排序 = 施加顺序
    migrations = sorted(p for p in SQL_DIR.glob("*_migrate_*.sql"))
    print(f"  · DDL：{DDL.name}")
    print(f"  · migration：{' → '.join(p.name for p in migrations)}")
    print()

    db = "rp_parity_" + uuid.uuid4().hex[:8]
    rc, txt = _psql(f'create database "{db}";', PG_DB)
    if rc != 0:
        print(f"  ⊘ 跳过：建不出临时库（{txt.strip()[:120]}）")
        SKIP[0] += 1
        return 0

    try:
        # ── ① 只跑 DDL ────────────────────────────────────────────────────
        rc, txt = _psql_file(DDL, db)
        ok("① 全新装：只跑 10_ddl_v0.5.sql 成功", rc == 0, txt.strip()[-200:] if rc else "")
        if rc != 0:
            return 1

        snap_a = _snapshot(db)
        n_ddl_tables = len(snap_a["tables"])
        print(f"    快照 A：{n_ddl_tables} 表 / {len(snap_a['cols'])} 列 / "
              f"{len(snap_a['cons'])} 约束 / {len(snap_a['idx'])} 索引")

        # ── 非空转 ①：快照不能是空的 ──────────────────────────────────────
        ok("★ 非空转：快照 A 非空（表/列/约束/索引都取到了东西）",
           n_ddl_tables > 40 and len(snap_a["cols"]) > 300
           and len(snap_a["cons"]) > 50 and len(snap_a["idx"]) > 40,
           f"表 {n_ddl_tables} 列 {len(snap_a['cols'])} "
           f"约束 {len(snap_a['cons'])} 索引 {len(snap_a['idx'])}")

        # ── 非空转 ②：表数要和 DDL 文件里数出来的对上 ─────────────────────
        # 用另一条路径（读文本数 CREATE TABLE）钉住，避免"快照查询写错了但两边一致"。
        ddl_text = DDL.read_text(encoding="utf-8")
        n_in_file = len(re.findall(r"CREATE TABLE IF NOT EXISTS\s+(\w+)", ddl_text, re.I))
        ok("★ 非空转：快照里的表数 == DDL 文件里 CREATE TABLE 的个数",
           n_ddl_tables == n_in_file,
           f"快照 {n_ddl_tables} vs 文件 {n_in_file}")

        # ── ② 依次施加 migration ─────────────────────────────────────────
        applied, failed = [], []
        for m in migrations:
            rc, txt = _psql_file(m, db)
            if rc == 0:
                applied.append(m.name)
            else:
                failed.append((m.name, txt.strip()[-260:]))

        # migration 在"已是最新 DDL"的库上**不该报错** —— 报错说明它没考虑
        # "目标状态已经达成"这一情形，老库升级会直接中断。
        ok(f"② {len(applied)}/{len(migrations)} 条 migration 在最新 DDL 上跑通（不报错）",
           not failed,
           "；".join(f"{n}: {e.splitlines()[-1][:90]}" for n, e in failed[:2]) if failed else "")

        snap_b = _snapshot(db)
        print(f"    快照 B：{len(snap_b['tables'])} 表 / {len(snap_b['cols'])} 列 / "
              f"{len(snap_b['cons'])} 约束 / {len(snap_b['idx'])} 索引")

        # ── ③ 比对 ───────────────────────────────────────────────────────
        diffs = _diff(snap_a, snap_b)
        if diffs:
            print()
            print("    ── 差异明细（最多 20 条）──")
            for d in diffs[:20]:
                print(f"      {d}")
            if len(diffs) > 20:
                print(f"      …… 另有 {len(diffs) - 20} 条")
        ok("★★ 不变量：DDL 全新装 与 DDL+migration 升级 得到同一套 schema",
           not diffs, f"{len(diffs)} 处不一致" if diffs else "")

        # ── ④ 元测试：比对函数真的在看内容吗 ─────────────────────────────
        # 伪造一条差异（把 A 里某一列的字符类型改掉），_diff 必须报出来。
        # 不验这一下，就排除不了"两个空集相等"式的假通过。
        fake = {k: list(v) for k, v in snap_a.items()}
        target = next((c for c in fake["cols"] if "character varying" in c), None)
        if target:
            fake["cols"] = [c.replace("character varying", "text", 1)
                            if c == target else c for c in fake["cols"]]
            ok("★★ 元测试：伪造一处列类型差异后，比对**必须**变红（证明比对非摆设）",
               bool(_diff(fake, snap_b)))
        else:
            ok("★ 元测试：快照里存在 character varying 列（元测试才有素材）", False,
               "找不到 varchar 列 —— 元测试无从下手")
        ok("★★ 元测试：把快照 B 掏空后，比对**必须**变红",
           bool(_diff(snap_a, {"tables": [], "cols": [], "cons": [], "idx": []})))

        # ── ⑤ 顺带：20_partitions.sql 也要能在只跑过 DDL 的库上跑通 ──────
        # 它和 DDL 一样是"全新装"路径的一部分（compose 挂在同一个目录里）。
        if PARTITIONS.exists():
            rc2, txt2 = _psql_file(PARTITIONS, db)
            ok("★ 20_partitions.sql 也能在全新装的库上跑通",
               rc2 == 0, txt2.strip()[-160:] if rc2 else "")

    finally:
        _psql(f'drop database if exists "{db}" with (force);', PG_DB)

    print()
    print(f"通过 {PASS[0]} ｜ 失败 {FAIL} ｜ 跳过 {SKIP[0]}")
    if FAIL:
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
