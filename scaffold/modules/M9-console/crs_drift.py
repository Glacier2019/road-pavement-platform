"""「文件与库一致」的比对（契约变更工单 005 / FR-014 / SC-012）。

★ 这个模块存在的理由，以及它为什么在 M9 里
===========================================================================

T027 原文要求「在 ``scaffold/modules/M9-console/app.py`` 与
``scaffold/tests/e2e/quickstart_probe.py`` 加『文件与库一致』检查的实现」。

**按字面写不成。** M9 的硬线是「不得直连数据库」（读只经 M3 rpdao）——
``app.py`` 里没有 ``psycopg``、没有 ``rpdao``、compose 也不给它 ``PG_DSN``。
让 M9 自己去看库，等于为了一个检查把这条硬线拆掉。

所以三者这样分工，每一方都守住自己那一层：

    ┌─ M9 控制台 ──── 比「**文件**」vs「**契约**给的取值」
    │                 取值经 /gw → M6 → M3 拿到，M9 始终看不见库
    ├─ quickstart_probe  比「文件」vs「**DAO** 读出的库」
    └─ 契约测试 T025      同上，且是**权威判据**（离线、可反复跑）

比对逻辑只能有一份 —— 抄三遍必然漂开，而漂开之后**没有任何东西会红**。
所以它落在这里：``crs.yaml`` 本来就归 M9 管，而 M9 的职责原文就是
「显示当前基准 + 这个值是否已确认」。本模块是**纯函数**，不碰网络也不碰库。

★ 谁也别把数据库客户端引进这个文件
---------------------------------------------------------------------------
`srid_exists` 那一类需要库的函数**不许**长在这里。一旦这里 ``import`` 了
``psycopg``，M9 的硬线就被一个「顺手」的改动拆掉了，而且是静默的。
``test_console.py`` 的 AST 断言盯着 ``app.py``；本文件由契约测试的同一条
断言一并覆盖（T031）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

__all__ = ["parse_evidence", "compare", "compare_detailed", "describe",
           "FIELDS", "ABSENT", "MISSING"]


class _Absent:
    """**「这一条我没得比」** —— 与 `None`（「值是空的」）严格区分。

    ★★ 为什么必须把这两个分开（宪法 V「**没去看 ≠ 看了没有**」）
    ---------------------------------------------------------
    M9 只经 `/gw` → M6 拿取值。而 M6 的两条 HTTP 通路
    （`/v1/geometry/sections` 与 `…/{id}/geo`）**都不带 `crs_evidence`** ——
    它在 M6 那边被 `_CRS_ROW_KEYS` 剥掉了。于是：

      · 把「通道不提供」当成不一致 → M9 这条巡检**永远红**，
        而它红的原因与文件、与库都无关。一条永远红的检查等于没有检查。
      · 把它当成「一致」 → 那更糟：`crs_evidence` 是这份基准的**论证史**，
        悄悄放过等于宣称「依据也核对过了」，而其实**根本没看**。

    所以第三种答案必须存在，而且必须**被报出来**：不是 `True`、不是 `False`、
    是「**未比对，原因是这个通道不提供该字段**」。

    ★ 这不是给自己留后门：`compare_detailed` 把 `skipped` 单独返回，
      `describe` 一定把它写进报告，§18d 有元测试钉住
      「**源侧真有这个字段且不一致时必须红**」——
      即「跳过」只在源侧**确实没有**该字段时发生。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "ABSENT"


#: 「这一条没得比」的唯一实例。
ABSENT = _Absent()

#: `skipped` 列表里用的措辞模板。
MISSING = "（此通道不提供该字段，未比对）"

# ★ 要比对的五项，逐字来自 T027：
#   ready / assumed / zone_number / central_meridian_deg / epsg
# ★ 另加 evidence —— 种子文件自己写死了「一条不多一条不少」，
#   不去比它就等于那句话没有执行者。
FIELDS: tuple[str, ...] = (
    "ready", "assumed", "zone_number", "central_meridian_deg", "epsg", "evidence",
)

