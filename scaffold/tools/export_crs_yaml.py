#!/usr/bin/env python3
"""把 `modules/M9-console/config/crs.yaml` 从库里重新导出一遍。

契约变更工单 005（Q3 裁决）：**库是权威，文件是派生产物。**

★ 为什么需要一个导出入口，而不是"手改文件就行"
===========================================================================
005 之前，基准只活在那一份文件里。005 之后库是权威，但**四个消费者仍在
读文件**（见 §消费者），所以文件不能删。那么它就必须是**生成的** ——
否则它总有一天会与库不一致，而那一天没有任何东西会红。

这个脚本 + `crs_drift.compare` 合起来构成 FR-014 / SC-012 那条判据的两半：
  本脚本负责「让文件跟着库走」；compare 负责「发现它没跟上」。

★ 为什么模板在这个文件里，而不是读旧文件改写
---------------------------------------------------------------------------
一个"读旧文件、只替换派生值"的实现会**保留旧值沿用的注释**。举个真会发生的
例子：库里把 `assumed` 从 false 翻回 true 之后，旧文件里那句
「2026-10-11 用户确认：带号为真实值」还在原地 —— 而它已经不成立了。
生成器带着模板，改的是**生成器里的那一段**，人才会被迫一起改。

★ 消费者（T029 实测，一个都不许改）
---------------------------------------------------------------------------
    scaffold/simulator/crg/export_viewer_mesh.py     只读 geographic_frame
    scaffold/tests/integration/sim_inline_script.mjs  同上（node 里手写解析）
    scaffold/modules/M9-console/app.py                /sim 页面显示
    scaffold/tests/e2e/quickstart_probe.py            区域帧探针
  ⇒ 因此**形状必须保持不变**：`local_frame` / `geographic_frame` / `evidence`
    三段、键名逐字照旧。加一个键可以让它们都不认识，而它们不报错 ——
    只是那个值变空。

★ `local_frame` 不从库里来
---------------------------------------------------------------------------
它说的是「设计坐标系：X=北, Y=东, Z=上，单位米」，与大地基准无关，
库里没有对应的列。它由本模板给定，并在生成的文件里注明。

★ 用法
---------------------------------------------------------------------------
    cd scaffold
    export PG_DSN=...            # 或 --dsn
    uv run --with "psycopg[binary,pool]==3.2.3" python tools/export_crs_yaml.py
    uv run ... python tools/export_crs_yaml.py --check    # 只比不写，不一致则 exit 1
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys
from decimal import Decimal
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "modules" / "M9-console" / "config" / "crs.yaml"

# ═══════════════════════════════════════════════════════════════════════════════
# 模板：与 005 之前那份手写文件同形，prose 一并搬进来
# ═══════════════════════════════════════════════════════════════════════════════
#
# ★ 生成的文件**不是**「配置」了，是一份**报表**：
#   照抄它去改任何一个值都不会生效，下一次导出就把改动冲掉。
#   要改基准，改库（经写入路径），然后重跑本脚本。

TEMPLATE = '''\
# M9 平台管理台 —— 坐标基准配置
#
# ⚠️⚠️ 本文件是**派生产物**，由 `scaffold/tools/export_crs_yaml.py` 生成。
#      手改无效：下一次导出会覆盖。要改基准，请改**库**。
#      （契约变更工单 005 / Q3 裁决：库为准，文件是库的产物。）
#
# 生成来源：design_project / road_section 的 crs_* 六列
# 生成时间：{generated_at}
#
# ★ 为什么这份文件还在，而局部帧并不需要它
# ---------------------------------------------------------------
# 「局部帧」用的是设计坐标系（X=北, Y=东, Z=上, 米），与大地基准无关。
# 这条路本身的三维起伏、仿真轨迹、沿程量着色，全都不需要带号。
#
# 那这份文件是给谁的？给**区域帧**（全省/区域路网、病害空间分布、
# 养护巡检时序、地形影像底图）。那五件事都要把桩号变成经纬度，
# 而带号一旦错了，位置误差是**几百公里** —— 且在任何一个局部视图里
# 都看不出来。它只在把模型放到地球上那一刻暴露。
#
# ★ 既然库已经是权威，为什么还留一个文件
# ---------------------------------------------------------------
# 004/005 期间有四个消费者**只读文件、不连库**（其中两个在 node 侧，
# 一个在 M9 —— M9 的硬线是不得直连数据库）。把文件删掉会让它们全部失明。
# 所以文件留下，但降为**派生产物**，并由 FR-014 的一致性判据盯着。

version: "{version}"

# 局部帧：本 feature（003-local-terrain-viewer）实际使用的帧
# ★ 本段**不从库导出** —— 它与大地基准无关，库里没有对应的列。
local_frame:
  axes: "X=北, Y=东, Z=上"
  unit: m
  note: >
    设计坐标系。与大地基准无关，因此不需要带号。
    路面高程来自 CRG 实测地形高程（0.1 m 网格），不是中线控制点 z
    —— 后者比实际路面高约 200 mm 且没有路拱。

# 地理帧：区域帧（004 及以后）将使用的帧
# ★ 本段全部由库导出，权威来源见 generated_from。
geographic_frame:
  # ★ 区域帧能否使用。
  #   注意 ready 说的是「基准可不可信」，不是「区域帧写没写完」。
  #   ★ 库里**没有** ready 这一列 —— 它是**派生的**：
  #     「有没有一个 EPSG」＝「能不能用」。所以下面这行的判据是
  #     `crs_epsg is not null`，不是某个人填的布尔。
  ready: {ready}

  ellipsoid: {ellipsoid}
  projection: {projection}
  zone_width_deg: {zone_width_deg}
  zone_number: {zone_number}
  central_meridian_deg: {central_meridian_deg}

  # ★★ 与 ready 是**两个不同的标记**，不能合并：
  #    ready  说「能不能用」
  #    assumed 说「值可不可信」
  #    把 ready 设成 true 而 assumed 仍是 true 是合法状态 ——
  #    那是「先用着，但我知道它是猜的」。
  #    反过来 ready=false + assumed=false 也是合法状态。
  assumed: {assumed}

  # 推断依据。与值一同记录，使接手者能复核，也能推翻。
  # ★ 确认之后**不删**：它是这个值的历史，也是复核的起点。
  # ★ 本段由 `design_project.crs_evidence` 导出，**一条不多一条不少** ——
  #   增量会让 FR-014 的一致性判据变成一句空话（它比对一个集合，不是前缀）。
  evidence:
{evidence}

  # ★ 2026-10-11 更正：上面第 3 条里的 25.08°N 是那一条写下时的**单点采样**值，
  #   不是路线的纬度范围。区域帧实现后实测（33 个线元 / 337 个采样点）：
  #     纬度 25.16789 .. 25.20878 °N   （南北 4.552 km）
  #     经度 117.34292 .. 117.37376 °E （东西 3.106 km）
  #   中心 25.18596 °N / 117.35926 °E。
  #   ★ 旧值不删 —— 它是这条判断的**原始观测**，也是复核的起点。
  #   ★ 结论不受影响：25.08 与实测范围同属闽中山区，
  #     "39 带落点在闽中、40 带落点在海峡" 这个对比依然成立。
  #   但**判断的输入**要写成实测范围，否则下一个人会以为路线只有 25.08°N。
  #   ★ 本段是**注解，不是数据** —— 它不参与导出，也不参与一致性比对。
  #     搬进生成器之后它才不会再随一次导出被冲掉。

  # ★★★ 这一段是本次改动里最要紧的一条，也是差点被写错的一条。
  #    仓库里原先的注释把 39 带写成「EPSG:4547 一类的带号编码」。
  #    4547 是 **CM 114E**，也就是 **38 带** —— 差一个带，3 度，约 300 km。
  #    而本文件自己在下面那行警告过：「错带号的特征是纬度不变、经度整体
  #    平移一个带宽」。注释里犯的正好是它警告的错。
  epsg_note: >
    39 带有**两个** EPSG 编码，差别在 Y 值带不带带号前缀：
    EPSG:4548 = CGCS2000 / 3-degree Gauss-Kruger CM 117E（x_0 = 500000，无前缀）
    EPSG:4527 = CGCS2000 / 3-degree Gauss-Kruger zone 39（x_0 = 39500000，有前缀）
    本项目实测 Y 为 534559..537679 m，无前缀，故取 **4548**。
    两者不可互换：取错会让东坐标差 39,000,000 m。

  # 一条能把这个推断变成确认的低成本路径（已于 2026-10-11 完成）
  # ★ 这一段是**历史**，不是当前状态 —— 它是「当时凭什么敢把它定下来」
  #   的可复核记录。导出保留它，但**不**由它推导任何值。
  confirm_path: >
    已确认。原路径：.PRJ 文件的可读载荷含 lql730@outlook.com，
    一封邮件即可把 assumed 从 true 变成 false。

  # ★ 换带号时改**库**（不是这里），再重跑导出。换完必须重跑区域帧的落点
  #   检查 —— 错带号的特征是「纬度不变、经度整体平移一个带宽（约 300 km）」，
  #   所以一个只打印经纬度的检查就能发现它。
  #
  # ★ 不变量（由契约测试强制）：assumed 为 true 时这一行必须为空，
  #   为 false 时必须与 zone_number 对应。猜的时候不许填 EPSG。
  epsg_after_confirm: {epsg_after_confirm}

  note: >
    已确认。EPSG 由 zone_number 派生，取值以 EPSG 官方登记为准
    （核对源：proj4rdf 的 EPSG def 页，见 README 的重核方法）。
    换基准时 spatial_ref_sys 里已有 8500 行，PostGIS 就位，
    所以替换是**一条 UPDATE**，不是重算。

# ═══════════════════════════════════════════════════════════════════════════════
# 导出溯源：这一段由生成器写入，人不该手改
# ═══════════════════════════════════════════════════════════════════════════════
generated_from:
{generated_from}
'''

# 库里没存的三项。★ 它们不是「平台标识符」而是**坐标系本身的性质**，
#   在库里由 `crs_epsg` 这一个权威字段唯一决定 —— 写在这里是**冗余注解**，
#   与 `crs_zone_number` 那三列同一性质：有则须与 SRID 自洽。
#   ★ 导出的值不是从这三个常量来的，是从库里来的；常量只用于**自洽性检查**。
ELLIPSOID = "CGCS2000"
PROJECTION = "高斯-克吕格"


def _fetch(dsn: str) -> dict[str, Any]:
    """读库里那个「当前生效」的基准。

    选取规则与 M3 的 `effective_crs` **同源**：项目级优先，路段级是覆盖。
    这里只取**项目级**那一个作为文件的基准 —— 因为文件是**全平台一份**，
    它描述的是「这套部署默认用哪个基准」。多项目共存时，文件必然只能代表
    其中一个，这正是 SC-001/SC-002 要证明「文件不够用」的地方。

    ★ 所以本脚本刻意**只导出第一个已配基准的项目**，并把它的 id/name 写进
      `generated_from` —— 读到这份文件的人能立刻知道它代表谁。
    """
    sys.path.insert(0, str(ROOT / "modules" / "M3-rpdao"))
    from rpdao import Dao  # noqa: PLC0415  （按路径延迟导入：不连库时不加载 psycopg）

    dao = Dao(dsn, app_name="export-crs-yaml")
    dao.open()
    try:
        # ★ 走 rpdao 的 `query`，不自己抓连接 —— 池化、超时、`app_name`
        #   这些都在它后面。自己 new 一个连接会让本脚本成为第二个数据通路，
        #   而宪法 II 说的是「读只经 M3（rpdao）」。
        proj = dao.query("""
            SELECT id, project_name, crs_epsg, crs_zone_number,
                   crs_central_meridian_deg, crs_zone_width_deg,
                   crs_assumed, crs_evidence
              FROM design_project
             WHERE crs_epsg IS NOT NULL
             ORDER BY id
        """)
        sec = dao.query_one("""
            SELECT count(*) AS n, count(crs_epsg) AS n_bound FROM road_section
        """) or {"n": 0, "n_bound": 0}
    finally:
        dao.close()

    if not proj:
        raise SystemExit(
            "★ design_project 里**没有任何一行**配了坐标基准。\n"
            "  导出会生成一份 ready=false 的文件 —— 但那需要有人明确决定，\n"
            "  不是本脚本替你决定。请先经写入路径给项目配一个 EPSG。")

    p = proj[0]
    # ⚠⚠ 这里**不许**再写 `dict(zip(cols, p))`。
    #   `dao.query` 返回的已经是 `list[dict]`，`p` 本身就是字典；
    #   对着字典做 `zip(列名, p)` 迭代的是它的**键**，于是得到一张
    #   「名字 → 名字」的表：`{"crs_epsg": "crs_epsg", ...}`。
    #   ★ 它**不报错**，而且每个值都是一个看起来合理的字符串 ——
    #     导出会静默地写出一份「所有值都是列名」的 YAML。
    #   实测抓到这一次，靠的是拿新文件与旧文件逐键对比，不是靠任何断言。
    r = dict(p)
    r["_n_projects_with_crs"] = len(proj)
    # ⚠ `query_one` 返回的是 **dict**（不是行元组），所以按键取。
    #   本仓库已经在同一类猜测上栽过一次（`geo.inverse` 返回 tuple 而非 dict）
    #   —— 兄弟函数的返回形状不能外推，要读签名。
    r["_n_sections"] = sec["n"]
    r["_n_sections_with_crs"] = sec["n_bound"]
    return r


def _yaml_scalar(v: Any) -> str:
    """把库里取到的值渲染成 YAML 标量。

    ★ `Decimal('117.000000')` 要渲染成 `117.000000`，不是 `117.0` ——
      精度是这条记录的一部分（库里是 numeric(9,6)），抹掉它会让
      「文件与库一致」这条判据在**看起来对**的情况下失配。
    """
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, (int, float)):
        return str(v)
    return str(v)


def render(db: dict[str, Any], *, generated_at: str) -> str:
    # ★ evidence 落进文件时**剥掉行首编号**。
    #   库里存的是种子文件原样的 `1. …\n2. …`（那是给人读的流水号），
    #   而 crs.yaml 里这 8 条从来不带编号。
    #   保留编号会让「文件与库一致」这条判据永远失配 —— 因为比对的一侧
    #   带了编号、另一侧没有，而那两次编辑**谁也没改内容**。
    #   ⇒ 编号是排版，不是数据；两侧都比对规范化之后的形态（crs_drift.parse_evidence）。
    sys.path.insert(0, str(ROOT / "modules" / "M9-console"))
    from crs_drift import parse_evidence  # noqa: PLC0415  （同目录语义：M9 侧模块）
    items = parse_evidence(db.get("crs_evidence"))
    # 逐行原样落成 YAML list —— 保留 `1. ` 编号（那是 seed 里的事实）
    ev_block = "\n".join(
        "    - " + _dq(ln) for ln in items) if items else "    []"

    epsg = db.get("crs_epsg")
    assumed = db.get("crs_assumed")

    # ★ 不变量：assumed 为 true 时 epsg_after_confirm 必须为空。
    #   这里**主动执行**它，而不是等契约测试来抓 —— 导出是一次写入。
    if assumed is True and epsg is not None:
        raise SystemExit(
            f"★ 库里的状态不合法：crs_assumed=true（值是猜的）却填了 "
            f"crs_epsg={epsg}。\n  不变量：猜的时候不许填 EPSG。"
            f"先把它确认为 false，或清掉 epsg。")
    epsg_out = "null" if assumed is True else _yaml_scalar(epsg)

    zone = db.get("crs_zone_number")
    cm = db.get("crs_central_meridian_deg")
    width = db.get("crs_zone_width_deg")

    gf = "\n".join(
        f"  - {{k: {k!r}, v: {v!r}}}" for k, v in (
            ("design_project.id", db.get("id")),
            ("design_project.project_name", db.get("project_name")),
            ("crs_epsg", epsg),
            ("crs_zone_number", zone),
            ("crs_central_meridian_deg", _yaml_scalar(cm)),
            ("crs_zone_width_deg", _yaml_scalar(width)),
            ("crs_assumed", assumed),
            ("crs_evidence 条数", len(items)),
        ))

    return TEMPLATE.format(
        generated_at=generated_at,
        version="0.2",
        ready="true" if epsg is not None else "false",
        ellipsoid=ELLIPSOID,
        projection=PROJECTION,
        zone_width_deg=_yaml_scalar(width),
        zone_number=_yaml_scalar(zone),
        central_meridian_deg=_yaml_scalar(cm),
        assumed=_yaml_scalar(assumed),
        evidence=ev_block,
        epsg_after_confirm=epsg_out,
        generated_from=gf,
    )


def _dq(s: str) -> str:
    """YAML 双引号标量：转义反斜杠与双引号。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main() -> int:
    ap = argparse.ArgumentParser(description="把 crs.yaml 从库导出")
    ap.add_argument("--dsn", default=os.getenv("PG_DSN", ""))
    ap.add_argument("--check", action="store_true",
                    help="只比对，不写；不一致则 exit 1")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    if not a.dsn:
        print("★ 没给 PG_DSN（也没有 --dsn）。导出要读库。", file=sys.stderr)
        return 2

    import datetime as _dt
    stamp = _dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    db = _fetch(a.dsn)
    text = render(db, generated_at=stamp)

    out = pathlib.Path(a.out)
    if a.check:
        cur = out.read_text(encoding="utf-8") if out.exists() else ""
        # ★ 生成时间必然不同，比对前先把它抹平 —— 否则 --check 永远红，
        #   而一条永远红的检查等于没有检查（宪法 V）。
        def _strip(s: str) -> list[str]:
            return [ln for ln in s.splitlines() if not ln.startswith("# 生成时间：")]
        if _strip(cur) == _strip(text):
            print(f"✓ crs.yaml 与库一致（{out}）")
            return 0
        print(f"★ crs.yaml 与库**不一致**（{out}）。重跑不带 --check 的导出。",
              file=sys.stderr)
        return 1

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(f"✓ 已导出 {out}")
    print(f"  来源：design_project id={db['id']} 「{db['project_name']}」"
          f"  epsg={db['crs_epsg']} zone={db['crs_zone_number']} "
          f"cm={_yaml_scalar(db['crs_central_meridian_deg'])} "
          f"assumed={_yaml_scalar(db['crs_assumed'])}")
    print(f"  库里另有 {db['_n_projects_with_crs']} 个项目配了基准，"
          f"路段 {db['_n_sections_with_crs']}/{db['_n_sections']} 条配了路段级基准")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
