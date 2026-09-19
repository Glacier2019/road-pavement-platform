"""纬地 HintCAD `.CTR` 设计参数控制数据文件解析器（契约⑤ 第 8 个适配器）。

文件格式（**教程 §13.10 逐类定义** + 实测自 052201341 毕设工程，86 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD5.83_CTR_SHUJU          ← 魔数，含厂商版本号
    之后      **关键字行** 与 **数据行** 交替

关键字行形如 ``ZTFBP.DAT``（源文件有的带前导空格，须 strip 后比对）。
数据行的**列数与含义完全由它上面那个关键字决定** —— 这就是本文件与
`.SUP`/`.WID` 那种"固定列数"文件的根本区别，也是本解析器必须**按关键字分派**
而不能"按空格切了再看列数"的原因：同一个 7 列行，在 `TFFD` 下是六类土石百分比，
在 `ZTFBP` 下却是"桩号 + 组数 + 一组边坡"，切错了不会报错，只会把数据读反。

教程 §13.10 共定义 **18 类格式 / 29 个关键字**；本工程实测出现 **36 个关键字**，
其中 **19 个有数据、17 个为空**。另有 `ZDMDG.DAT` 在教程全文**搜不到**（见下）。

本解析器只解析**已建表的 9 组**，其余一律**登记不解析**
-------------------------------------------------------------------------------
「登记不解析」不是偷懒，是**不假装懂**：
  · 17 个空关键字（`ZCHT`/`DZGK`/`ZJSG`/`ZFYHP`/`ZFJBK`/… ）本工程没有数据，
    教程虽有定义，但**没有实测样本可校验**解析器写得对不对；
  · `ZDMDG.DAT` 教程全文没有，20 行数据经比对**既不等于** `.DMX` 地面线
    （46.393~124.188）**也不等于** `.ZDM` 设计标高（54.064~83.814），
    来源与含义都无法确定 —— 经确认**跳过**。
写一个"猜"的解析器，会让下游把猜出来的东西当设计输入用；登记下来则人一眼能看到
"这个文件里还有这些关键字没接"。故它们进 IR 顶层 `notes`，**不进 payload**
（payload 会被原样塞进 IR，而段定义是 additionalProperties: false，多余键会被拒）。

9999 是哨兵不是数值
-------------------------------------------------------------------------------
教程 §13.10 明写：坡度 9999 / −9999 表示**垂直向上 / 向下**；`ZFYHP` 的
护坡顶面标高填 9999 表示**该段不设置反压护坡**。本解析器把坡度列的 9999
收成 ``None``（与 `.SUP` 同一处理），并在 `check_*` 里对"整段 9999"给告警。
**桩号列的 9999 一律拒绝** —— 桩号是这一行的坐标，"忽略桩号"本身无意义。

侧别：源文件用 Z/Y 前缀，本解析器归一成 left/right
-------------------------------------------------------------------------------
`ZTFBP` = 左填方边坡、`YTFBP` = 右填方边坡（Z=左、Y=右，与 `.WID` 的约定一致）。
归一化放在**关键字→(side, kind) 的映射表**里，不靠字符串首字母猜 —— 因为
`ZPSGXS`/`ZYDK`/`ZFYHP` 都以 Z 开头，但它们是**排水沟/用地/护坡**，不是同一个"Z"。

桩号单位：本解析器对外一律用**米**（``station_m``），与其余适配器一致
-------------------------------------------------------------------------------
源文件是米（如 5805.421 表示 K5+805.421）。入库时由 design_import 换算成 km。
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..errors import SourceInvalid

# HINTCAD5.83_CTR_SHUJU —— 版本号捕获出来存进 IR 的 source.vendor_version
MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_CTR_SHUJU$")

# 关键字行：全大写字母/下划线 + .DAT。**必须 strip 后再匹配** ——
# 实测源文件里 GONGDIAN.DAT 等没有前导空格，而 ZTFBP.DAT 有，格式不统一。
KEYWORD_RE = re.compile(r"^([A-Z][A-Z0-9_]*\.DAT)$")

SEGMENT = "design_control"
FILE_KIND = "设计参数控制文件"
PAYLOAD_KEY = "control"

#: 文件结束标记。教程 §13.10 末尾原文：「XXXX.DAT （结束）」。
TERMINATOR = "XXXX.DAT"

#: 坡度哨兵（教程 §13.10：9999 / −9999 = 垂直向上 / 向下）。收成 ``None``。
IGNORE_VALUE = 9999.0

#: 坡度绝对值上限。实测最大 2.000（挖方边坡）；放坡坡度 m 一般 ≤ 10。
#: 取 100 是"大得不合理"，只抓**列错位**（桩号串进坡度列），抓不了语义错。
SLOPE_ABS_MAX = 100.0


def detect(text: str) -> bool:
    """格式探测：这个文件像不像纬地 `.CTR`？只认魔数，不靠扩展名。"""
    lines = text.splitlines()
    first = lines[0].strip() if lines else ""
    return bool(MAGIC_RE.match(first))


# ============================================================================
# 关键字 → 分派规则
# ============================================================================
# 每个规则说明：这一行的数据进哪张表、哪一侧、什么类别。
# 表 = (payload 里的列表名, side 或 None, kind 或 None)
#
# ⚠ 这张表是**唯一**把源关键字映射到 IR 字段的地方。新增关键字只改这里。
_SLOPE = {
    "ZTFBP.DAT": ("left", "fill"),
    "YTFBP.DAT": ("right", "fill"),
    "ZWFBP.DAT": ("left", "cut"),
    "YWFBP.DAT": ("right", "cut"),
}
_DITCH = {
    "ZBGXS.DAT": ("left", "side_ditch"),
    "YBGXS.DAT": ("right", "side_ditch"),
    "ZPSGXS.DAT": ("left", "drainage_ditch"),
    "YPSGXS.DAT": ("right", "drainage_ditch"),
}
_STANDARD = {"ZBZDM.DAT": "left", "YBZDM.DAT": "right"}
_TRENCH = {"ZLCSD.DAT": "left", "YLCSD.DAT": "right"}
_LAND_USE = {"ZYDK.DAT": "left", "YYDK.DAT": "right"}
_SIDE_OVERFILL = {"ZCHT.DAT": "left", "YCHT.DAT": "right"}

#: 已建表、本解析器**会解析**的关键字集合（19 个有数据的关键字里，除 ZDMDG 外全在此）。
#: ZDMDG 不在其中 —— 教程无定义、数据对不上，经确认跳过（只登记）。
PARSED_KEYWORDS: tuple[str, ...] = (
    "ZTFBP.DAT", "YTFBP.DAT", "ZWFBP.DAT", "YWFBP.DAT",
    "ZBGXS.DAT", "YBGXS.DAT", "ZPSGXS.DAT", "YPSGXS.DAT",
    "ZBZDM.DAT", "YBZDM.DAT",
    "ZLCSD.DAT", "YLCSD.DAT",
    "QHSJ.DAT", "HDSJ.DAT", "SUIDAO.DAT",
    "TFFD.DAT",
    "ZYDK.DAT", "YYDK.DAT",
    "ZCHT.DAT", "YCHT.DAT", "DCHT.DAT", "QCHBT.DAT",
    "DZGK.DAT", "SHUIZHUNDIAN.DAT",
)

#: 教程 §13.10 **有定义**、但本工程为空 → 不建表，仅登记。附教程原话摘要。
REGISTERED_NOT_BUILT: dict[str, str] = {
    "ZFJBK.DAT": "左附加路面板块（§13.10-8）：分段终点桩号 + 组数 + (坡度,高度,0,路槽深度)×N",
    "YFJBK.DAT": "右附加路面板块（§13.10-8）",
    "ZJSG.DAT": "左截水沟（§13.10-14）：起点/终点桩号 + 距坡口距离 + 点数 + (坡度,坡高)×N",
    "YJSG.DAT": "右截水沟（§13.10-14）",
    "ZFYHP.DAT": "左反压护坡（§13.10-18）：分段桩号 + 护坡顶宽度 + 护坡顶面标高（9999=该段不设）",
    "YFYHP.DAT": "右反压护坡（§13.10-18）",
}

#: 教程全文**搜不到**、但本工程**有数据**的关键字 —— 与"教程有定义但为空"是两回事，
#: 报给用户时必须分开，否则会得到"这个关键字是空的"这种**反的**结论。
UNDOCUMENTED_WITH_DATA: dict[str, str] = {
    "ZDMDG.DAT": "教程 §13.10 全文无定义。本工程 **20 行** (桩号, 数值)，值域 42~74，"
                 "末行 5805.421→9999.000（按 §13.10 的约定 9999 = 该段不设置）。"
                 "经比对：既不等于 .DMX 地面线（46.393~124.188，332 点），"
                 "也不等于 .ZDM 设计标高（54.064~83.814，12 变坡点）—— 含义无法确定，"
                 "经确认**跳过**，只登记不解析",
}

#: 教程里**没有定义**、但本工程文件里出现的其它关键字 → 同样只登记。
UNDOCUMENTED: tuple[str, ...] = ("GONGDIAN.DAT", "BGKZ.DAT", "PZSTDG.DAT", "RAILWAY_SHJG.DAT")


def _to_float(txt: str, what: str, *, file: str | None, line_no: int) -> float:
    try:
        v = float(txt)
    except ValueError:
        raise SourceInvalid(f"{what}不是合法数字：{txt!r}",
                            file=file, line_no=line_no) from None
    if not math.isfinite(v):
        raise SourceInvalid(f"{what}不是有限数：{v}", file=file, line_no=line_no)
    return v


def _station(txt: str, *, file: str | None, line_no: int) -> float:
    """桩号列。**唯一不接受 9999 的列**（见模块 docstring）。"""
    v = _to_float(txt, "桩号", file=file, line_no=line_no)
    if abs(v - IGNORE_VALUE) < 1e-9:
        raise SourceInvalid(
            "桩号列出现 9999 —— 桩号是这一行的坐标，不能「忽略此数据」"
            "（该哨兵只用于坡度/标高列）", file=file, line_no=line_no)
    if v < 0:
        raise SourceInvalid(f"桩号为负：{v}", file=file, line_no=line_no)
    return v


def _slope(txt: str, *, file: str | None, line_no: int) -> float | None:
    """坡度列。9999 / −9999 = 垂直向上 / 向下（哨兵）→ ``None``。"""
    v = _to_float(txt, "坡度", file=file, line_no=line_no)
    if abs(abs(v) - IGNORE_VALUE) < 1e-9:
        return None
    if abs(v) > SLOPE_ABS_MAX:
        raise SourceInvalid(f"坡度 = {v} 超出合理区间（疑似列错位）",
                            file=file, line_no=line_no)
    return v


def _split_group(raw: str, *, file: str | None, line_no: int,
                 group_size: int, what: str) -> tuple[list[str], int]:
    """切出「桩号 组数 (组内字段×N)」这种**带组数**的行。

    返回 ``(组内字段的扁平列表, 组数)``。
    这是 `.CTR` 里最容易读错的一类：组数是**第二列**，它决定了后面还有多少个数 ——
    按空格切完直接当下标取，会在"组数变了"的行上静默错位。
    """
    parts = raw.split()
    if len(parts) < 2:
        raise SourceInvalid(f"{what}：至少要有「桩号 组数」两列，实为 {len(parts)} 列",
                            file=file, line_no=line_no)
    n = int(_to_float(parts[1], f"{what}的组数", file=file, line_no=line_no))
    if n < 0:
        raise SourceInvalid(f"{what}的组数为负：{n}", file=file, line_no=line_no)
    rest = parts[2:]
    expect = n * group_size
    if len(rest) != expect:
        raise SourceInvalid(
            f"{what}：组数 {n} × 每组 {group_size} 项 = {expect} 个数值，实为 {len(rest)} 个",
            file=file, line_no=line_no)
    return rest, n


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.CTR` → ``{"vendor_version", "control": {...}, "notes": [...]}``

    ``control`` 的 9 个键对应 DDL 的 I1–I9 九张表；每项是一个列表，元素字段名
    与表列名一一对应（桩号在 IR 里叫 ``station_m``，单位米；入库时换算成 km）。

    校验策略与其余适配器一致：**宁可拒绝，不要猜。**
    本文件是横断面戴帽、土方计算、纵断面标注的**参数控制源**，读错一组数不会报错，
    只会让整条路的边坡/沟型/构造物全错。
    """
    if not text.strip():
        raise SourceInvalid("空文件", file=file)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_CTR_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1)
    version = m.group(1)

    control: dict[str, list[dict[str, Any]]] = {
        "slope_segments": [], "ditch_segments": [], "standard_cross_sections": [],
        "roadbed_trenches": [], "structures": [], "earthwork_compositions": [],
        "land_use_widths": [], "extra_fills": [], "design_control_texts": [],
    }
    notes: list[str] = []
    seen: dict[str, int] = {}          # 关键字 → 数据行数
    seen_order: list[str] = []
    cur: str | None = None
    terminated = False

    for i, raw in enumerate(lines[1:], start=2):
        s = raw.strip()
        if s == "":
            continue                    # .CTR 用空行分隔关键字块，空行是**正常**的
        km = KEYWORD_RE.match(s)
        if km:
            cur = km.group(1)
            if cur == TERMINATOR:
                terminated = True
                cur = None
                continue
            seen_order.append(cur)
            seen.setdefault(cur, 0)
            continue
        if cur is None:
            raise SourceInvalid(
                f"数据行出现在任何关键字之前：{s[:40]!r}（格式应为「关键字行 + 数据行」）",
                file=file, line_no=i)
        seen[cur] += 1

        # ── 按关键字分派 ────────────────────────────────────────────────
        if cur in _SLOPE:
            side, kind = _SLOPE[cur]
            rest, n = _split_group(raw, file=file, line_no=i, group_size=4, what=cur)
            st = _station(raw.split()[0], file=file, line_no=i)
            for g in range(n):
                b = g * 4
                control["slope_segments"].append({
                    "side": side, "slope_kind": kind, "station_m": st, "group_seq": g + 1,
                    "slope_ratio": _slope(rest[b], file=file, line_no=i),
                    "control_height_m": _to_float(rest[b + 1], "控制坡高", file=file, line_no=i),
                    "max_height_m": _to_float(rest[b + 2], "最大坡高", file=file, line_no=i),
                    "protection": int(_to_float(rest[b + 3], "砌护控制", file=file, line_no=i)),
                })

        elif cur in _DITCH:
            side, kind = _DITCH[cur]
            rest, n = _split_group(raw, file=file, line_no=i, group_size=3, what=cur)
            st = _station(raw.split()[0], file=file, line_no=i)
            for g in range(n):
                b = g * 3
                control["ditch_segments"].append({
                    "side": side, "ditch_kind": kind, "station_m": st, "group_seq": g + 1,
                    "slope_ratio": _slope(rest[b], file=file, line_no=i),
                    "height_m": _to_float(rest[b + 1], "坡高", file=file, line_no=i),
                    "protection": int(_to_float(rest[b + 2], "砌护控制", file=file, line_no=i)),
                })

        elif cur in _STANDARD:
            parts = raw.split()
            if len(parts) != 10:
                raise SourceInvalid(
                    f"{cur}：应为「桩号 + 9 个数值」（半幅中分带宽/中分带坡度/中分带高度/"
                    f"行车道宽/行车道坡度/硬路肩宽/硬路肩坡度/土路肩宽/土路肩坡度），实为 {len(parts)} 列",
                    file=file, line_no=i)
            names = ("median_half_width_m", "median_crossfall_pct", "median_height_m",
                     "lane_width_m", "lane_crossfall_pct",
                     "hard_shoulder_width_m", "hard_shoulder_crossfall_pct",
                     "earth_shoulder_width_m", "earth_shoulder_crossfall_pct")
            row: dict[str, Any] = {"side": _STANDARD[cur],
                                   "station_m": _station(parts[0], file=file, line_no=i)}
            for k, nm in enumerate(names):
                row[nm] = _to_float(parts[k + 1], nm, file=file, line_no=i)
            control["standard_cross_sections"].append(row)

        elif cur in _TRENCH:
            parts = raw.split()
            if len(parts) != 5:
                raise SourceInvalid(
                    f"{cur}：应为「桩号 + 4 个路槽深度」（中分带/行车道/硬路肩/土路肩），"
                    f"实为 {len(parts)} 列", file=file, line_no=i)
            names = ("median_trench_depth_m", "lane_trench_depth_m",
                     "hard_shoulder_trench_depth_m", "earth_shoulder_trench_depth_m")
            row = {"side": _TRENCH[cur], "station_m": _station(parts[0], file=file, line_no=i)}
            for k, nm in enumerate(names):
                row[nm] = _to_float(parts[k + 1], nm, file=file, line_no=i)
            control["roadbed_trenches"].append(row)

        elif cur == "QHSJ.DAT":
            # 起点 终点 标注桩号 名称 跨径 结构形式 路线角度 控制标高 标高控制类型 分幅类型 尾随第3个
            #
            # ★ 实测 11 列，而教程正文只列了 10 项 —— **教程自己前后不一致**：
            #   正文：「桥梁起点桩号，终点桩号，标注桩号，桥名称，跨径，主要结构形式，
            #          路线角度，控制标高，标高控制类型…以及桥梁分幅类型…」= 10 项；
            #   示例：「4100 4280 4190 京广铁路分离式立交桥 5*35m 预应力混凝土箱梁
            #          90.00 135.80 0 1 0」= **11 个**。
            #   本工程实测同样 11 个（273.000 333.000 300.000 桥 30+30 混凝土空心板梁桥
            #   90.0000 0.0000 0 1 0）。
            #   故尾部有 **3 个整数**，正文只解释了前 2 个。第 3 个**原样存下待考**，
            #   不猜、不丢 —— 丢一个字段不会报错，只会让以后对不上。
            parts = raw.split()
            if len(parts) != 11:
                raise SourceInvalid(
                    f"QHSJ.DAT：应为 11 列（教程正文列了 10 项，但教程示例与实测文件都是 11 列），"
                    f"实为 {len(parts)} 列", file=file, line_no=i)
            st = _station(parts[0], file=file, line_no=i)
            control["structures"].append({
                "structure_kind": "bridge", "anchor_station_m": st,
                "start_station_m": st,
                "end_station_m": _station(parts[1], file=file, line_no=i),
                "center_station_m": _station(parts[2], file=file, line_no=i),
                "name": parts[3],
                "span_text": parts[4], "structure_form": parts[5],
                "angle_deg": _to_float(parts[6], "路线角度", file=file, line_no=i),
                "control_elev_m": _to_float(parts[7], "控制标高", file=file, line_no=i),
                "elev_control_type": int(_to_float(parts[8], "标高控制类型", file=file, line_no=i)),
                "deck_type": int(_to_float(parts[9], "桥梁分幅类型", file=file, line_no=i)),
                "trailing_flag": int(_to_float(parts[10], "尾随第 3 个整数", file=file, line_no=i)),
            })

        elif cur == "HDSJ.DAT":
            # 中心桩号 与路线角度 跨径说明 构造物名称 控制标高
            parts = raw.split()
            if len(parts) != 5:
                raise SourceInvalid(f"HDSJ.DAT：应为 5 列，实为 {len(parts)} 列",
                                    file=file, line_no=i)
            st = _station(parts[0], file=file, line_no=i)
            control["structures"].append({
                "structure_kind": "culvert", "anchor_station_m": st,
                "start_station_m": None, "end_station_m": None, "center_station_m": st,
                "name": parts[3], "span_text": parts[2], "structure_form": None,
                "angle_deg": _to_float(parts[1], "与路线角度", file=file, line_no=i),
                "control_elev_m": _to_float(parts[4], "控制标高", file=file, line_no=i),
                "elev_control_type": None, "deck_type": None, "trailing_flag": None,
            })

        elif cur == "SUIDAO.DAT":
            # 隧道起点桩号 终点桩号 隧道名称（只占一行）
            parts = raw.split()
            if len(parts) < 3:
                raise SourceInvalid(f"SUIDAO.DAT：应为「起点 终点 名称」3 列，实为 {len(parts)} 列",
                                    file=file, line_no=i)
            st = _station(parts[0], file=file, line_no=i)
            control["structures"].append({
                "structure_kind": "tunnel", "anchor_station_m": st,
                "start_station_m": st,
                "end_station_m": _station(parts[1], file=file, line_no=i),
                "center_station_m": None,
                # 名称可能含空格 → 第 3 列起全算名称（教程没给名称的长度约定）
                "name": " ".join(parts[2:]), "span_text": None, "structure_form": None,
                "angle_deg": None, "control_elev_m": None,
                "elev_control_type": None, "deck_type": None, "trailing_flag": None,
            })

        elif cur == "TFFD.DAT":
            # 分段桩号 + 六类土（石）百分比
            parts = raw.split()
            if len(parts) != 7:
                raise SourceInvalid(
                    f"TFFD.DAT：应为「桩号 + 六类土石百分比」7 列，实为 {len(parts)} 列",
                    file=file, line_no=i)
            row = {"station_m": _station(parts[0], file=file, line_no=i)}
            for k in range(6):
                row[f"pct_{k + 1}"] = _to_float(parts[k + 1], f"第{k + 1}类土石占比",
                                                file=file, line_no=i)
            control["earthwork_compositions"].append(row)

        elif cur in _LAND_USE:
            # 桩号 填方用地宽度 挖方用地宽度
            parts = raw.split()
            if len(parts) != 3:
                raise SourceInvalid(f"{cur}：应为「桩号 填方用地宽度 挖方用地宽度」3 列，"
                                    f"实为 {len(parts)} 列", file=file, line_no=i)
            control["land_use_widths"].append({
                "side": _LAND_USE[cur],
                "station_m": _station(parts[0], file=file, line_no=i),
                "fill_land_width_m": _to_float(parts[1], "填方用地宽度", file=file, line_no=i),
                "cut_land_width_m": _to_float(parts[2], "挖方用地宽度", file=file, line_no=i),
            })

        elif cur in _SIDE_OVERFILL:
            # 分段终点桩号 一侧超填水平宽度
            parts = raw.split()
            if len(parts) != 2:
                raise SourceInvalid(f"{cur}：应为「桩号 超填水平宽度」2 列，实为 {len(parts)} 列",
                                    file=file, line_no=i)
            control["extra_fills"].append({
                "side": _SIDE_OVERFILL[cur], "fill_kind": "side_overfill",
                "station_m": _station(parts[0], file=file, line_no=i),
                "width_m": _to_float(parts[1], "一侧超填水平宽度", file=file, line_no=i),
                "thickness_m": None,
            })

        elif cur == "DCHT.DAT":
            # 分段终点桩号 超填厚度
            parts = raw.split()
            if len(parts) != 2:
                raise SourceInvalid(f"DCHT.DAT：应为「桩号 超填厚度」2 列，实为 {len(parts)} 列",
                                    file=file, line_no=i)
            control["extra_fills"].append({
                "side": None, "fill_kind": "top_overfill",
                "station_m": _station(parts[0], file=file, line_no=i),
                "width_m": None,
                "thickness_m": _to_float(parts[1], "超填厚度", file=file, line_no=i),
            })

        elif cur == "QCHBT.DAT":
            # 分段终点桩号 左右侧坡脚外增加宽度 清除表土厚度
            parts = raw.split()
            if len(parts) != 3:
                raise SourceInvalid(f"QCHBT.DAT：应为「桩号 坡脚外增加宽度 清除表土厚度」3 列，"
                                    f"实为 {len(parts)} 列", file=file, line_no=i)
            control["extra_fills"].append({
                "side": None, "fill_kind": "topsoil_clear",
                "station_m": _station(parts[0], file=file, line_no=i),
                "width_m": _to_float(parts[1], "坡脚外增加宽度", file=file, line_no=i),
                "thickness_m": _to_float(parts[2], "清除表土厚度", file=file, line_no=i),
            })

        elif cur == "DZGK.DAT":
            # 分段终点桩号 + 地质概况文字说明（**可含空格，不可换行** → 第 2 列起全算文字）
            parts = raw.split(None, 1)
            if len(parts) < 2:
                raise SourceInvalid("DZGK.DAT：应为「桩号 + 地质概况文字」，文字缺失",
                                    file=file, line_no=i)
            control["design_control_texts"].append({
                "text_kind": "geology",
                "station_m": _station(parts[0], file=file, line_no=i),
                "name": None, "elev_m": None, "content": parts[1].strip(),
            })

        elif cur == "SHUIZHUNDIAN.DAT":
            # 位置桩号 名称 高程 说明
            parts = raw.split()
            if len(parts) != 4:
                raise SourceInvalid(f"SHUIZHUNDIAN.DAT：应为「桩号 名称 高程 说明」4 列，"
                                    f"实为 {len(parts)} 列", file=file, line_no=i)
            control["design_control_texts"].append({
                "text_kind": "benchmark",
                "station_m": _station(parts[0], file=file, line_no=i),
                "name": parts[1],
                "elev_m": _to_float(parts[2], "水准点高程", file=file, line_no=i),
                "content": parts[3],
            })

        # 其余关键字（17 个空 + ZDMDG + 教程未定义者）**不解析**，只计数登记 —— 见模块 docstring。

    # ── 收尾：登记「没接」的关键字 ──────────────────────────────────────
    if not terminated:
        notes.append(f"{TERMINATOR}（教程 §13.10 的文件结束标记）未出现 —— "
                     f"文件可能被截断，已解析到的关键字共 {len(seen_order)} 个")
    for kw in seen_order:
        if kw in REGISTERED_NOT_BUILT:
            notes.append(f"{kw}：教程有定义但本工程为空（{seen[kw]} 行）—— "
                         f"{REGISTERED_NOT_BUILT[kw]}；本版**不建表**，仅登记")
        elif kw in UNDOCUMENTED_WITH_DATA:
            notes.append(f"{kw}：**教程无定义但本工程有 {seen[kw]} 行数据** —— "
                         f"{UNDOCUMENTED_WITH_DATA[kw]}")
        elif kw in UNDOCUMENTED:
            notes.append(f"{kw}：**教程 §13.10 未定义**，本工程出现 {seen[kw]} 行 —— "
                         f"含义不明，本版不解析、不建表，仅登记")
        elif kw not in PARSED_KEYWORDS:
            notes.append(f"{kw}：未在 PARSED_KEYWORDS 中登记，本版不解析（{seen[kw]} 行）")

    if not seen_order:
        raise SourceInvalid("没有任何关键字行（只有魔数）", file=file)
    if not any(control.values()):
        raise SourceInvalid(
            f"有 {len(seen_order)} 个关键字，但没有任何一个被解析出数据 —— "
            f"要么文件是空的，要么全是本版不解析的关键字", file=file)

    return {"vendor_version": version, "control": control, "notes": notes}