# 库里的取值 → 本模块用的平面字典。键名与文件里的 **契约名** 一致，
# 而不是库里那些 `crs_` 前缀的内部列名 —— 内部列名一改，比对就静默失配。
_DB_KEYS = {
    "epsg": "epsg",
    "zone_number": "zone_number",
    "central_meridian_deg": "central_meridian_deg",
    "assumed": "assumed",
    "evidence": "evidence",
}


def _num(v: Any) -> float | None:
    """数值化。`117`（文件里的整数）与 `Decimal('117.000000')`（库里的 numeric）
    必须**相等** —— 比文本的话它们永远不等，而那不是不一致，是记法不同。"""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    try:
        return float(Decimal(str(v).strip()))
    except (InvalidOperation, ValueError):
        return None


def parse_evidence(text: Any) -> list[str]:
    """`crs_evidence` 那一列 → 一条一条的依据。

    ★ 编码是「**一条一行**，行首可有 `N. ` 编号」（见 sql/99_seed_crs_39band.sql）。
      不按 JSON 解析 —— 那一列的类型是 `text`，它的注释写着「给人看的」，
      所以里面完全可能出现人写的散文。

    容忍四种形状，一种都不许报错：
      · `"1. a\\n2. b"`  → ["a", "b"]        （种子的样子）
      · `"a\\nb"`         → ["a", "b"]        （没有编号）
      · `"a"`             → ["a"]             （只有一条）
      · `None` / `""`     → []                （没填）

    ★ 一行都不许吞：**空行只跳过，不合并**。把两条粘成一条会让「一条不多
      一条不少」这句判据失去意义。
    """
    if text is None:
        return []
    out: list[str] = []
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            continue
        # 剥掉行首的 `N.` / `N、` / `N)` 编号
        head, sep, rest = line.partition(".")
        if sep and head.strip().isdigit():
            line = rest.strip()
        out.append(line)
    return out


def _file_frame(doc: dict[str, Any]) -> dict[str, Any]:
    """从文件文档里取出要比的那五项，**用契约名**。

    ★ `epsg` 在文件里叫 `epsg_after_confirm`（"确认之后填的 EPSG"）。
      那是个历史名字：当时 `assumed` 还能是 true，需要一个只在该状态下
      才填的字段。库里没有这个二段式 —— `crs_epsg` 本身就是权威字段。
      所以这里是**映射，不是改名**，与 app.py 的 `_geo_crs` 同一处理。
    """
    gf = (doc or {}).get("geographic_frame") or {}
    return {
        "ready": gf.get("ready"),
        "assumed": gf.get("assumed"),
        "zone_number": gf.get("zone_number"),
        "central_meridian_deg": gf.get("central_meridian_deg"),
        "epsg": gf.get("epsg_after_confirm"),
        # ★ 文件侧也走 `parse_evidence`。
        #   两侧必须用**同一个**规范化 —— 库那边存的是带 `N. ` 编号的原文
        #   （种子文件原样），文件这边历来不带编号。只规范化一侧的话，
        #   判据会因为「编号」这种排版差异永远红，而两次编辑谁也没改内容。
        "evidence": parse_evidence(
            "\n".join(str(x) for x in (gf.get("evidence") or []))),
    }


def _db_frame(db: dict[str, Any]) -> dict[str, Any]:
    """从库取值（平面字典）里取出同样五项。

    ★★ 键**在不在**决定三态，而不是值：
         · 键不在   → `ABSENT`（这个通道不提供该字段 → **不比对、但要报出来**）
         · 键在、值为 None → 正常值，「库里是空的」，照比对
      这是**有意的不对称**：文件是**被审的对象**，它缺一个字段是文件自己的毛病，
      要红；源侧是**审的人**，它看不见一个字段时只能弃权，不能判罪。
    """
    out: dict[str, Any] = {}
    for k, src in _DB_KEYS.items():
        out[k] = db[src] if src in db else ABSENT
    # ★ `ready` 库里**没有对应的列**，它是**派生的**：
    #   「这个基准能不能用」＝「库里到底有没有一个 EPSG」。
    #   文件里那份 ready=false 而库里有值，是真实的不一致 —— 要报出来。
    out["ready"] = ABSENT if "epsg" not in db else (db.get("epsg") is not None)
    # evidence 在库里是一整块 text，到这儿才拆成条
    out["evidence"] = (ABSENT if "evidence" not in db
                       else parse_evidence(db.get("evidence")))
    return out


