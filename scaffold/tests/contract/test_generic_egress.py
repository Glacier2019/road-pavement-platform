#!/usr/bin/env python3
"""契约②：**通用出口**必须能翻页，且不许静默吞掉不认识的查询参数。

运行（**需要活库**；无 `.env` 则跳过并明说）：
  cd /data/cy/shujuku/scaffold
  ./run_contract_tests.sh 通用出口
  # 单跑：PYTHONPATH=modules/_shared uv run --with "psycopg[binary,pool]==3.2.3" \
  #       --with fastapi==0.115.6 --with httpx python tests/contract/test_generic_egress.py

★★ 本文件守的是 2026-10-14（用户裁决的「甲」）修掉的两个**实测**缺陷。
   两个都是「看着成功、其实错了」那一类，所以此前**没有任何东西报错**：

   ① **不认识的查询参数被静默吞掉。** 现场实测（改造前）：
      `?limit=1000`、`?limit=1000&section_id=6`、`?limit=1000&bogus_xyz=1`
      拿到的响应**一模一样**（都是全表的 332 行）。调用方把「全部数据」
      读成了「我筛出来的数据」，而响应里**没有任何字段**说明筛选没生效。
   ② **不能翻页。** `cross_section_ground_point` 有 2215 行，而改造前
      `?limit=1000&offset=1000` 回的仍是 id=[1, 2] —— 第 1001..2215 行
      **永远取不到**。而契约 `m6-gateway.v0.3.yaml` 里明写着
      「truncated: true → 客户端据此翻页」，那句在当时是**空话**。

★★ 为什么另开一份，而不是并进 `m6-路由`：
   `m6-路由` 把数据层**替换成假实现** —— 那是对的做法，它守的是「路由自洽、
   契约对齐、状态码」，且**离线可跑**。但它测的是「管道通不通」，
   测不到 `OFFSET` 这个**子句**。而这次改的**正是 SQL 本身**。
   ⇒ 这里跑**真库 + 真仓储 + 真路由**。
   两条门的分工是「接口形状」与「真实取数」，不是重复。

★ 本文件自带一条**活体元测试**（第 5 组）：把仓储包一层、故意忽略 `offset`，
  证明第 1 组的判据**真的会响**。宪法原则 V：
  「一个永远不会失败的检查，比没有检查更糟。」
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# M3（rpdao）与 M6（app.py 的兄弟模块）都按路径加载，所以先就位。
for _d in ("M3-rpdao", "M6-api"):
    _p = str(ROOT / "modules" / _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _env import dsn as _env_dsn  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

#: 通用出口 `limit` 的上限。
#: ★ 与 `modules/M6-api/app.py` 的 `Query(100, ge=1, le=1000)` 是**同一个数**。
#:   契约改了上限而这里没跟着改，第 4 组就会红 —— 这是有意留的联动。
LIMIT_MAX = 1000

_bad: list[str] = []
_n: int = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _n
    _n += 1
    print(f"  {'✓' if ok else '✗'} {name}" + (f" —— {detail}" if detail else ""))
    if not ok:
        _bad.append(name)
    return ok


def load_app():
    """按路径加载 M6 的 app.py（与 test_api_routes.py 同一手法）。"""
    spec = importlib.util.spec_from_file_location(
        "ge_api_app", ROOT / "modules" / "M6-api" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ge_api_app"] = mod
    spec.loader.exec_module(mod)
    return mod


def sweep(repo, table: str, lim: int, page_cap: int = 500) -> tuple[list[int], int]:
    """用 `limit=lim` 一页一页把整张表取回来，返回 (id 序列, 页数)。

    ★ 判据不是「取到了几行」，而是**取回来的 id 恰好是 1..n 的一个排列** ——
      一个不跳行的实现在这里必然出现**重复**，一个跳错行的必然出现**空缺**。
    ★ `page_cap` 是死循环保险：一个永远返回满页的实现会一直翻下去，
      而测试**不能挂住**（挂住和被跳过一样，是「空转不是通过」）。
    """
    seen: list[int] = []
    off = pages = 0
    while pages < page_cap:
        rows = repo.list_objects(table, limit=lim, offset=off)
        seen.extend(r["id"] for r in rows)
        pages += 1
        if len(rows) < lim:
            break
        off += lim
    return seen, pages


def main() -> int:
    dsn = _env_dsn()
    if not dsn:
        print("  未设 PG_DSN 且 scaffold/.env 无 PG_* —— 跳过（本套件需要活库）")
        print("通过 0 ｜ 失败 0 ｜ 跳过 1")
        return 0
    os.environ["PG_DSN"] = dsn

    app_mod = load_app()
    # ★ 必须**手动开池**：`TestClient(app)` 自己不会开，池没开时每个请求都 503。
    app_mod.dao.open()
    client = TestClient(app_mod.app)

    try:
        # ---------------------------------------------------- 第 0 组 前置
        print("第 0 组  前置：找一张大到**必须翻页**的表")
        sized: list[tuple[int, str]] = []
        for ot in sorted(app_mod.OBJECT_TYPES):
            try:
                n = app_mod.dao.domain(app_mod.OBJECT_TYPES[ot]).count(ot)
            except Exception as exc:                      # noqa: BLE001
                # ★ 不静默：跳过了什么、为什么，都打出来。
                print(f"    {ot}：跳过（{type(exc).__name__}: {exc}）")
                continue
            sized.append((n, ot))
        sized.sort(reverse=True)
        print(f"  登记对象按行数（前 4）：{sized[:4]}")
        if not check(
                "★★ 前置：至少有一张登记表的行数 > `limit` 上限（否则翻页无从测起）",
                bool(sized) and sized[0][0] > LIMIT_MAX,
                f"最大 = {sized[0] if sized else None}，上限 = {LIMIT_MAX}"):
            print(f"通过 {_n - len(_bad)} ｜ 失败 {len(_bad)} ｜ 跳过 0")
            return 1
        N, TABLE = sized[0]
        GE = app_mod.dao.domain(app_mod.OBJECT_TYPES[TABLE])
        print(f"  选定：{TABLE}（{N} 行，上限 {LIMIT_MAX}）")

        # ------------------------------------------- 第 1 组 真仓储：真跳行
        print("\n第 1 组  真仓储：`offset` **真的跳行**（整表分页，不重不漏）")
        lim = LIMIT_MAX
        seen, pages = sweep(GE, TABLE, lim)
        want = list(range(1, N + 1))
        ceil_pages = -(-N // lim)
        check("分页取回的 id **恰好**是 1..n 的一个排列",
              sorted(seen) == want and len(seen) == N,
              f"{pages} 页 × limit {lim} → 取回 {len(seen)} 行，去重后 {len(set(seen))} 行")
        check("★ 没有**重复**（收下 offset 却不跳行的实现必然在这里出现重复）",
              len(seen) == len(set(seen)),
              f"{len(seen) - len(set(seen))} 个重复")
        check("★ 没有**空缺**（跳错位置的实现必然在这里出现空缺）",
              sorted(set(seen)) == want,
              f"缺 {len(set(want) - set(seen))} 个")
        check("页数与行数自洽", pages == ceil_pages,
              f"实际 {pages} 页，期望 ⌈{N}/{lim}⌉ = {ceil_pages}")

        # --------------------------------------- 第 2 组 真仓储：边界与兼容
        print("\n第 2 组  真仓储：边界与向后兼容")
        a = [r["id"] for r in GE.list_objects(TABLE, limit=3)]
        b = [r["id"] for r in GE.list_objects(TABLE, limit=3, offset=0)]
        check("★ 不传 offset 与传 offset=0 取到**同一批行**（老调用行为不变）",
              a == b and a[:1] == [1], f"{a} / {b}")
        tail = [r["id"] for r in GE.list_objects(TABLE, limit=3, offset=N)]
        check("offset = 总行数 → 空列表（**不是**报错）", tail == [], str(tail))
        beyond = [r["id"] for r in GE.list_objects(TABLE, limit=3, offset=N + 5000)]
        check("offset 远超总行数 → 空列表", beyond == [], str(beyond))
        mid = [r["id"] for r in GE.list_objects(TABLE, limit=3, offset=N - 2)]
        check("offset 落在最后一页 → 只剩尾部两行", mid == [N - 1, N], str(mid))

        # ------------------------------------- 第 3 组 路由：未知参数必须报错
        print("\n第 3 组  路由：未知查询参数必须报错**并列出支持的键**")
        for bad in ("section_id", "bogus_xyz", "offsett", "Limit"):
            r = client.get(f"/v1/objects/{TABLE}?{bad}=1")
            ok = (r.status_code == 422 and bad in r.text
                  and "limit" in r.text and "offset" in r.text)
            check(f"?{bad}=1 → 422 且正文列出 limit/offset", ok,
                  f"HTTP {r.status_code} {r.text[:96]}")
        r = client.get(f"/v1/objects/{TABLE}?limit=5&offset=0")
        check("合法参数**不受影响**（别为了挡坏人误伤好人）",
              r.status_code == 200, f"HTTP {r.status_code}")
        r = client.get("/v1/objects/not_registered_typeszz")
        check("未登记的对象类型仍然 404（这条语义没被改动）",
              r.status_code == 404, f"HTTP {r.status_code}")
        r = client.get("/v1/objects/not_registered_typeszz?bogus=1")
        check("未登记类型 + 未知参数 → 404（先报更根本的那个错）",
              r.status_code == 404, f"HTTP {r.status_code}")

        # ----------------------------- 第 4 组 端到端：路由翻页取回整张表
        print(f"\n第 4 组  端到端：路由翻页把 {N} 行**全取回来**（缺陷②的回归点）")
        got: list[int] = []
        truncs: list[bool] = []
        counts: list[int] = []
        off = pages2 = 0
        while pages2 < 500:
            body = client.get(
                f"/v1/objects/{TABLE}?limit={lim}&offset={off}").json()
            got.extend(x["id"] for x in body["items"])
            truncs.append(body["truncated"])
            counts.append(body["count"])
            pages2 += 1
            if not body["truncated"]:
                break
            off += lim
        check("★ 路由能把整张表取完（改造前第 1001 行起**永远取不到**）",
              sorted(got) == want, f"{pages2} 页 → {len(got)} 行，期望 {N}")
        check("★ 没有重复", len(got) == len(set(got)),
              f"{len(got) - len(set(got))} 个重复")
        check("每页 count ≤ limit",
              all(c <= lim for c in counts), f"counts = {counts}")
        check("前几页 truncated=True（否则翻页根本不会发生）",
              len(truncs) > 1 and all(truncs[:-1]), str(truncs))
        check("★ 最后一页 truncated=False（否则客户端会**永远翻下去**）",
              truncs[-1] is False, str(truncs))
        check("页数与第 1 组的真仓储扫描一致", pages2 == pages,
              f"路由 {pages2} 页 vs 仓储 {pages} 页")

        # --------------------------------- 第 5 组 元测试：证明判据会响
        print("\n第 5 组  元测试：第 1 组的判据**会**认出「收下 offset 却不跳行」")

        class _NoSkip:
            """故意忽略 `offset` 的仓储 —— 正是缺陷本身的形态。

            ★ 收下 `offset`（签名对得上，所以不会被类型错误挡住）、
              但**不拿它去跳行**。第 1 组那条「没有重复」必须在这里响。
            """

            def __init__(self, real):
                self._real = real

            def list_objects(self, table, *, limit=100, offset=0):
                return self._real.list_objects(table, limit=limit)

        bad_seen, bad_pages = sweep(_NoSkip(GE), TABLE, 50, page_cap=12)
        check("★ 不跳行的实现在同一条扫描下**出现重复**（判据有分辨力）",
              len(bad_seen) != len(set(bad_seen)),
              f"{bad_pages} 页 × 50 → {len(bad_seen)} 行，去重后 {len(set(bad_seen))} 行")
        check("而真仓储在同一判据下**不**出现重复",
              len(seen) == len(set(seen)), f"{len(seen)} 行")
    finally:
        app_mod.dao.close()

    # ★ 摘要是**最后一行** —— 运行器 `tail -1` 取它当这一套的结论。
    #   所以「通过 N ｜ 失败 M」之后不许再打印任何东西。
    print(f"通过 {_n - len(_bad)} ｜ 失败 {len(_bad)} ｜ 跳过 0")
    return 1 if _bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
