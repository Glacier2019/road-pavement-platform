"""纬地 HintCAD `.tf` 土方数据文件解析器（契约⑤ 第 9 段）。

文件格式（实测自 052201341 毕设工程，334 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD6.00_TF_SHUJU           ← 魔数，含厂商版本号
    第 2 行   //[ 桩号 ][挖方面积][填方面积]… ← **文件自带的列名注释**（GBK）
    第 3 行起 74 个 TAB 分隔的数值           ← 每行一个桩号断面
    行尾 CRLF；第 75 列恒为空串 `""`（程序产物，不取）

★★ 这个文件是**唯一自带完整列名**的纬地文件（74 个），所以：
  ① 解析时**从文件里读表头**并与期望的 74 个名字逐一对齐 ——
     供应商改了列序或加删列，这里**当场拒绝**，而不是按位置静默错位；
  ② 落库的列名由这张表头逐条译出（见 `design_import` 与 DDL 的 J1），
     **没有一个列名是凭记忆写的**。

⚠⚠ 说明书正文把第 2/3 列写反了
-------------------------------------------------------------------------------
教程 §13.9 正文写「`[桩号] [填方面积] [挖方面积]`」，
但**文件自带表头**写「`[桩号] [挖方面积] [填方面积]`」。

用 `.lj` 的设计标高 vs 地面标高独立判填挖，再比全部 332 行谁大：
    按文件表头 → 326/332 吻合；按说明书 → 6/332。
（6 处不符全是「设计标高 = 地面标高」的零填零挖边界，那时谁大谁小本就没有意义。）
**以文件表头为准。**

用途：土石方计算与调配、公路用地图、路线总体图生成的基础数据（教程 §13.9 / §7.1.4）。
"""
from __future__ import annotations

import re
from typing import Any

from ..errors import SourceInvalid

MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_TF_SHUJU$")

SEGMENT = "earthwork_section"
FILE_KIND = "土方数据文件"
PAYLOAD_KEY = "points"

#: 文件自带的 74 个列名（第 2 行 `//[..][..]`）。**顺序即列序**。
#: 这是「供应商改了列序就报错」的判据，故写死在这里、不随文件变。
EXPECTED_HEADER: tuple[str, ...] = (
    "桩     号", "挖方面积", "填方面积", "中桩填挖", "路基左宽", "路基右宽",
    "基缘左高", "基缘右高", "左坡脚距", "右坡脚距", "左坡脚高", "右坡脚高",
    "左沟缘距", "右沟缘距", "左护坡道宽", "右护坡道宽", "左沟底高", "右沟底高",
    "左沟心距", "右沟心距", "左沟深度", "右沟深度", "左用地宽", "右用地宽",
    "清表面积", "顶超面积", "左超面积", "右超面积", "计排水沟",
    "左沟面积填", "左沟面积挖", "右沟面积填", "右沟面积挖",
    "路槽面积填", "路槽面积挖", "清表宽度", "清表厚度",
    "挖1类面积", "挖2类面积", "挖3类面积", "挖4类面积", "挖5类面积", "挖6类面积",
    "左路槽B", "右路槽B", "左路槽C", "右路槽C", "左垫层", "右垫层",
    "左路床", "右路床", "左土肩培土", "右土肩培土", "左包边土", "右包边土",
    "左边沟回填", "右边沟回填", "左截沟填", "左截沟挖", "右截沟填", "右截沟挖",
    "挖台阶面积",
    "填1类面积", "填2类面积", "填3类面积", "填4类面积", "填5类面积", "填6类面积",
    "弃1类面积", "弃2类面积", "弃3类面积", "弃4类面积", "弃5类面积", "弃6类面积",
)

