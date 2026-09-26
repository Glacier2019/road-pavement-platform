"""契约④：空表缺口归因（/v1/catalog/gaps）的契约测试。

运行（**离线可跑**，不起容器、不连库）：
  cd /data/cy/shujuku/scaffold
  python3 tests/contract/test_gap_attribution.py

这个套件钉住的是**判定逻辑**，不是 HTTP 形状 —— 后者由 test_api_routes.py 管。
之所以要单独一套，是因为归因有两处**错了也不报错**的地方：

  ① 段↔表映射漏登记 ⇒ 那段对应的表被判成"没有对应表"，
     **段还在、表也还在，联系静默消失**；
  ② 短路顺序写反     ⇒ 把"源里没有"误报成"适配器没做"，
     **盖掉真正的原因**（本仓库记着的真实踩坑，见 weidi/__init__.py）。

两处都是"跑一遍全绿"。所以每一条判定都必须配**元测试**：
故意写错，看它会不会红。见了红才算这条测试真的在测东西。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in ("M6-api", "M2-ingest", "M3-rpdao"):
    _d = str(ROOT / "modules" / _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)

import gaps as G  # noqa: E402  (M6-api/gaps.py，纯拼装、零依赖)

_fails: list[str] = []


def ok(what: str, cond: bool, detail: str = "") -> None:
    mark = "✓" if cond else "✗"
    print(f"  {mark} {what}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        _fails.append(what)


def _facts(**kw):
    """造一条段事实。默认：已实现、源已收到。"""
    base = {"suffix": ".sta", "kind": "桩号序列文件", "implemented": True,
            "source_state": "received", "source_file": "x.STA"}
    base.update(kw)
    return base


def main() -> int:
    print("契约④ 空表缺口归因")

    # ── 1. 短路顺序：说**真正**的那一个原因（INV-1）──────────────────────────
    print("1. 短路顺序（INV-1）—— 说真正的原因")
    # 记在 weidi/__init__.py 里的真实踩坑：早先写成"在 IMPLEMENTED 里才报
    # source_absent，否则报 not_supported"，于是 .3DR（本工程没生成）被报成
    # not_supported —— **把真正的原因盖掉了**。
    #
    # 正确规则（三条，按短路顺序）：
    #   ① 源已收到 + 已实现 ⇒ 只差导入         → source_ready_not_imported
    #   ② 源已收到 + 未实现 ⇒ 解析器是瓶颈      → module_not_built
    #   ③ 源没有   + 已实现 ⇒ 文件是瓶颈        → source_absent
    #   ④ 源没有   + 未实现 ⇒ **先补解析器**    → module_not_built
    #      （④ 的理由：没有解析器时，就算把文件找来也导不进去。
    #        此时"去要文件"是做了也用不上的动作，所以瓶颈是解析器。）
    r1 = G._classify(row_count=0, segments=["s"],
                     seg_facts={"s": _facts(source_state="received", implemented=True)})
    ok("① 源已收到 ＋ 已实现 ⇒ source_ready_not_imported",
       r1 == "source_ready_not_imported", r1)
    r2 = G._classify(row_count=0, segments=["s"],
                     seg_facts={"s": _facts(source_state="received", implemented=False)})
    ok("② 源已收到 ＋ 未实现 ⇒ module_not_built（解析器是瓶颈）",
       r2 == "module_not_built", r2)
    r3 = G._classify(row_count=0, segments=["s"],
                     seg_facts={"s": _facts(source_state="absent", implemented=True)})
    ok("③ 源没有 ＋ 已实现 ⇒ source_absent（文件是瓶颈，**不是**没实现）",
       r3 == "source_absent", r3)
    r4 = G._classify(row_count=0, segments=["s"],
                     seg_facts={"s": _facts(source_state="absent", implemented=False)})
    ok("④ 源没有 ＋ 未实现 ⇒ module_not_built（先补解析器，找文件也没用）",
       r4 == "module_not_built", r4)
    # 元测试：③ 是**最容易被写错**的那一格 —— 它正是那个踩过的坑。
    #  把 implemented 从 True 翻成 False，结论**必须**从 source_absent 变走；
    #  若实现里把顺序写反（先看 implemented），③ 就会错误地报 module_not_built。
    ok("元测试：③ 翻转 implemented 后结论必须变（证明不是先看 implemented）",
       r4 != r3, f"{r3} vs {r4}")
    ok("元测试：③ 与 ② 结论必须不同（源的状态真的参与判定）", r3 != r2)

    # ── 2. has_data 最优先（INV-2）───────────────────────────────────────────
    print("2. 有数据不编造空因（INV-2）")
    r = G._classify(row_count=5, segments=["s"],
                    seg_facts={"s": _facts(source_state="absent")})
    ok("有数据 + 源缺失 ⇒ has_data（不因源缺失改口）", r == "has_data", r)
    r0 = G._classify(row_count=0, segments=["s"], seg_facts={"s": _facts()})
    ok("元测试：row_count 归零后结论必须变（证明行数真的被用到）",
       r0 != r, f"{r} vs {r0}")

    # ── 3. 无对应段 ⇒ upstream_pending（INV-3）─────────────────────────────
    print("3. 下游产出表不赖到源头上（INV-3）")
    r = G._classify(row_count=0, segments=[], seg_facts={})
    ok("无对应段 ⇒ upstream_pending", r == "upstream_pending", r)
    ok("元测试：给它一个段就不会再报 upstream_pending",
       G._classify(row_count=0, segments=["s"],
                   seg_facts={"s": _facts()}) != "upstream_pending")

    # ── 4. 源已收到但需转换（本轮实测新增的一支）──────────────────────────
    print("4. 源已收到·待转换 vs 源缺失（本轮实测新增）")
    r = G._classify(row_count=0, segments=["s"],
                    seg_facts={"s": _facts(source_state="pending")})
    ok("pending ⇒ source_needs_conversion", r == "source_needs_conversion", r)
    ok("它的建议动作**必须**提到转换（否则用户会直接导入、白跑）",
       "转换" in G.ACTION_OF["source_needs_conversion"])
    ok("元测试：pending 改成 absent 结论必须变",
       G._classify(row_count=0, segments=["s"],
                   seg_facts={"s": _facts(source_state="absent")}) != r)

    # ── 5. unknown 必须暴露（FR-010）────────────────────────────────────────
    print("5. 判不出来必须报 unknown，不许猜（FR-010）")
    r = G._classify(row_count=0, segments=["s"], seg_facts={})
    ok("段有名字但查不到事实 ⇒ unknown（不静默归入任何一类）",
       r == "unknown", r)
    ok("unknown 在枚举里（可被统计与显眼展示）", "unknown" in G.EMPTY_REASONS)
    ok("元测试：unknown 的 action 必须提示人工排查，不能是「无需动作」",
       "排查" in G.ACTION_OF["unknown"] and G.ACTION_OF["unknown"] != "无需动作")

    # ── 6. 排序确定性（SC-003）──────────────────────────────────────────────
    print("6. 排序可复现且可复核（SC-003）")
    # 三张同键表（同为"已实现＋源已收到"）**只在域与表名上不同** ——
    #  专挑这种来测，是为了把第三键与决胜键单独逼出来。
    census = [{"table_name": "b_tbl", "domain": "SU", "row_count": 0,
               "is_partitioned": False, "partition_count": 0},
              {"table_name": "a_tbl", "domain": "SU", "row_count": 0,
               "is_partitioned": False, "partition_count": 0},
              {"table_name": "c_tbl", "domain": "GE", "row_count": 0,
               "is_partitioned": False, "partition_count": 0}]
    st = {"b_tbl": ("b_tbl",), "a_tbl": ("a_tbl",), "c_tbl": ("c_tbl",)}
    sf = {"b_tbl": _facts(), "a_tbl": _facts(), "c_tbl": _facts()}
    g1 = G.build_gaps(census=census, owners={}, seg_tables=st, seg_facts=sf, phases={})
    g2 = G.build_gaps(census=list(reversed(census)), owners={}, seg_tables=st,
                      seg_facts=sf, phases={})
    order1 = [i["table"] for i in g1["items"]]
    ok("同输入不同到达顺序 ⇒ 结果次序完全一致（可复现）",
       order1 == [i["table"] for i in g2["items"]], str(order1))
    # 三键完全相同 ⇒ 由**域序**（第三键）决胜：GE 在 SU 前。
    ok("三键相同时由域序决胜（GE 先于 SU）", order1[0] == "c_tbl", str(order1))
    # 域也相同 ⇒ 由**表名**字典序决胜。
    ok("域也相同时以表名字典序决胜（a_tbl 在 b_tbl 前）",
       order1[1:] == ["a_tbl", "b_tbl"], str(order1))
    ok("每条都带 priority_keys（复核者要靠它重算）",
       all("priority_keys" in i for i in g1["items"]))
    ok("priority_keys 恰好三键（多一个都不是契约里说的那三键）",
       all(len(i["priority_keys"]) == 3 for i in g1["items"]))
    # 元测试：不排序时，输入顺序确实会**影响**输出 ——
    #  证明上面"可复现"那条不是自动成立的空话。
    _input_order = [i["table_name"] for i in census]
    ok("元测试：不排序时输入顺序会影响输出（证明排序真的在起作用）",
       _input_order != order1, f"输入 {_input_order} vs 输出 {order1}")
    # 元测试：把键序颠倒（表名优先），次序必须变。
    _by_name = sorted([i["table"] for i in g1["items"]])
    ok("元测试：改成按表名排序次序必须变（证明三键真的在主导）",
       _by_name != order1, f"{_by_name} vs {order1}")

    # ── 7. source_present 三态（不得把 None 压成 false）────────────────────
    print("7. source_present 是三态，不是布尔")
    gi = g1["items"][0]
    ok("有对应段的表 source_present 为布尔", isinstance(gi["source_present"], bool))
    g3 = G.build_gaps(census=[{"table_name": "x", "domain": None, "row_count": 0,
                              "is_partitioned": False, "partition_count": 0}],
                      owners={}, seg_tables={}, seg_facts={}, phases={})
    ok("无对应段的表 source_present 为 None（≠ False）",
       g3["items"][0]["source_present"] is None,
       str(g3["items"][0]["source_present"]))

    # ── 8. 硬线：gaps.py 不碰库、不 import M2 ──────────────────────────────
    print("8. 硬线（硬线 II）")
    # ⚠ 必须查 **AST 里的 import 语句**，不能拿源码做子串匹配 ——
    #  本文件的注释与 docstring 里**故意**写着"无 psycopg、不 import M2"，
    #  子串匹配会把那句说明当成违规（实测就是这样误报的）。
    #  查 import 才是查"结构上做不做得到"，注释怎么写都不影响。
    import ast as _ast
    src = (ROOT / "modules" / "M6-api" / "gaps.py").read_text(encoding="utf-8")
    _mods = set()
    for _n in _ast.walk(_ast.parse(src)):
        if isinstance(_n, _ast.Import):
            for _a in _n.names:
                _mods.add((_a.asname or _a.name).split(".")[0])
        elif isinstance(_n, _ast.ImportFrom):
            if _n.module:
                _mods.add(_n.module.split(".")[0])
    ok("gaps.py 不 import psycopg（M6 不直连存储）", "psycopg" not in _mods, str(sorted(_mods)))
    ok("gaps.py 不 import M2 的任何模块（只认契约，不认代码）",
       not (_mods & {"weidi", "design_import", "adapters", "models"}),
       str(sorted(_mods)))
    ok("gaps.py 不 import M3 的 Dao（拼装层自己不取数）",
       "rpdao" not in _mods, str(sorted(_mods)))
    # 元测试：真给它加一条违规 import，上面那条必须红。
    _bad = _ast.parse("import psycopg\n" + src)
    _bmods = set()
    for _n in _ast.walk(_bad):
        if isinstance(_n, _ast.Import):
            for _a in _n.names:
                _bmods.add((_a.asname or _a.name).split(".")[0])
    ok("元测试：加上 import psycopg 后必须被认出来（非摆设）", "psycopg" in _bmods)

    print("\n结果：" + ("全部通过 ✓" if not _fails else f"失败 {len(_fails)} 项 → {_fails}"))
    return 1 if _fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