# ============================================================================
# 对账 / 合理性检查（返回**告警**，不抛异常 —— 可疑 ≠ 非法）
# ============================================================================

def check_against_stations(control: dict[str, list[dict[str, Any]]],
                           stations: list[dict[str, Any]]) -> list[str]:
    """所有分段桩号必须落在**路线范围之内**。返回告警。

    与 `.ZDM`/`.WID` 的同类检查**含义不同**：
      · `.ZDM` 不覆盖全线 = **设计没做到头**（错误级）；
      · `.WID` 不覆盖全线 = 源文件的**真实缺口**（本工程后 103.960 m 无宽度数据）；
      · `.CTR` 的**分段桩号本来就不必覆盖全线** —— 分段变化只在有变化的地方写一行，
        本工程 `ZTFBP` 就只写了 5805.421 一行（整条路一个边坡方案）。
    所以这里只查**越界**，不查覆盖。
    """
    warn: list[str] = []
    if not stations:
        return warn
    lo, hi = stations[0]["station_m"], stations[-1]["station_m"]
    for key, rows in control.items():
        for r in rows:
            for fld in ("station_m", "anchor_station_m", "start_station_m", "end_station_m"):
                v = r.get(fld)
                if v is None:
                    continue
                if v < lo - 1e-6 or v > hi + 1e-6:
                    warn.append(f"{key} 的 {fld}={v:.3f} m 越出路线范围 "
                                f"[{lo:.3f}, {hi:.3f}] m")
    return warn