def _eq(field: str, a: Any, b: Any) -> bool:
    """按字段语义比较，不按 Python 的 `==` 一刀切。"""
    if field == "evidence":
        # 逐条比，且**顺序也算** —— 换了顺序说明有人重排了依据，
        # 那是一次没有人审过的编辑。
        return list(a or []) == list(b or [])
    if field in ("zone_number", "central_meridian_deg", "epsg"):
        return _num(a) == _num(b)
    # ready / assumed 是布尔：先归一，免得 1/True/"true" 三种记法互不相等
    def _b(v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            return v.strip().lower() in ("true", "1", "yes", "是")
        return bool(v)
    return _b(a) == _b(b)


def compare_detailed(doc: dict[str, Any],
                     db: dict[str, Any]) -> tuple[list[str], list[str]]:
    """`compare` 的完整形态：返回 **(不一致清单, 未比对清单)**。

    ★ 第二项绝不能省。宪法 V：「**没去看 ≠ 看了没有**」。
      一条只看第一项的调用会看到「不一致清单是空的」就宣称一致 ——
      而真相可能是**五项里有四项这个通道根本没提供**。
      所以「一致」的正确判据是「不一致为空 **且** 未比对为空」，
      M9 的路由与 `describe` 都按这条写。
    """
    f, d = _file_frame(doc), _db_frame(db)
    bad: list[str] = []
    skipped: list[str] = []
    for k in FIELDS:
        dv = d.get(k)
        if dv is ABSENT:
            skipped.append(k)
            continue
        if _eq(k, f.get(k), dv):
            continue
        if k == "evidence":
            fe, de = list(f.get("evidence") or []), list(dv or [])
            bad.append(
                f"evidence：库里有 {len(de)} 条，文件里有 {len(fe)} 条"
                + ("（条数相同但内容或顺序不同）" if len(fe) == len(de) else "")
                + f"；库={de!r}；文件={fe!r}")
            continue
        bad.append(f"{k}：库里是 {dv!r}，文件里是 {f.get(k)!r}")
    return bad, skipped


def compare(doc: dict[str, Any], db: dict[str, Any]) -> list[str]:
    """比对一份 `crs.yaml` 文档与一组库取值，返回**不一致清单**。

    空列表 = 一致。每一条都说清**哪个字段、库里是什么、文件里是什么** ——
    T027 逐字要求这三样，因为只说「不一致」的话，接手的人还得自己去翻两份
    东西才能知道往哪边改。

    ★ 参数顺序是 (文件, 库)，报出来的措辞按这个顺序写。
      反过来传会让每一条提示都把两边说反 —— 而它**读起来仍然通顺**。

    ⚠ 这个签名**看不到「未比对」那一项**。需要判断「到底一致了没有」时
      用 `compare_detailed`：空清单只是「没有冲突」，不等于「都看过了」。
    """
    return compare_detailed(doc, db)[0]


def describe(mismatches: list[str], *, source: str,
             skipped: list[str] | None = None) -> str:
    """给人和给日志用的一段话。"""
    lines: list[str] = []
    if mismatches:
        lines.append("★ crs.yaml 与库不一致 —— 文件是**派生产物**，它该跟着库走：")
        lines.append(f"  （比对来源：{source}）")
        lines += [f"  · {m}" for m in mismatches]
        lines.append("  修法：重跑导出（tools/export_crs_yaml.py），不要手改文件。")
    elif skipped:
        lines.append("crs.yaml 与库**已比对的字段**全部一致"
                     f"（比对来源：{source}）。")
    else:
        return f"文件与库一致（比对来源：{source}）"
    if skipped:
        # ★ 「没得比」必须留下痕迹，而且要说清**是谁的局限** ——
        #   不说的话，下一个人会把「一致」当成「依据也核对过了」。
        lines.append(f"  ⚠ 未比对 {len(skipped)} 项：{'、'.join(skipped)}"
                     f"{MISSING}")
        lines.append("     这不是「一致」，是「**这个通道看不到它**」。"
                     "要核这一项请用 quickstart_probe（走 DAO）。")
    return "\n".join(lines)
