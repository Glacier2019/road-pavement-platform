"""空表缺口归因（契约 ④：/v1/catalog/gaps）。

回答三个问题：**这张表为什么空、归谁、先动哪条**。

## 为什么归因必须"拼装"，而不是一个模块自己算

归因需要三类事实，分属两个模块，谁也不能替谁：

| 事实 | 持有者 | 为什么不能换人 |
|---|---|---|
| 表里有几行 | M3 rpdao | 硬线：读只经 M3 |
| 表的归属模块 | M3 TABLE_OWNER | 已在 M3，抄一份就会漂 |
| 段实现了没 / 源收到没 | M2 | 解析器与导入记录都在它那边 |

所以本模块**只做拼装**：M3 出表事实、M2 出段事实，在这里按「段 ↔ 表」合并。
它自己**不碰数据库**（无 psycopg、无 SQL），也**不 import M2**
（那会造成代码依赖，破坏"模块可拔插"）。
"""

from __future__ import annotations

from typing import Any

#: 空因枚举。**封闭**：扩枚举须走契约变更。
EMPTY_REASONS: tuple[str, ...] = (
    "has_data",                    # 有数据 —— 不编造空因
    "source_ready_not_imported",   # 源已收到，可直接导入
    "source_needs_conversion",     # 源已收到，但需先转换（.tsf → .tsftxt）
    "source_absent",               # 该段需要这个文件，但从未收到过
    "module_not_built",            # 适配器还没实现这个段
    "upstream_pending",            # 不是导入来的，是别的模块算出来的
    "unknown",                     # 判不出来 —— **必须暴露，不得静默**
)

#: 域序 —— 排序的第三键。取自七域数据流方向，
#  不用"重要性"这类主观量表：那种词无法复核，会让排序变成不可证伪。
DOMAIN_ORDER: tuple[str, ...] = ("GE", "SU", "RE", "LO", "WE", "TE", "DE")

#: 空因 → 建议动作。★ 措辞**不许**过度承诺：
#  source_ready_not_imported 只能说"源已收到"，不能说"导入一定成功"。
ACTION_OF: dict[str, str] = {
    "has_data": "无需动作",
    "source_ready_not_imported": "跑一次设计导入",
    "source_needs_conversion": "先跑 tools/tsf2txt.py 转换，再导入",
    "source_absent": "向外部索取该文件后导入",
    "module_not_built": "为该段写适配器",
    "upstream_pending": "等上游模块产出",
    "unknown": "人工排查（系统判不出空因，已如实报出）",
}

#: 空因 → 所需改动模块数（排序第一键）。
#  "改几个模块"是"这条链要拉几个组一起干"的直接代理 —— 比主观打分可复核。
_ACTION_COST: dict[str, int] = {
    "has_data": 0,
    "source_ready_not_imported": 1,   # 只跑导入，不动代码
    "source_needs_conversion": 1,     # 跑一次转换脚本，不动代码
    "source_absent": 2,               # 要外部给文件 + 一次导入
    "upstream_pending": 3,            # 要别的模块先做完
    "module_not_built": 3,            # 要写解析器 + 测试 + 契约
    "unknown": 9,                     # 未知一律排最后，且显眼
}


def _classify(*, row_count: int, segments: list[str],
              seg_facts: dict[str, dict[str, Any]],
              known: bool = True) -> str:
    """判空因。**短路顺序是被钉住的**，改动前先读下面这段。

    ⚠⚠ 顺序必须是 [has_data] → [有对应段?] → [source] → [implemented]

    ① has_data 最前：表里有数据就是有数据，**不因"源缺失"改口**。

    ② source 必须**先于** implemented。这一条是**真实踩过的坑**：
       weidi/__init__.py 里记着，早先写成"在 IMPLEMENTED 里才报 source_absent，
       否则报 not_supported"，结果 .3DR（本工程没生成）被报成 not_supported，
       **把真正的原因盖掉了**。诊断要说的是**真正**的那一个：
       "源根本没给" 与 "给了但我不会解析" 是两件完全不同的事 ——
       前者要去找人要文件，后者要写代码。
    """
    # ① 有数据就到此为止，不编造空因。
    if row_count > 0:
        return "has_data"

    # ② 无对应段 ⇒ 它不是导入来的，是别的模块算出来的（下游产出表）。
    #    必须在判"源缺失"**之前**分流：把 alarm_record 报成"源缺失"
    #    会让人去外面找一份根本不存在的外部数据。
    #
    #  ⚠ 但"无对应段"有**两种**含义，不能用一句话答完（T037 实测撞到）：
    #      · 我知道这张表归谁、也知道它是算出来的 ⇒ upstream_pending
    #        （这是正常的等待，动作明确：等上游做完）
    #      · 我连它归谁都不知道           ⇒ unknown
    #        （这是**认知缺口**，不是等待）
    #    两者报成一样，第二类就会被伪装成"在等上游" → 等一个永远不会来的东西，
    #    而且**永远查不出来**（因为看起来一切正常）。用 known（有归属登记）区分。
    if not segments:
        return "upstream_pending" if known else "unknown"

    facts = [seg_facts[s] for s in segments if s in seg_facts]
    if not facts:
        # 段有名字但查不到事实 —— 两模块认知不一致，**不猜**。
        return "unknown"

    present = [f for f in facts if f.get("source_state") == "received"]
    pending = [f for f in facts if f.get("source_state") == "pending"]
    missing = [f for f in facts if f.get("source_state") == "absent"]

    if present:
        if all(f.get("implemented") for f in present):
            return "source_ready_not_imported"
        return "module_not_built"

    if pending:
        # 源**已收到**，但它是"还要先加工一下"的形态。
        #  实测本工程正是这一支：.tsf 收到了（Access 二进制），
        #  而适配器吃的是 tsf2txt.py 摊出来的 .tsftxt —— 那一步**没人跑过**。
        #  ★ 这一支若并进 source_absent，用户会去"找一份还没有的文件"，
        #    而真正该做的是**跑一次转换**。两件事的可行动作完全不同。
        return "source_needs_conversion"

    if missing:
        # ⚠ 顺序不能反：先判 source、再判 implemented。
        #   反了就会把"源没给"报成"没实现" —— 正是 ② 里那个坑。
        if all(f.get("implemented") for f in missing):
            return "source_absent"
        return "module_not_built"

    return "unknown"


