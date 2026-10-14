#!/usr/bin/env python3
"""契约② 补漏：**桩号类列 MUST 有注释** —— 桩号语义的唯一载体。

为什么要有这一条
================================================================================
库里同一个列名 `station_km` 有**两种语义**，而它俩**都是合法的**：

    earthwork_section.station_km   「这一点在哪」     一个点    332 个不同值
    ditch_segment.station_km       「这一段到哪结束」 区间终点  1 个值（5.805421）

实测（2026-10-14）：`ditch_segment` 6 行 / `slope_segment` 22 行的 station_km
**全部等于 5.805421** —— 因为它们是「一个分段内的 N 组参数」，不是 N 个位置。
两者**同类型、同量纲**，差别只在**消费方怎么读** —— 所以它**不该**占一个代码字段，
而该写在**列注释**里（这是 2026-10-14 定的口径）。

⇒ 于是列注释**成了语义的唯一载体**。而它当时只有 29% 覆盖率：
   62 个桩号类列 → 有注释 18 ｜ ★无注释 44。

★★★ 而 `ditch_segment` 与 `slope_segment` 是**同构表**（同来源 .CTR、同语义、
   列结构几乎一样），**只有 `slope_segment.station_km` 写了注释**
   （「★分段终点桩号。⚠ 不挂 station_id 外键：.CTR 的分段桩号不是 .STA 桩号序列的子集」）。
   ⇒ 这**不是写得不好**，是**写不写全靠人记得**。所以需要一个门。

本门查什么 / 不查什么（★ 这一节是本文件最重要的部分）
================================================================================
★ 查：
    §2 活库里**没有注释**的桩号类列 == LEGACY_UNCOMMENTED（**精确集合相等**，两个方向）
    §3 LEGACY 不许有幽灵条目（清单里的列必须真实存在）
    §4 `sql/*.sql` 声明的「有注释的桩号类列」== 活库里有注释的（**两个方向**）
    §5 逐字一致：活库注释正文 == DDL 注释正文（归一化空白后比较）

★★ 查不了：
    **注释说得对不对**。没有机器能判断「这句注释是不是把区间终点说成了点」。
    ⇒ 所以本门**不是**语义正确性的证明，它只挡住「**完全没写**」这一种。
      把它当成语义门来读，就是本仓反复栽的那种**假绿**（宪法 Principle V：
      「一个永远不会失败的检查，比没有检查更糟」）。

★★★ 为什么 §4/§5 是必要的（不是锦上添花）：
    `test_ddl_migration_parity.py` 比的是**表/列结构**，实测它**不读 pg_description**。
    于是「有人 `ALTER` 了一下活库，DDL 里没写」这种漂移**没有任何检查会发现** ——
    下次重装就静默丢掉，而重装前一切正常。

棘轮（LEGACY_UNCOMMENTED）
================================================================================
44 条不可能一次补完，所以用**棘轮**：清单**只许缩短**。
    · 新增一个没注释的桩号类列        → §2 红（不在清单里）
    · 给清单里的某一列补上注释        → §2 红（清单没缩短，条目成了幽灵）
    · 只改活库、不改 DDL（或反之）    → §4 红
    · 改了 DDL 里的注释正文           → §5 红
清单纯数据，没有逻辑；它的唯一用途就是**被一行行删掉**。

跑法：
    cd scaffold
    uv run --quiet --with "psycopg[binary,pool]==3.2.3" tests/contract/test_column_comments.py
"""

from __future__ import annotations

import re
import sys
import pathlib

_HERE = pathlib.Path(__file__).resolve()
ROOT = _HERE.parents[2]

sys.path.insert(0, str(_HERE.parent))          # 让 `from _env import dsn` 能用
from _env import dsn as _env_dsn                                   # noqa: E402


