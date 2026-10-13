"""契约测试共用的 `.env` / DSN 解析 —— **一个性质，一份定义**。

★★ 为什么要有这个文件
--------------------------------------------------------------------------------
本仓的契约测试**跑法特殊**：由 `run_contract_tests.sh` 用
`uv run python tests/contract/<file>.py` 直接执行，**不经过 pytest**，
而且运行器**从不传 `PG_DSN`**（脚本里搜 `PG_DSN` 零命中，这是实测的）。

于是每个要打真库的套件都得出自己解决「DSN 从哪来」。到 2026-10-13 为止，
这件事在 `tests/contract/` 下被**独立实现了四遍**：

    test_station_baseline.py      _dsn()      读 .env，含行内注释与引号处理
    test_station_query.py         _dsn()      读 .env，含行内注释处理
    test_design_import.py         _dsn()      读 .env，含行内注释处理
    test_ddl_migration_parity.py  _env()      读 .env，返回整张表

★ 这四份**语义基本等价**（行内注释那个坑都踩过、都补上了），
  所以它们不是"漂移"——**但它们是同一个性质的四个副本**：
  改一个坑要记得改四处，而**没有任何检查会发现你漏了哪一处**。

★★★ 而第五个消费者 `test_geometry_solver.py` 写的是
  ``os.environ.get("PG_DSN") or os.environ.get("RPP_PG_DSN")`` ——
  **压根不读 `.env`**。后果不是"报错"，是**它永远走不到「跳过」以外的那条路**：
  在运行器下它的真库那组**从未运行过**，而它会显示为一行**绿**。

  ⇒ 这正是本仓反复栽的那类**假绿**，也正是 `test_station_baseline.py`
    当初写下「为什么要读 .env 而不是没 PG_DSN 就跳过」那段注释的原因。
    ★ 教训：**那段话当时只写在一个文件里，所以它没能拦住第五个消费者。**

本文件把这套解析**收成一份**。新写的真库套件 `from _env import dsn` 即可。
★ 已有的四份**暂不改动**：它们的**取默认值策略不同**
（baseline 用默认值，query 要求四个键齐全否则返回 None），
统一策略是另一件事，需要单独决定 —— 不要顺手改。

用法（脚本模式下 `sys.path[0]` 就是本目录，直接 import 即可）::

    from _env import dsn
    d = dsn()          # 环境变量优先，否则读 scaffold/.env
    if not d:
        print("未设 PG_DSN 且无 scaffold/.env —— 跳过")
"""

from __future__ import annotations

import os
import pathlib

# tests/contract/_env.py → tests/contract → tests → scaffold
ROOT = pathlib.Path(__file__).resolve().parents[2]

# `.env` 里必须齐备的四个键。缺任何一个都拼不出一个**能连的** DSN。
_REQUIRED = ("PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DB")


def env() -> dict[str, str]:
    """把 `scaffold/.env` 读成一张字典。文件不存在就返回空表（不是报错）。

    ★★ 必须处理**行内注释**。本仓 `.env` 里写着::

        PG_PORT=55432          # 本机 5432 已被其它服务占用，故用 55432

    天真的 ``split("=")`` 会把注释一起吞进值里，得到
    ``postgresql://…@localhost:55432          # 本机…/road_pavement`` ——
    一个**语法上看着像 DSN、连不上又不报错**的字符串。

    ★★ 也要处理**包裹引号**：``PG_PASSWORD="a=b#c"`` 里的 `=` 和 `#`
      都不该被当成语法。所以先剥引号，再切注释 —— 顺序反了就会切错。
    """
    vals: dict[str, str] = {}
    f = ROOT / ".env"
    if not f.exists():
        return vals
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in ("'", '"'):
            q = v[0]
            v = v[1:].split(q, 1)[0]
        elif "#" in v:
            v = v.split("#", 1)[0].strip()
        vals[k.strip()] = v
    return vals


def dsn() -> str | None:
    """真库 DSN：环境变量优先，否则由 `scaffold/.env` 拼出。拿不到就返回 None。

    ★ 返回 None 的语义是「**本机确实没有库**」，不是「懒得连」。
      调用方 MUST 把 None 当成**显式跳过并打出来**，MUST NOT 静默继续 ——
      一个从不运行的检查组，在"全绿"里的读数和"没跑"是一样的。
    """
    for k in ("PG_DSN", "RPP_PG_DSN"):
        v = os.environ.get(k)
        if v:
            return v
    vals = env()
    if any(k not in vals or not vals[k] for k in _REQUIRED):
        return None
    return ("postgresql://{u}:{p}@{h}:{port}/{d}").format(
        u=vals["PG_USER"], p=vals["PG_PASSWORD"],
        h=vals.get("PG_HOST", "localhost"), port=vals["PG_PORT"], d=vals["PG_DB"])