def build_gaps(
    *,
    census: list[dict[str, Any]],
    owners: dict[str, str],
    seg_tables: dict[str, tuple[str, ...]],
    seg_facts: dict[str, dict[str, Any]],
    phases: dict[str, str],
) -> dict[str, Any]:
    """把 M3 的表事实与 M2 的段事实拼成缺口清单。

    seg_tables 是「段 → 表」映射（由 M2 给出，唯一入口）；这里**反向**取出
    「表 → 段」。之所以不让 M6 自己推映射：段名==表名只是**约定**，还有两个
    已登记的例外，把约定写死在 M6 就等于又多一份会漂的陈述。
    """
    table_to_segs: dict[str, list[str]] = {}
    for seg, tables in seg_tables.items():
        for t in tables:
            table_to_segs.setdefault(t, []).append(seg)

    items: list[dict[str, Any]] = []
    for row in census:
        t = row["table_name"]
        segs = sorted(table_to_segs.get(t, []))
        rc = int(row["row_count"])
        # known = 这张表在 TABLE_OWNER 里有归属登记。
        #  用它区分"知道是下游产出表"与"连归谁都不知道"（见 _classify ② ）。
        reason = _classify(row_count=rc, segments=segs, seg_facts=seg_facts,
                           known=bool(owners.get(t)))
        facts = [seg_facts[s] for s in segs if s in seg_facts]
        suffs = sorted({f["suffix"] for f in facts if f.get("suffix")})
        owner = owners.get(t)
        has_src = bool(facts) and any(
            f.get("source_state") != "absent" for f in facts)
        items.append({
            "table": t,
            "domain": row.get("domain"),
            "row_count": rc,
            "has_data": rc > 0,
            "is_partitioned": bool(row.get("is_partitioned")),
            "partition_count": int(row.get("partition_count") or 0),
            "owner": owner,
            "phase": phases.get(owner) if owner else None,
            "empty_reason": reason,
            "segments": segs,
            "suffixes": suffs,
            # 三态：true / false / None（None = 该表不对应任何段）。
            # 把 None 压成 false 就是把"下游产出"说成"源缺失"。
            "source_present": (None if not facts else has_src),
            "action": ACTION_OF[reason],
            # 三键**必须暴露**：复核者要靠它独立复算排序（SC-003）。
            "priority_keys": [
                _ACTION_COST[reason],
                has_src,
                (DOMAIN_ORDER.index(row["domain"])
                 if row.get("domain") in DOMAIN_ORDER else 99),
            ],
        })

    # 稳定排序：三键之后以表名字典序决胜 ⇒ 同输入必得同一次序（可复现）。
    items.sort(key=lambda i: (tuple(i["priority_keys"]), i["table"]))

    counts: dict[str, int] = {r: 0 for r in EMPTY_REASONS}
    for i in items:
        counts[i["empty_reason"]] = counts.get(i["empty_reason"], 0) + 1

    return {
        "logical_table_count": len(items),
        "with_data_count": sum(1 for i in items if i["has_data"]),
        "empty_count": sum(1 for i in items if not i["has_data"]),
        "unknown_count": counts.get("unknown", 0),
        "reason_counts": counts,
        "items": items,
    }