# ============================================================================
# 数据：棘轮清单。★ 只许缩短。
# ============================================================================
# 何时删一条：给那一列在 `sql/*.sql` 里补上 COMMENT ON COLUMN，并把活库同步。
# 何时加一条：★ 不该发生。新增桩号类列时就该同时写注释；真加了，说明本门失败了。
#
# 这份清单是 2026-10-14 从活库**机器生成**的（不是手抄），生成语句见文件末尾注释。
LEGACY_UNCOMMENTED: tuple[str, ...] = (
    "borrow_pit.econ_back_km",
    "borrow_pit.econ_front_km",
    "design_control_text.station_km",
    "design_file.coverage_from_station_km",
    "design_file.coverage_to_station_km",
    "disease_record.station_km",
    "disease_record.station_text",
    "earthwork_composition.station_km",
    "earthwork_section.station_id",
    "earthwork_transfer.cut_end_km",
    "earthwork_transfer.fill_end_km",
    "earthwork_transfer.fill_start_km",
    "extra_fill.station_km",
    "geometry_point.station_id",
    "land_use_width.station_km",
    "media_file.station_text",
    "monitor_cross_section.station_km",
    "monitor_cross_section.station_text",
    "profile_grade_point.station_km",
    "profile_ground_point.station_id",
    "road_section.end_station_text",
    "road_section.start_station_text",
    "roadbed_design_point.station_id",
    "roadbed_design_point.station_km",
    "roadbed_trench.station_km",
    "spoil_pit.access_station_km",
    "spoil_pit.econ_back_km",
    "spoil_pit.econ_front_km",
    "standard_cross_section.station_km",
    "station_sequence.is_integer_station",
    "station_sequence.station_seq_no",
    "station_sequence.station_text",
    "station_sequence.station_type",
    "structure_control.end_station_km",
    "structure_control.start_station_km",
    "superelev_transition.station_km",
)

# 生成这份清单的查询（活库）：
#   SELECT c.relname||'.'||a.attname
#   FROM pg_class c
#   JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
#   JOIN pg_namespace n ON n.oid=c.relnamespace AND n.nspname='public'
#   LEFT JOIN pg_description d ON d.objoid=c.oid AND d.objsubid=a.attnum
#   WHERE c.relkind='r' AND (a.attname LIKE '%station%' OR a.attname LIKE '%\_km')
#     AND d.description IS NULL
#   ORDER BY 1;


PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [OK] {name}" + (f" —— {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  [!!] {name}" + (f" —— {detail}" if detail else ""))
    return ok


# ============================================================================
# 判据的唯一定义（纯函数）
# ============================================================================
# ★★ 这些函数**不碰数据库、不读文件** —— 好让元测试能拿假数据喂它们。
#    「判据」和「取数」分开，元测试才可能只测判据。

# ★ SQL 侧与 Python 侧必须是**同一个谓词**的两份写法。任何一边漂了，
#   §2 的集合相等就会红 —— 这也是本门顺带守的一件事。
_STATION_SQL = "(a.attname LIKE '%station%' OR a.attname LIKE '%\\_km')"


def is_station_col(col: str) -> bool:
    """桩号类列 = 列名含 `station` 或以后缀 `_km` 结尾。

    ★ 只看**列名**，不看表名。踩过的坑：表名 `station_sequence` 会让
      「按表名匹配」把 `station_sequence.line_id` 也算成桩号列
      （line_id 是外键，不是桩号）。这一条在 2026-10-14 实测踩过。
    """
    c = col.lower()
    return "station" in c or c.endswith("_km")


def audit(
    live_uncommented: set[str],
    live_commented: set[str],
    ddl_commented: dict[str, str],
    ddl_text: dict[str, str],
    live_text: dict[str, str],
    legacy: tuple[str, ...] = LEGACY_UNCOMMENTED,
) -> list[str]:
    """★ 判据的全部逻辑。返回失败原因列表（空 = 通过）。"""
    bad: list[str] = []
    want = set(legacy)

    # ---- §2 活库无注释集合必须精确等于棘轮清单（两个方向）----
    new_bad = sorted(live_uncommented - want)
    if new_bad:
        bad.append(
            f"§2 新增了 {len(new_bad)} 个没注释的桩号类列（必须给它们写 COMMENT）："
            + "、".join(new_bad[:8])
        )
    stale = sorted(want - live_uncommented)
    if stale:
        bad.append(
            f"§2 清单里有 {len(stale)} 条已经**不是**无注释了 —— 棘轮只许缩短，"
            "请从 LEGACY_UNCOMMENTED 里删掉这些条目：" + "、".join(stale[:8])
        )

    # ---- §3 清单不许有幽灵：每条都必须真实存在于活库 ----
    live_all = live_uncommented | live_commented
    ghosts = sorted(want - live_all)
    if ghosts:
        bad.append(
            f"§3 清单里有 {len(ghosts)} 条是**幽灵**（活库里没有这个列，"
            "多半是列被改名/删了）：" + "、".join(ghosts[:8])
        )

    # ---- §4 DDL 声明的注释集合 == 活库有注释的（两个方向）----
    ddl_set = set(ddl_commented)
    only_ddl = sorted(ddl_set - live_commented)
    if only_ddl:
        bad.append(
            f"§4 有 {len(only_ddl)} 条注释只写在 sql/ 里、活库没有"
            "（重装才生效，或者迁移没跑）：" + "、".join(only_ddl[:8])
        )
    only_live = sorted(live_commented - ddl_set)
    if only_live:
        bad.append(
            f"§4 有 {len(only_live)} 条注释只在活库里、sql/ 里没有"
            "（★ 有人直接 ALTER 了库；重装会静默丢掉）：" + "、".join(only_live[:8])
        )

    # ---- §5 逐字一致 ----
    drift = sorted(k for k in (ddl_set & live_commented) if ddl_text.get(k) != live_text.get(k))
    if drift:
        bad.append(
            f"§5 有 {len(drift)} 条注释**正文不一致**（DDL 与活库说了不同的话）："
            + "、".join(drift[:5])
        )

    return bad


def _norm(s: str) -> str:
    """注释正文归一化：空白折叠。★ 只折叠空白 —— 不动标点和字，免得把真漂移抹掉。"""
    return re.sub(r"\s+", " ", s or "").strip()


# ============================================================================
# 取数（只在这里碰库/碰文件）
# ============================================================================

_SQL_LIVE = f"""
SELECT c.relname || '.' || a.attname,
       coalesce(d.description, '')
FROM pg_class c
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
LEFT JOIN pg_description d ON d.objoid = c.oid AND d.objsubid = a.attnum
WHERE c.relkind = 'r' AND {_STATION_SQL}
ORDER BY 1
"""


def live_station_comments(cur) -> tuple[set[str], set[str], dict[str, str]]:
    """活库里的桩号类列，按「有没有注释」分开。返回 (无注释, 有注释, 有注释的正文)。"""
    cur.execute(_SQL_LIVE)
    un: set[str] = set()
    cm: set[str] = set()
    txt: dict[str, str] = {}
    for key, desc in cur.fetchall():
        key = key.strip().lower()
        if desc:
            cm.add(key)
            txt[key] = _norm(desc)
        else:
            un.add(key)
    return un, cm, txt


_RX_DDL_HEAD = re.compile(
    r"(?im)^[ \t]*comment[ \t]+on[ \t]+column[ \t]+([a-z_0-9]+)\.([a-z_0-9]+)[ \t]+is[ \t]*"
)


def _skip_ws_and_line_comments(src: str, pos: int) -> int:
    n = len(src)
    while pos < n:
        if src[pos] in " \t\r\n":
            pos += 1
            continue
        if src.startswith("--", pos):
            nl = src.find("\n", pos)
            pos = n if nl < 0 else nl + 1
            continue
        break
    return pos


def _read_literal_chain(src: str, pos: int) -> tuple[str, int]:
    """按 **SQL 语义**读一串相邻字符串字面量并拼接。

    ★★★ 为什么不能简单地把 `'a'\\n'b'` 当成 `'a' 'b'`：
      SQL 里**相邻字面量之间只隔空白时会被拼接**（和 C 一样），空白**不进值**。
      本仓真的这么写了 —— 实测 `alignment_pi.station_km` 与
      `cross_section_ground_point.station_km` 的注释都是跨两行的两个字面量：

          COMMENT ON COLUMN alignment_pi.station_km IS
            '交点桩号（…前提有误：'
            '.JD 本就给出该值，…';

      库里的值是 `前提有误：.JD` —— **没有空格**。
      第一版抽取器把中间的引号对留成了 `' '`（引号 + 换行 + 引号，空白折叠后成为
      `' '`），于是 §5 报了 2 条**假红**。★ 假红也是失败的一种：
      它会训练人忽略这条检查。修的是抽取器，不是判据。

    返回 (拼接后的文本, 越过整串之后的位置)。
    """
    out: list[str] = []
    n = len(src)
    pos = _skip_ws_and_line_comments(src, pos)
    while pos < n and src[pos] == "'":
        pos += 1
        buf: list[str] = []
        while True:
            if pos >= n:
                raise AssertionError("DDL 里的字符串字面量没有收尾引号")
            ch = src[pos]
            if ch == "'":
                if pos + 1 < n and src[pos + 1] == "'":      # '' = 转义的单引号
                    buf.append("'")
                    pos += 2
                    continue
                pos += 1
                break
            buf.append(ch)
            pos += 1
        out.append("".join(buf))
        nxt = _skip_ws_and_line_comments(src, pos)
        if nxt < n and src[nxt] == "'":
            pos = nxt
            continue
        break
    if not out:
        raise AssertionError("COMMENT ON COLUMN … IS 后面不是字符串字面量")
    return "".join(out), pos


def ddl_station_comments(sql_dir: pathlib.Path) -> dict[str, str]:
    """`sql/*.sql` 里声明的桩号类列注释正文（按 SQL 语义解码）。

    ★★ 必须扫**全部** sql 文件，不能只扫 10_ddl_v0.6.sql：
       `alignment_pi.station_km` 的注释在 88_migrate_v05_pi_station.sql 里，
       只扫主 DDL 会得到一个「差一条」的假红（实测踩过）。
    ★ 头锚定在**行首**：`-- COMMENT ON COLUMN …` 这种被注释掉的 DDL 不该算数。
    """
    out: dict[str, str] = {}
    for f in sorted(sql_dir.glob("*.sql")):
        src = f.read_text(encoding="utf-8", errors="replace")
        for m in _RX_DDL_HEAD.finditer(src):
            t, c = m.group(1).lower(), m.group(2).lower()
            if not is_station_col(c):
                continue
            text, pos = _read_literal_chain(src, m.end())
            tail = _skip_ws_and_line_comments(src, pos)
            if tail >= len(src) or src[tail] != ";":
                raise AssertionError(
                    f"{f.name}: {t}.{c} 的 COMMENT 字面量后面不是分号 → {src[tail:tail+20]!r}"
                )
            key = f"{t}.{c}"
            text = _norm(text)
            # 同一列可能被主 DDL 与迁移各写一次。
            # ★ 两份不一致 → 显式暴露（否则「哪份算数」取决于文件名排序，是个隐式规则）。
            if key in out and out[key] != text:
                raise AssertionError(
                    f"{key}: 多处 COMMENT 正文不一致 —— 先读到 {out[key]!r}，"
                    f"这次是 {text!r}（在 {f.name}）"
                )
            out[key] = text
    return out


def _connect():
    """真库连接。★ 无 .env 且无 PG_DSN → 返回 None（调用方按「跳过」处理）。"""
    import psycopg

    d = _env_dsn()
    if not d:
        return None
    return psycopg.connect(d)


# ============================================================================
def main() -> int:
    print("=" * 78)
    print("契约② 补漏：桩号类列 MUST 有注释（桩号语义的唯一载体）")
    print("=" * 78)

    sql_dir = ROOT / "sql"
    if not sql_dir.is_dir():
        print(f"  [!!] 找不到 sql 目录：{sql_dir}")
        return 1

    # ---------------------------------------------------------------- §1
    print("\n§1  分母从数据里数出来（不写死）")
    ddl = ddl_station_comments(sql_dir)
    check("§1 sql/ 里至少声明了一条桩号列注释（谓词没写飘）", len(ddl) > 0, f"{len(ddl)} 条")

    conn = _connect()
    if conn is None:
        # ★★ 本仓的假绿就长这样：显示「跳过」，读起来却像「通过」。
        #    所以这里**分开对待**：.env 不存在 = 环境没数据库，跳过；
        #    .env 在却拼不出 DSN = **配置错**，一律判失败。
        envf = ROOT / ".env"
        if envf.exists():
            check("§1 有 .env 却拼不出 DSN —— 这是配置错，不是「没数据库」", False,
                  str(envf))
            print(f"\n通过 {PASS} ｜ 失败 {FAIL}")
            return 1
        print("  [SKIP] 既无 PG_DSN 也无 scaffold/.env —— 本机没有可连的库，跳过")
        print(f"\n通过 {PASS} ｜ 失败 {FAIL}")
        return 0

    try:
        with conn.cursor() as cur:
            un, cm, live_txt = live_station_comments(cur)
            total = len(un) + len(cm)
            check("§1 活库里桩号类列数 > 0（谓词没写飘）", total > 0, f"{total} 列")
            print(f"      活库：桩号类列 {total} ｜ 有注释 {len(cm)} ｜ ★无注释 {len(un)}")
            print(f"      sql/：声明了 {len(ddl)} 条桩号列注释")

            # ------------------------------------------------------------ §2~§5
            print("\n§2~§5  集合相等 + 逐字一致")
            bad = audit(un, cm, ddl, ddl, live_txt)
            if bad:
                for b in bad:
                    check(b.split("：")[0].split("（")[0][:70], False, b)
            else:
                check("§2 无注释集合 == 棘轮清单（两个方向）", True, f"{len(un)} 条")
                check("§3 棘轮清单无幽灵条目", True, f"{len(LEGACY_UNCOMMENTED)} 条都在库里")
                check("§4 DDL 声明 == 活库（两个方向）", True, f"{len(ddl)} 条")
                check("§5 注释正文逐字一致", True)

            # ------------------------------------------------------------ §6
            print("\n§6  元测试：判据必须真的会红（否则它是个空转的检查）")
            base = dict(
                live_uncommented=set(un),
                live_commented=set(cm),
                ddl_commented=dict(ddl),
                ddl_text=dict(ddl),
                live_text=dict(live_txt),
            )

            # 6a 新增一个没注释的桩号类列 → 必须红
            d = dict(base)
            d["live_uncommented"] = set(un) | {"brand_new_table.station_km"}
            r = audit(**d)
            check("§6a 新增无注释的桩号类列 → 红", any("§2" in x and "新增" in x for x in r),
                  f"报 {len(r)} 条")

            # 6b 清单里的列被补上了注释（清单没缩短）→ 必须红
            # ★ 受害者必须**从棘轮清单里挑**，不能从「现在无注释的集合」里挑：
            #   后者会被外部环境干扰 —— 实测红验证时我给库里加了个探针列，
            #   它按字典序排在 `borrow_pit` 前面就被挑走了，于是 6c 报了个
            #   **与判据无关的失败**。元测试的假数据要自己带，不要吃环境的。
            victim = sorted(LEGACY_UNCOMMENTED)[0]
            assert victim in un, f"前置不成立：{victim} 并不在无注释集合里"
            d = dict(base)
            d["live_uncommented"] = set(un) - {victim}
            d["live_commented"] = set(cm) | {victim}
            d["ddl_commented"] = dict(ddl)
            d["ddl_commented"][victim] = "补了新注释"
            d["live_text"] = dict(live_txt)
            d["live_text"][victim] = "补了新注释"
            r = audit(**d)
            check(f"§6b 清单条目「{victim}」被补上注释但没从清单删 → 红",
                  any("§2" in x and "只许缩短" in x for x in r), f"报 {len(r)} 条")

            # 6c 清单里的列被改名/删了（幽灵）→ 必须红
            ghost = sorted(LEGACY_UNCOMMENTED)[0]
            assert ghost in un, f"前置不成立：{ghost} 并不在无注释集合里"
            d = dict(base)
            d["live_uncommented"] = set(un) - {ghost}
            r = audit(**d)
            check(f"§6c 清单条目「{ghost}」在库里不存在 → 红（幽灵）",
                  any("§3" in x for x in r), f"报 {len(r)} 条")

            # 6d 只改活库、不改 DDL → 必须红
            d = dict(base)
            d["live_commented"] = set(cm) | {"hand_edited.station_km"}
            d["live_text"] = dict(live_txt)
            d["live_text"]["hand_edited.station_km"] = "手改的"
            r = audit(**d)
            check("§6d 注释只在活库、sql/ 里没有 → 红", any("§4" in x for x in r), f"报 {len(r)} 条")

            # 6e 只改 DDL、不改活库 → 必须红
            d = dict(base)
            d["ddl_commented"] = dict(ddl)
            d["ddl_commented"]["ddl_only.station_km"] = "只在 DDL 里"
            d["ddl_text"] = dict(ddl)
            d["ddl_text"]["ddl_only.station_km"] = "只在 DDL 里"
            r = audit(**d)
            check("§6e 注释只在 sql/ 里、活库没有 → 红", any("§4" in x for x in r), f"报 {len(r)} 条")

            # 6f 两边都有、正文不同 → 必须红
            # ★ 从**两边都有**的列里挑（§5 只在交集上比较）。
            k = sorted(set(cm) & set(ddl))[0]
            d = dict(base)
            d["ddl_text"] = dict(ddl)
            d["ddl_text"][k] = "改过的正文"
            r = audit(**d)
            check(f"§6f 「{k}」两边正文不同 → 红", any("§5" in x for x in r), f"报 {len(r)} 条")

            # ------------------------------------------------------------ §7
            print("\n§7  元测试（真库）：SQL 判据必须真的捞得到新表")
            cur.execute("BEGIN")
            try:
                cur.execute(
                    "CREATE TABLE _ctype_probe_zzz ("
                    "  id bigint, station_km_probe numeric(8,2), probe_km numeric(8,2), notit text)"
                )
                probe_un, probe_cm, _ = live_station_comments(cur)
            finally:
                cur.execute("ROLLBACK")
            check("§7a 表名不是桩号列不算数（notit 不该被捞到）",
                  "_ctype_probe_zzz.notit" not in probe_un)
            check("§7b 列名含 station 的要捞到",
                  "_ctype_probe_zzz.station_km_probe" in probe_un)
            check("§7c 列名以 _km 结尾的要捞到",
                  "_ctype_probe_zzz.probe_km" in probe_un)
            # ★ 探针表回滚后必须真的没了 —— 否则本门自己污染了被测对象
            cur.execute(
                "SELECT count(*) FROM pg_class WHERE relname = '_ctype_probe_zzz'"
            )
            check("§7d 探针表已回滚（本门不许污染被测对象）",
                  cur.fetchone()[0] == 0)
    finally:
        conn.close()

    print("")
    print(f"通过 {PASS} ｜ 失败 {FAIL}")
    print("全部通过" if FAIL == 0 else "有失败项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