#: 中文列名 → 落库用的英文列名。顺序与 `EXPECTED_HEADER` 一致。
#: ★ 与 DDL 的 J1 表列名**必须一致** —— 契约测试会逐条对账（改了这里不改 DDL 会红）。
COLUMNS: tuple[tuple[str, str], ...] = (
    ("桩     号", "station_m"), ("挖方面积", "cut_area_m2"), ("填方面积", "fill_area_m2"),
    ("中桩填挖", "center_fill_cut_m"),
    ("路基左宽", "subgrade_left_width_m"), ("路基右宽", "subgrade_right_width_m"),
    ("基缘左高", "subgrade_edge_left_elev_m"), ("基缘右高", "subgrade_edge_right_elev_m"),
    ("左坡脚距", "left_slope_toe_offset_m"), ("右坡脚距", "right_slope_toe_offset_m"),
    ("左坡脚高", "left_slope_toe_elev_m"), ("右坡脚高", "right_slope_toe_elev_m"),
    ("左沟缘距", "left_ditch_edge_offset_m"), ("右沟缘距", "right_ditch_edge_offset_m"),
    ("左护坡道宽", "left_berm_width_m"), ("右护坡道宽", "right_berm_width_m"),
    ("左沟底高", "left_ditch_bottom_elev_m"), ("右沟底高", "right_ditch_bottom_elev_m"),
    ("左沟心距", "left_ditch_center_offset_m"), ("右沟心距", "right_ditch_center_offset_m"),
    ("左沟深度", "left_ditch_depth_m"), ("右沟深度", "right_ditch_depth_m"),
    ("左用地宽", "left_land_width_m"), ("右用地宽", "right_land_width_m"),
    ("清表面积", "topsoil_clear_area_m2"), ("顶超面积", "top_overfill_area_m2"),
    ("左超面积", "left_overfill_area_m2"), ("右超面积", "right_overfill_area_m2"),
    ("计排水沟", "drainage_ditch_flag"),
    ("左沟面积填", "left_ditch_fill_area_m2"), ("左沟面积挖", "left_ditch_cut_area_m2"),
    ("右沟面积填", "right_ditch_fill_area_m2"), ("右沟面积挖", "right_ditch_cut_area_m2"),
    ("路槽面积填", "trench_fill_area_m2"), ("路槽面积挖", "trench_cut_area_m2"),
    ("清表宽度", "topsoil_clear_width_m"), ("清表厚度", "topsoil_clear_thickness_m"),
    ("挖1类面积", "cut_class_1_area_m2"), ("挖2类面积", "cut_class_2_area_m2"),
    ("挖3类面积", "cut_class_3_area_m2"), ("挖4类面积", "cut_class_4_area_m2"),
    ("挖5类面积", "cut_class_5_area_m2"), ("挖6类面积", "cut_class_6_area_m2"),
    ("左路槽B", "left_trench_b_area_m2"), ("右路槽B", "right_trench_b_area_m2"),
    ("左路槽C", "left_trench_c_area_m2"), ("右路槽C", "right_trench_c_area_m2"),
    ("左垫层", "left_bedding_area_m2"), ("右垫层", "right_bedding_area_m2"),
    ("左路床", "left_subgrade_bed_area_m2"), ("右路床", "right_subgrade_bed_area_m2"),
    ("左土肩培土", "left_earth_shoulder_fill_area_m2"),
    ("右土肩培土", "right_earth_shoulder_fill_area_m2"),
    ("左包边土", "left_edge_wrap_fill_area_m2"), ("右包边土", "right_edge_wrap_fill_area_m2"),
    ("左边沟回填", "left_side_ditch_backfill_area_m2"),
    ("右边沟回填", "right_side_ditch_backfill_area_m2"),
    ("左截沟填", "left_intercept_ditch_fill_area_m2"),
    ("左截沟挖", "left_intercept_ditch_cut_area_m2"),
    ("右截沟填", "right_intercept_ditch_fill_area_m2"),
    ("右截沟挖", "right_intercept_ditch_cut_area_m2"),
    ("挖台阶面积", "bench_cut_area_m2"),
    ("填1类面积", "fill_class_1_area_m2"), ("填2类面积", "fill_class_2_area_m2"),
    ("填3类面积", "fill_class_3_area_m2"), ("填4类面积", "fill_class_4_area_m2"),
    ("填5类面积", "fill_class_5_area_m2"), ("填6类面积", "fill_class_6_area_m2"),
    ("弃1类面积", "waste_class_1_area_m2"), ("弃2类面积", "waste_class_2_area_m2"),
    ("弃3类面积", "waste_class_3_area_m2"), ("弃4类面积", "waste_class_4_area_m2"),
    ("弃5类面积", "waste_class_5_area_m2"), ("弃6类面积", "waste_class_6_area_m2"),
)

N_COLUMNS = 74
#: 桩号列在英文列名里的名字。IR 统一用**米**（源文件原生单位），落库时才换算 km。
STATION_KEY = "station_m"