def check_control(control: dict[str, list[dict[str, Any]]]) -> list[str]:
    """`.CTR` 的**内部一致性**检查。返回告警。

    判据全部由教程定义与实测数据推导：
      · **六类土石占比之和**：教程说这是"占挖方数量中的百分比"，六项之和应为 100。
        本工程实测 = 100.000。但**不设 CHECK 约束**、这里也只是告警 ——
        设计中间稿的百分比可以先不配平。
      · **边坡/沟的组数**：0 组是**合法**的（本工程排水沟就是 0 组 = 该段不设沟），
        但一级边坡都没有（挖方/填方各 0 组）就说明这行没意义，值得提出来。
      · **同一 (side, kind) 下桩号重复**：同一桩号两行会让"该桩号取哪个值"变成
        未定义行为（与 `.SUP` 拒绝同桩号同一理由）。
    """
    warn: list[str] = []

    for r in control.get("earthwork_compositions", []):
        vals = [r.get(f"pct_{k}") for k in range(1, 7)]
        if all(v is not None for v in vals):
            s = sum(vals)
            if abs(s - 100.0) > 0.01:
                warn.append(f"土石成份（桩号 {r['station_m']:.3f} m）六类占比之和为 {s:.3f}%，"
                            f"教程口径应为 100%")

    for r in control.get("slope_segments", []):
        if r.get("protection") not in (0, 1):
            warn.append(f"边坡（桩号 {r['station_m']:.3f} m 第 {r['group_seq']} 级）砌护控制 = "
                        f"{r.get('protection')}，教程只定义 0（不砌护）/ 1（砌护）")
    for r in control.get("ditch_segments", []):
        if r.get("protection") not in (0, 1):
            warn.append(f"边沟/排水沟（桩号 {r['station_m']:.3f} m 第 {r['group_seq']} 个折点）"
                        f"砌护控制 = {r.get('protection')}，教程只定义 0 / 1")

    # 同一 (side, kind[, group_seq]) + 桩号重复 → 取值未定义。
    #
    # ⚠ 这里**必须**把 group_seq 算进身份，否则会报出一堆**假告警**：
    #   .CTR 的一行是"分段终点桩号 + 组数 + N 组数据"，**同一个桩号下天然有多行**
    #   （本工程填方边坡在 5805.421 就有 5 级、挖方 6 级，边沟 3 个折点）。
    #   把 group_seq 漏掉，等于对每一级边坡都喊一次"重复"—— 一个恒真的检查
    #   比没有检查更糟（它会把真告警淹没）。
    #   这与 DDL 的唯一键 (section_id, side, slope_kind, station_km, group_seq) 一致。
    for key, fields in (("slope_segments", ("side", "slope_kind", "group_seq")),
                        ("ditch_segments", ("side", "ditch_kind", "group_seq")),
                        ("standard_cross_sections", ("side",)),
                        ("roadbed_trenches", ("side",)),
                        ("land_use_widths", ("side",))):
        seen: set[tuple] = set()
        for r in control.get(key, []):
            sig = tuple(r.get(f) for f in fields) + (r["station_m"],)
            if sig in seen:
                warn.append(f"{key} 出现重复的 {'/'.join(str(r.get(f)) for f in fields)} "
                            f"桩号 {r['station_m']:.3f} m —— 该桩号取哪个值未定义")
            seen.add(sig)

    return warn


__all__ = ["detect", "parse", "check_control", "check_against_stations",
           "MAGIC_RE", "KEYWORD_RE", "SEGMENT", "FILE_KIND", "PAYLOAD_KEY",
           "TERMINATOR", "IGNORE_VALUE", "SLOPE_ABS_MAX",
           "PARSED_KEYWORDS", "REGISTERED_NOT_BUILT", "UNDOCUMENTED_WITH_DATA",
           "UNDOCUMENTED"]
