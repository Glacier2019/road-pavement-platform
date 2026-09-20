#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从真实 DDL v0.2 解析生成 ER 图，并标注「纬地设计文件 → 表」的数据来源。

与 gen_er.py 的区别：
  · gen_er.py 是**设计文档版**（12 域 35 表，含 QU 语义域等尚未落地的表）
  · 本脚本是**实测版**：表/字段/外键全部从 scaffold/sql/10_ddl_v0.5.sql 解析，
    域分组从契约③ scaffold/modules/M3-rpdao/rpdao/catalog.py 读取 —— 与代码同源，不会漂移。

产出（output/）：
  · ER-全景-<N>表.svg / .png          全部物理表 + 全部外键（表数动态计算，别写死）
  · ER-纬地导入.svg / .png            GE 域聚焦视图：纬地数据能填什么、缺哪张表

用法：python3 docpipe/scripts/gen_er_weidi.py
"""
from __future__ import annotations

import re
import pathlib
import subprocess
import sys

ROOT = pathlib.Path("/data/cy/shujuku")
# ⚠ 原写 v0.3 —— 那个路径现在是个**空目录**（误操作留下的），脚本直接
#   IsADirectoryError 跑不起来。真源已到 v0.5。
DDL = ROOT / "scaffold" / "sql" / "10_ddl_v0.5.sql"
#: 当前 DDL 版本标签（只用于图上的文字与下面的自洽断言）。
DDL_VERSION = "v0.5"
#: 期望表数。v0.3=42 → v0.4=53 → v0.5=55。
EXPECTED_TABLES = 55
CATALOG = ROOT / "scaffold" / "modules" / "M3-rpdao" / "rpdao" / "catalog.py"
OUT = ROOT / "output"

# ────────────────────────────────────────────────────────────
# 1. 解析 DDL
# ────────────────────────────────────────────────────────────
CONSTRAINT_KW = ("primary key", "unique", "foreign key", "constraint", "check")


def parse_ddl(text: str) -> dict[str, dict]:
    """→ {表名: {comment, cols: [(名, 类型, flags)], fks: [(列, 引用表, 引用列)]}}

    注意两处坑（都是本项目 DDL 的真实写法，正则图省事会漏）：
      1) 分区表以 `) PARTITION BY RANGE (...);` 收尾，不是 `);`
         —— 用非贪婪正则会把后一张表整个吞掉（wim_axle_detail 就这么丢过）。
         故改为「按 CREATE TABLE 切块 → 遇到顶格 `)` 收尾」。
      2) wim_axle_detail 用**表级复合外键**
         `FOREIGN KEY (record_id, pass_time) REFERENCES wim_axle_record(id, pass_time)`
         —— 只认行内 `列 ... REFERENCES` 的写法会漏掉它。
    """
    tables: dict[str, dict] = {}

    for chunk in text.split("CREATE TABLE IF NOT EXISTS ")[1:]:
        name = re.match(r"(\w+)", chunk).group(1)
        body_lines: list[str] = []
        for ln in chunk.splitlines()[1:]:      # 跳过"表名 ("行
            if ln.startswith(")"):             # 顶格右括号 = 表体结束
                break
            body_lines.append(ln)

        cols: list[tuple[str, str, list[str]]] = []
        fks: list[tuple[str, str, str]] = []
        pk_cols: list[str] = []

        for raw in body_lines:
            line = raw.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            low = line.lower()

            # 表级主键
            if low.startswith("primary key"):
                inner = re.search(r"\((.*?)\)", line)
                if inner:
                    pk_cols += [c.strip() for c in inner.group(1).split(",")]
                continue

            # 表级复合外键
            if low.startswith("foreign key"):
                m = re.match(
                    r"FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+(\w+)\s*\(([^)]*)\)",
                    line, re.I,
                )
                if m:
                    rtbl = m.group(2)
                    rcols = [c.strip() for c in m.group(3).split(",")]
                    for c in [x.strip() for x in m.group(1).split(",")]:
                        fks.append((c, rtbl, rcols[0] if len(rcols) == 1 else ",".join(rcols)))
                continue

            if low.startswith(("unique", "check", "constraint")):
                continue

            # 列定义
            fm = re.match(r"(\w+)\s+([a-zA-Z]+(?:\([^)]*\))?(?:\[\])?)", line)
            if not fm:
                continue
            cname, ctype = fm.group(1), fm.group(2)
            flags: list[str] = []
            if "PRIMARY KEY" in line.upper():
                flags.append("pk")
                pk_cols.append(cname)
            if "NOT NULL" in line.upper():
                flags.append("nn")
            # ★ 必须带 re.I：DDL 里列内 FK 写的是**小写** `references`
            #   （如 `section_id bigint not null references road_section(id)`），
            #   而表级 FK 写的是大写 `FOREIGN KEY ... REFERENCES`（上面那条带了 re.I）。
            #   原正则只认大写 → 实测 52 条 FK 里**漏掉 4 条列内的**，
            #   于是 earthwork_section / roadbed_design_point 等明明有外键的表
            #   在图上成了孤儿节点。
            ref = re.search(r"REFERENCES\s+(\w+)\s*\(\s*(\w+)\s*\)", line, re.I)
            if ref:
                flags.append("fk")
                fks.append((cname, ref.group(1), ref.group(2)))
            cols.append((cname, ctype, flags))

        pks = set(pk_cols)
        cols = [
            (n, t, sorted(set(f + (["pk"] if n in pks else []))))
            for n, t, f in cols
        ]
        cm = re.search(rf"COMMENT ON TABLE\s+{name}\s+IS\s+'([^']*)'", text)
        tables[name] = {
            "comment": (cm.group(1) if cm else ""),
            "cols": cols,
            "fks": fks,
        }

    return tables


# ────────────────────────────────────────────────────────────
# 2. 从契约③ catalog.py 读域分组（与代码同源）
# ────────────────────────────────────────────────────────────
DOMAIN_LABEL = {
    "GE": "GE 道路几何",
    "SU": "SU 路面表面",
    "RE": "RE 结构响应",
    "LO": "LO 交通荷载",
    "WE": "WE 环境气象",
    "TE": "TE 试验检测",
    "DE": "DE 决策输出",
    "XX": "跨域支撑（字典·治理·闭环）",
}

# 域配色：中性底 + 每域一色（与 gen_arch_figures.py 同一套 stance）
DOMAIN_STYLE = {
    "GE": ("#065f46", "#d1fae5"),
    "SU": ("#7c2d12", "#ffedd5"),
    "RE": ("#831843", "#fce7f3"),
    "LO": ("#1e3a8a", "#dbeafe"),
    "WE": ("#134e4a", "#ccfbf1"),
    "TE": ("#4c1d95", "#ede9fe"),
    "DE": ("#78350f", "#fef3c7"),
    "XX": ("#334155", "#e2e8f0"),
}


def _strip_comments(text: str) -> str:
    """剥掉行内 ``#`` 注释后再做正则解析。

    ★ 为什么必须剥：本函数曾在 v0.3 静默失效——catalog 的注释里写了
      ``纬地(HintCAD)``，而表清单用 ``[^)]*`` 捕获，**ASCII 右括号**让捕获
      在第 4 张表处提前收尾，GE 域 14 张表只解析出 4 张。
      注释是给人看的，不该参与结构解析。
    """
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def load_domains() -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """→ ({域: [表]}, {域: [逻辑视图待建说明]})

    不用跨块的 `.*?`（re.S 下会一路吞到下一个域），而是定位每个 Domain( 块后
    取**第一个括号组**=物理表元组。表名不含括号，故 `[^)]*` 安全。
    """
    s = CATALOG.read_text(encoding="utf-8")
    dom_block = s[s.find("DOMAINS"): s.find("CROSS_TABLES")]
    domains: dict[str, list[str]] = {}
    logical: dict[str, list[str]] = {}

    for code in ("GE", "SU", "RE", "LO", "WE", "TE", "DE"):
        i = dom_block.find(f'"{code}": Domain(')
        if i < 0:
            domains[code] = []
            logical[code] = []
            continue
        # ★ 段边界取到**下一个域块**为止，不写死长度：
        #   GE 域 v0.3 从 4 表扩到 14 表，写死窗口会把它拦腰截断。
        nxt = min([j for j in (dom_block.find(f'"{c}": Domain(', i + 1)
                               for c in ("GE", "SU", "RE", "LO", "WE", "TE", "DE"))
                   if j > i] or [len(dom_block)])
        seg = _strip_comments(dom_block[i:nxt])
        tm = re.search(r"Domain\(\s*\"\w+\",\s*\"[^\"]*\",\s*\(([^)]*)\)", seg)
        domains[code] = re.findall(r'"(\w+)"', tm.group(1)) if tm else []
        # 逻辑有而物理未建：取物理表元组之后的**下一个**括号组
        # （不能写成 `\),\s*\(`：元组间夹着注释与逗号，会一路滑到下一个域）
        rest = seg[tm.end():] if tm else ""
        lm = re.search(r"\((.*?)\)", rest, re.S)
        logical[code] = re.findall(r'"([^"]+)"', lm.group(1)) if lm else []

    cm = re.search(r"CROSS_TABLES[^=]*=\s*\((.*?)\)\n", s, re.S)
    cross = re.findall(r'"(\w+)"', cm.group(1)) if cm else []
    # 只保留真实物理表名（排除注释里出现的文字）
    domains["XX"] = [c for c in cross if re.fullmatch(r"\w+", c)]
    logical["XX"] = []
    return domains, logical


# ────────────────────────────────────────────────────────────
# 3. 纬地数据来源标注（本次分析的核心结论）
# ────────────────────────────────────────────────────────────
# 值 = (来源文件列表, 现状说明)；现状：now=现有表可装 / new=需新建 / gap=平台不适用
WEIDI_SOURCE: dict[str, tuple[list[str], str]] = {
    "road_line": ([".PRJ 总项目"], "now"),
    "road_section": ([".PRJ 项目分段"], "now"),
    "structure_layer": ([".BDM 标准断面", ".HDMSJ 断面设计"], "now"),
    "monitor_cross_section": ([".STA 桩号序列"], "now"),   # v0.3 已在册（原误标 new）
    "geometry_point": ([".JD 交点", ".pm 平面线形", ".STA 桩号", ".ZDM 纵断面", ".SUP 超高", ".WID 路幅宽度"], "now"),  # v0.3 批次一已落地
}

# 纬地有、但平台 DDL 完全没有对应表的数据（设计期成果，属另一个库）
WEIDI_ORPHAN = [
    ("横断面地面线", ".HDM / .HDMSJ", "逐桩地面线测点（平距/高差）"),
    ("土方与调配", ".tf / .tsf", "填挖方量、土石方调配"),
    ("结构物", ".dq / .hda / .cys", "挡墙、涵洞"),
    ("三维数模", ".DTM / .gtm", "地形三角网"),
    ("路基中间数据", ".lj", "逐桩路基设计高程/宽度"),
]


# ────────────────────────────────────────────────────────────
# 4. 生成 Graphviz DOT
# ────────────────────────────────────────────────────────────
def esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def node_html(tname: str, meta: dict, compact: bool = False) -> str:
    """表节点：表名 + 字段列表（PK/FK/待建 标记）"""
    src, kind = WEIDI_SOURCE.get(tname, ([], ""))
    title = tname
    if kind == "new":
        title += "  ★待建"
    elif src:
        title += "  ◀纬地"

    rows = []
    cols = meta["cols"] if not compact else [
        c for c in meta["cols"] if "pk" in c[2] or "fk" in c[2]
    ][:8]
    for cname, ctype, flags in cols:
        mark = ""
        if "pk" in flags:
            mark = "🔑"
        elif "fk" in flags:
            mark = "🔗"
        short_type = re.sub(r"\(.*\)", "", ctype)[:9]
        cnt = cname if len(cname) <= 22 else cname[:21] + "…"
        rows.append(
            f'<TR><TD ALIGN="LEFT">{mark}{esc(cnt)}</TD>'
            f'<TD ALIGN="RIGHT"><FONT COLOR="#94a3b8">{short_type}</FONT></TD></TR>'
        )

    body = "".join(rows) if rows else '<TR><TD>（无字段）</TD></TR>'
    style = 'STYLE="dashed"' if kind == "new" else 'STYLE="solid"'
    port = ""
    if kind == "new":
        port = '<TR><TD ALIGN="LEFT"><FONT COLOR="#b45309">需新建此表</FONT></TD></TR>'
    elif src:
        port = (
            '<TR><TD ALIGN="LEFT"><FONT COLOR="#0f766e">源：'
            + esc("／".join(s.split()[0] for s in src))
            + "</FONT></TD></TR>"
        )

    return (
        f'<TR><TD PORT="{tname}" BGCOLOR="#0f172a">'
        f'<FONT COLOR="white"><B>{esc(title)}</B></FONT></TD></TR>'
        f"{body}{port}"
    )


def planned_node_html(name: str, note: str) -> str:
    """待建表（逻辑有、物理未建）：虚线节点，标出缺口与原因"""
    src, kind = WEIDI_SOURCE.get(name, ([], ""))
    rows = (
        f'<TR><TD ALIGN="LEFT"><FONT COLOR="#b45309">★ 物理未建（{esc(note[:34])}）</FONT></TD></TR>'
    )
    if src:
        rows += (
            '<TR><TD ALIGN="LEFT"><FONT COLOR="#0f766e">源：'
            + esc("／".join(s.split()[0] for s in src))
            + "</FONT></TD></TR>"
        )
    return (
        f'<TR><TD BGCOLOR="#7c2d12"><FONT COLOR="white"><B>{esc(name)}  ★待建</B></FONT></TD></TR>'
        + rows
    )


def build_dot(tables: dict[str, dict], domains: dict[str, list[str]],
              logical: dict[str, list[str]] | None = None,
              focus: str | None = None) -> str:
    logical = logical or {}
    lines = [
        "digraph ER {",
        '  graph [rankdir=LR, splines=ortho, nodesep=0.35, ranksep=1.1,',
        '         bgcolor="white", fontname="Noto Sans CJK SC",',
        # ★ 表数/外键数**动态计算** —— 写死数字正是「图件与真源各自漂移」的根源
        f'         label=<<B>2025Y095 路面性能数据平台 · 物理 ER'
        f'（DDL {DDL_VERSION}，{len(tables)} 表 {sum(len(m["fks"]) for m in tables.values())} 外键）</B>'
        f'<BR/><FONT POINT-SIZE="9">表/字段/外键均从 scaffold/sql/10_ddl_{DDL_VERSION}.sql 解析 ｜ '
        '域分组取自契约③ catalog.py ｜ ◀纬地=可由此设计文件填充　★待建=DDL 尚无此表</FONT>>,',
        "         labelloc=t, fontsize=13];",
        '  node [shape=plaintext, fontname="Noto Sans CJK SC", fontsize=9];',
        '  edge [color="#2563eb", arrowsize=0.7, fontname="Noto Sans CJK SC", fontsize=7];',
    ]

    # 域子图
    for code, tabs in domains.items():
        if not tabs and not logical.get(code):
            continue
        if focus and code not in focus:
            continue
        dark, light = DOMAIN_STYLE[code]
        lines.append(f'  subgraph cluster_{code} {{')
        lines.append(f'    label=<<B>{esc(DOMAIN_LABEL[code])}</B>>;')
        lines.append(f'    style="rounded,filled"; fillcolor="{light}"; color="{dark}";')
        lines.append(f'    fontcolor="{dark}"; fontsize=11; penwidth=1.6;')
        for t in tabs:
            if t not in tables:
                continue
            meta = tables[t]
            lines.append(
                f'    "{t}" [label=<<TABLE BORDER="1" CELLBORDER="0" CELLSPACING="0" '
                f'CELLPADDING="3" BGCOLOR="white">{node_html(t, meta)}</TABLE>>];'
            )
        # 待建表（虚线）
        for item in logical.get(code, []):
            pname = item.split("（")[0].split("(")[0].strip()
            note = item[len(pname):].strip("（）() ") or "待补"
            if not re.fullmatch(r"\w+", pname):
                continue
            lines.append(
                f'    "PLAN_{pname}" [label=<<TABLE BORDER="1" STYLE="dashed" CELLBORDER="0" '
                f'CELLSPACING="0" CELLPADDING="3" BGCOLOR="#fffbeb">'
                f'{planned_node_html(pname, note)}</TABLE>>];'
            )
        lines.append("  }")

    # 外键边
    shown = {t for tabs in domains.values() for t in tabs}
    for t, meta in tables.items():
        if t not in shown:
            continue
        for cname, rtbl, rcol in meta["fks"]:
            if rtbl not in shown:
                continue
            lines.append(f'  "{t}":{t} -> "{rtbl}":{rtbl} [label="{esc(cname)}"];')

    lines.append("}")
    return "\n".join(lines)


def render(dot_text: str, stem: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dotf = OUT / f"{stem}.dot"
    dotf.write_text(dot_text, encoding="utf-8")
    for fmt in ("svg", "png"):
        cmd = ["dot", f"-T{fmt}", str(dotf), "-o", str(OUT / f"{stem}.{fmt}")]
        if fmt == "png":
            cmd = ["dot", "-Tpng", "-Gdpi=150", str(dotf), "-o", str(OUT / f"{stem}.png")]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            print(f"  ✗ {stem}.{fmt}: {r.stderr[:300]}")
            sys.exit(1)
    size = (OUT / f"{stem}.png").stat().st_size
    print(f"  ✓ {stem}.svg / .png  ({size//1024} KB)")


def main() -> None:
    text = DDL.read_text(encoding="utf-8")
    tables = parse_ddl(text)
    domains, logical = load_domains()

    total_tabs = sum(len(v) for v in domains.values())
    total_fks = sum(len(m["fks"]) for m in tables.values())
    print(f"  解析：{len(tables)} 张表，{total_fks} 条外键")
    print(f"  域分组：{total_tabs} 张（域内 {total_tabs - len(domains['XX'])} + 跨域 {len(domains['XX'])}）")

    # 表数一致性自检（与契约测试同口径）
    # ⚠ 这两个数是**手抄**的，会漂 —— 真正的钉子不在这里，而在
    #   scaffold/tests/contract/test_ddl_dict_catalog.py（它按实库口径数表）。
    #   这里保留断言只为"脚本自身前后一致"（解析出的表数 vs 域分组表数）。
    assert len(tables) == EXPECTED_TABLES, \
        f"表数应为 {EXPECTED_TABLES}（{DDL_VERSION}），实为 {len(tables)}"
    assert total_tabs == EXPECTED_TABLES, \
        f"域分组合计应为 {EXPECTED_TABLES}，实为 {total_tabs}"

    # 图1：全景
    # 文件名带上实际表数 —— 原先写死 "42表"，DDL 到 v0.5（55 表）后
    # 图里的数字与文件名就对不上了（图上写 55、文件名写 42）。
    render(build_dot(tables, domains, logical), f"ER-全景-{len(tables)}表")

    # 图2：纬地导入聚焦（GE 域 + RE 域 + 待建表标注）
    render(build_dot(tables, domains, logical, focus={"GE", "RE"}), "ER-纬地导入-GE聚焦")

    # 未落地逻辑视图提示
    for code, logs in logical.items():
        if logs:
            print(f"  · {code} 逻辑有而物理未建：{'；'.join(l.split('（')[0] for l in logs)}")


if __name__ == "__main__":
    main()