def detect(text: str) -> bool:
    """格式探测：只认魔数，不靠扩展名。"""
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def _header_names(line: str) -> list[str]:
    """从 `//[..][..]` 注释行抠出列名。"""
    return [x.strip() for x in re.findall(r"\[([^\]]*)\]", line)]


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.tf` → ``{"vendor_version", "points": [ {…74 列…}, … ], "notes": []}``

    每个点的键是**英文列名**（`COLUMNS`），另加 `station_m`（米，IR 统一单位）。

    校验策略：**宁可拒绝，不要猜。**
    这个文件是土石方计算与用地图的基础数据，读错一列不会报错，
    只会让整条路的土方量、用地宽度、坡脚位置全错。
    """
    if not text.strip():
        raise SourceInvalid("空文件", file=file)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_TF_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1)
    version = m.group(1)

    # ── 第 2 行必须是自带的列名注释，且与期望的 74 个**逐一对齐** ──────────
    if len(lines) < 2 or not lines[1].lstrip().startswith("//"):
        raise SourceInvalid(
            "第 2 行应为文件自带的列名注释（以 `//` 开头，形如 `//[ 桩号 ][挖方面积]…`）。"
            "本解析器靠它对齐列序 —— 没有它就只能按位置猜，而猜错不会报错。",
            file=file, line_no=2)
    got = _header_names(lines[1])
    if got != list(EXPECTED_HEADER):
        if len(got) != N_COLUMNS:
            raise SourceInvalid(
                f"列名个数应为 {N_COLUMNS}，实为 {len(got)} —— 供应商改了列数，"
                f"落库映射（DDL 的 J1）需要同步。", file=file, line_no=2)
        diff = [(i + 1, a, b) for i, (a, b) in enumerate(zip(got, EXPECTED_HEADER)) if a != b]
        raise SourceInvalid(
            f"列名与期望不符（首个不同在第 {diff[0][0]} 列：文件 {diff[0][1]!r} ／ "
            f"期望 {diff[0][2]!r}，共 {len(diff)} 处）—— 列序变了，"
            f"按位置解析会**静默错位**，故直接拒绝。", file=file, line_no=2)

    en_names = [en for _, en in COLUMNS]
    assert len(en_names) == N_COLUMNS, "COLUMNS 与 EXPECTED_HEADER 长度不一致（内部错误）"

    points: list[dict[str, Any]] = []
    for i, raw in enumerate(lines[2:], start=3):
        s = raw.strip()
        if not s:
            continue
        parts = raw.split("\t")
        # ⚠ 第 75 列不是**空字段**，是**字面量 `""`**（两个引号字符，程序产物）。
        #   第一版按"过滤空串"写，于是 75 列被当成"多了一列"直接拒收 ——
        #   实测报 `字段数应为 74，实为 75`。故这里显式认这一列：
        #     74 列            → 正常
        #     75 列且末列 `""`  → 正常（把那一列丢掉）
        #     其它             → 拒绝
        if len(parts) == N_COLUMNS + 1 and parts[-1].strip() == '""':
            parts = parts[:-1]
        if len(parts) != N_COLUMNS:
            raise SourceInvalid(
                f"字段数应为 {N_COLUMNS}（文件自带的 74 列；可多一个恒为 `\"\"` 的尾列），"
                f"实为 {len(parts)}：{s[:60]!r}", file=file, line_no=i)
        row: dict[str, Any] = {}
        for en, txt in zip(en_names, parts):
            row[en] = _num(txt, en, file=file, line_no=i)
        st = row["station_m"]
        if st is None:
            raise SourceInvalid("桩号为空", file=file, line_no=i)
        if st < 0:
            raise SourceInvalid(f"桩号为负：{st}", file=file, line_no=i)
        points.append(row)

    notes: list[str] = []
    if not points:
        notes.append("没有任何数据行 —— 文件可能被截断")
    return {"vendor_version": version, PAYLOAD_KEY: points, "notes": notes}


def _num(txt: str, what: str, *, file: str | None, line_no: int) -> float | None:
    t = txt.strip()
    if t == "":
        return None
    try:
        return float(t)
    except ValueError:
        raise SourceInvalid(f"{what}不是合法数字：{txt!r}", file=file, line_no=line_no) from None


def check_against_stations(points: list[dict[str, Any]],
                           stations: list[dict[str, Any]]) -> list[str]:
    """与 `.STA` 桩号序列对账。

    `.tf` 与 `.lj` 都是**逐桩**文件（每行一个桩号断面），且实测行数与 `.STA` 完全相同。
    故这里比**集合相等**（不是像 `.CTR` 那样只比范围）：
    少一个桩号 = 那个断面的土方数据丢了；多一个 = 挂到了不存在的桩号上。
    """
    have = {round(p["station_m"], 3) for p in points}
    want = {round(s["station_m"], 3) for s in stations}
    out: list[str] = []
    miss, extra = sorted(want - have), sorted(have - want)
    if miss:
        out.append(f"桩号序列里有、.tf 里没有的 {len(miss)} 个（前 3：{miss[:3]}）")
    if extra:
        out.append(f".tf 里有、桩号序列里没有的 {len(extra)} 个（前 3：{extra[:3]}）")
    return out


def check_earthwork(points: list[dict[str, Any]]) -> list[str]:
    """物理合理性。**正例必须零告警** —— 否则告警就是噪声，人会学会无视它。

    ★ 这里删过一条规则，值得记下来：
      第一版写了「填挖面积不可能同时显著为正 —— 一个断面要么填要么挖」，
      实测**在 332 行里报了 16 条**（5%）。那不是异常，是**半填半挖**断面 ——
      山区公路极常见的一种断面。**规则的前提本身就是错的**，
      留着它只会让人学会无视告警。删掉，而不是调大阈值。
    """
    out: list[str] = []
    for p in points:
        st = p["station_m"]
        cut, fill = p.get("cut_area_m2"), p.get("fill_area_m2")
        if cut is not None and cut < 0:
            out.append(f"{st} m：挖方面积为负 {cut}")
        if fill is not None and fill < 0:
            out.append(f"{st} m：填方面积为负 {fill}")
        f = p.get("drainage_ditch_flag")
        if f is not None and f not in (0, 1):
            out.append(f"{st} m：计排水沟应为 0/1，实为 {f}")
    # 桩号必须严格递增：.tf 是逐桩文件且按桩号排序。
    # 乱序不会让面积算错，但会让"取某桩号的断面"这类查询静默取错行。
    for a, b in zip(points, points[1:]):
        if b["station_m"] <= a["station_m"]:
            out.append(f"桩号未严格递增：{a['station_m']} → {b['station_m']}")
    return out
