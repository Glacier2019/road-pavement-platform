"""纬地 HintCAD `.SUP` 超高过渡数据文件解析器（契约⑤ 第 6 个适配器）。

文件格式（**教程 §13.5 逐列定义** + 实测自 052201341 毕设工程，76 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD5.83_SUP_SHUJU          ← 魔数，含厂商版本号
    第 2 行起 `%12.2f\t` × 7                 ← TAB 分隔，行尾 CRLF

    列 1  左侧土路肩的横坡值
    列 2  左侧硬路肩的横坡值
    列 3  左侧行车道（路面）的横坡值
    列 4  **桩号**
    列 5  右侧行车道横坡值
    列 6  右侧硬路肩的横坡值
    列 7  右侧土路肩横坡值

教程原文（§13.5）：

    「每一行前三项数据与后三项数据绕第四项数据呈对称位置排列。分别为左侧土路肩的
      横坡值、左侧硬路肩的横坡值、左侧行车道（路面）的横坡值、桩号、右侧行车道横坡值、
      右侧硬路肩的横坡值、右侧土路肩横坡值。其中数据 9999 表示可以忽略此数据，横坡渐变
      至此位置时，系统跳过此数据的计算继续进行横坡的超高渐变。」

★「对称」是**位置对称，不是值相等** —— 别读错
-------------------------------------------------------------------------------
实测 76 行里，左右土路肩**值相等**的只有 2 行。因为超高段是**单向横坡**：
左行车道 +4.00 / 右行车道 −4.00，左右**异号**。把"对称"理解成"值相等"会写出一个
永远失败的校验器。教程说的是"前三项与后三项**绕**第四项排列"，讲的是**列的位置**。

9999 的语义：**用数据独立验证过，不是靠猜**
-------------------------------------------------------------------------------
三种可能语义：① 沿用上值；② 缺值/无效；③ 此点不约束该列（过渡照常继续）。

实测判据：跨过一个 9999 之后，该列的下一个实值**有时相同、有时改变**
（左土路肩：相同 39 次 / 改变 16 次；左行车道：相同 25 次 / 改变 24 次）。
若是①，跨过后必然**全部相同**（39+16 次里应当 55 次相同）—— 数据否决了①。
于是与教程的③一致：**该列在此位置不参与约束，过渡照常继续**。
故本解析器把 9999 收成 `None`：`.SUP` 每格非数即 9999，故 `None` 与"缺值"无歧义。

桩号列**不接受** 9999
-------------------------------------------------------------------------------
实测桩号列 0 次 9999（76 行全是真桩号）。这是有道理的：桩号是这一行的**坐标**，
"忽略桩号"这句话本身无意义。所以桩号列出现 9999 一律**拒绝**，不当作可忽略。

这是**设计输入**，不是逐桩结果
-------------------------------------------------------------------------------
本文件给的是**过渡转折点**（实测 76 个），而 `station_sequence` 逐桩是 332 个 ——
两者粒度不同，所以 `superelev_transition` 单独成段、单独成表。
逐桩的六列横坡由 `geometry_point` 存**插值后**的结果（本适配器不管插值）。
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..errors import SourceInvalid

# HINTCAD5.83_SUP_SHUJU —— 版本号捕获出来存进 IR 的 source.vendor_version
MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_SUP_SHUJU$")

# ★ 本解析器的输出**已被独立校验**（契约测试第 12b 组）：
#   `.lj` 那 11 个「高差」列可以由本文件的逐桩横坡 + `.lj` 自己的宽度**完全复现**
#   （332/332 行，最大残差 9.0e-5）。两个文件互不抄写，所以这是一次真交叉验证：
#     · 横坡**值**对 —— 复现不出来就说明本文件读错了列或读错了符号；
#     · 9999 的**语义**对 —— 必须是「跳过（插值穿过）」，按「沿用上值」算有
#       55/332 行不符；
#     · 而且校验里**必须**给右半幅翻号（σ = +1 左 / −1 右），因为本文件左右两侧
#       用的是同一套符号约定（正常路拱两侧都写 −2.00），而高差是「离开旋转轴就
#       下降」。漏掉翻号时 332 行**全部**不符。
#   换言之：本文件若哪天被改坏（列序、符号、9999 处理），第 12b 组会红。

SEGMENT = "superelev_transition"
FILE_KIND = "超高过渡数据文件"
PAYLOAD_KEY = "points"

#: 数据行的字段数。多一列少一列都说明格式与预期不符，宁可拒绝也不猜列义。
FIELD_COUNT = 7

#: 「可以忽略此数据」的哨兵值（教程 §13.5）。收成 ``None``，见模块 docstring。
IGNORE_VALUE = 9999.0

#: 桩号列的下标（0 起）。单独列出来，因为它是**唯一不接受 9999** 的列。
STATION_IDX = 3

#: 六个横坡列的下标 → IR 字段名。顺序即教程原文的顺序，**不可重排**：
#: 前三项在左、后三项在右，桩号夹在中间。重排会让左右互换而**不报任何错**。
PCT_COLUMNS: tuple[tuple[int, str], ...] = (
    (0, "earth_shoulder_left_pct"),
    (1, "hard_shoulder_left_pct"),
    (2, "lane_left_pct"),
    (4, "lane_right_pct"),
    (5, "hard_shoulder_right_pct"),
    (6, "earth_shoulder_right_pct"),
)

#: 横坡绝对值上限（%）。实测最大 6.00（行车道）／5.00（路肩）；公路最大超高一般 ≤ 8~10。
#: 取 20 是"大得不合理"，只用来抓**列错位**（桩号串进横坡列），抓不了"像横坡其实是别的数"。
PCT_ABS_MAX = 20.0

#: 超过此值**告警**（不拒绝）：超出任何常规设计的横坡量级。
PCT_WARN_ABS = 10.0


def detect(text: str) -> bool:
    """格式探测：这个文件像不像纬地 `.SUP`？只认魔数，不靠扩展名。"""
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def _to_float(txt: str, what: str, *, file: str | None, line_no: int) -> float:
    try:
        v = float(txt)
    except ValueError:
        raise SourceInvalid(f"{what}不是合法数字：{txt!r}",
                            file=file, line_no=line_no) from None
    if not math.isfinite(v):
        raise SourceInvalid(f"{what}不是有限数：{v}", file=file, line_no=line_no)
    return v


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.SUP` → ``{"vendor_version": "5.83", "points": [...]}``

    ``points`` 每项：``{seq_no, station_m, earth_shoulder_left_pct, hard_shoulder_left_pct,
    lane_left_pct, lane_right_pct, hard_shoulder_right_pct, earth_shoulder_right_pct}``。
    六个横坡为 ``None`` 表示源文件写了 9999（"忽略此数据"，见模块 docstring）。

    校验策略与其余适配器一致：**宁可拒绝，不要猜。**
    超高横坡直接决定路面排水方向与行车安全，读错一列不会报错，只会让整条路的
    横坡反着来（左变右、正变负），所以每种畸形都抛 `SourceInvalid`。
    """
    if not text.strip():
        raise SourceInvalid("空文件", file=file)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_SUP_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1,
        )
    version = m.group(1)

    points: list[dict[str, Any]] = []
    for i, raw in enumerate(lines[1:], start=2):
        # 末尾空行放行（导出工具常留一行），中间空行意味着漏读一段过渡。
        if raw.strip() == "":
            if all(l.strip() == "" for l in lines[i:]):
                break
            raise SourceInvalid("文件中间出现空行（疑似漏读一段超高过渡）", file=file, line_no=i)

        parts = raw.split("\t") if "\t" in raw else raw.split()
        if len(parts) != FIELD_COUNT:
            raise SourceInvalid(
                f"字段数应为 {FIELD_COUNT}（左土路肩/左硬路肩/左行车道/桩号/"
                f"右行车道/右硬路肩/右土路肩），实为 {len(parts)}：{raw.strip()[:40]!r}",
                file=file, line_no=i,
            )

        # ① 桩号列：唯一不接受 9999 的列（实测 0 次；"忽略桩号"本身无意义）
        station_txt = parts[STATION_IDX].strip()
        station_m = _to_float(station_txt, "桩号", file=file, line_no=i)
        if abs(station_m - IGNORE_VALUE) < 1e-9:
            raise SourceInvalid(
                f"桩号列出现 9999 —— 桩号是这一行的坐标，不能「忽略此数据」"
                f"（该哨兵只用于横坡列）", file=file, line_no=i)
        if station_m < 0:
            raise SourceInvalid(f"桩号为负：{station_m}", file=file, line_no=i)
        if points and station_m <= points[-1]["station_m"]:
            # 桩号必须**严格**递增：同桩号的两个过渡点无法解释（是同一位置的两种横坡？）
            raise SourceInvalid(
                f"桩号未严格递增：{points[-1]['station_m']} → {station_m}"
                f"（同桩号的两个过渡点无法解释）", file=file, line_no=i)

        # ② 六个横坡列：9999 → None；其余做量级检查
        row: dict[str, Any] = {"seq_no": len(points) + 1, "station_m": station_m}
        for idx, name in PCT_COLUMNS:
            v = _to_float(parts[idx].strip(), name, file=file, line_no=i)
            if abs(v - IGNORE_VALUE) < 1e-9:
                row[name] = None                 # 「可以忽略此数据」
                continue
            if abs(v) > PCT_ABS_MAX:
                raise SourceInvalid(
                    f"{name} = {v} 超出合理区间 [−{PCT_ABS_MAX:g}, {PCT_ABS_MAX:g}] %"
                    f"（疑似列错位）", file=file, line_no=i)
            row[name] = v
        points.append(row)

    if not points:
        raise SourceInvalid("没有任何数据行（只有魔数）", file=file)
    if len(points) < 2:
        raise SourceInvalid(f"过渡点不足 2 个（实为 {len(points)} 个），构不成超高过渡",
                            file=file)

    return {"vendor_version": version, "points": points}


def check_superelev(points: list[dict[str, Any]]) -> list[str]:
    """超高横坡的**物理合理性**检查。返回**告警**（可疑 ≠ 非法），不抛异常。

    三条判据**全部由实测数据推导**，不是凭常识写的：

      · **土路肩横坡为正**：实测左右土路肩各 0 次为正（范围 [−5, −2]）。土路肩的作用
        是把水**排出路基**，所以它必然朝外下倾；出现正值说明列读错或数据异常。
      · **laneL + laneR > 0**：实测取值范围 [−4, 0]。双向路拱时左右同号（和 −4），
        单向超高时左右异号（和 0）。**和为正**意味着左右行车道朝同一侧排水 ——
        一个路拱不可能同时向两边排水，故为正必属异常。
      · **整行六个横坡全为 None**：实测 0 行。这样的行对过渡**不施加任何约束**，
        是"有这一行、但没有任何信息"，值得顶到人眼前而不是静默接受。

    为什么不检查"左右是否相等"：教程说的"对称"是**位置对称**。实测 76 行里左右土路肩
    值相等的只有 2 行（超高段是单向横坡，左右本就异号）。按"相等"检查会得出一个
    **永远失败**的结论 —— 那比没有检查更糟。
    """
    warn: list[str] = []
    for p in points:
        seq, st = p["seq_no"], p["station_m"]
        left = p.get("earth_shoulder_left_pct")
        right = p.get("earth_shoulder_right_pct")
        if left is not None and left > 0:
            warn.append(f"过渡点 {seq}（{st:.3f} m）左侧土路肩横坡为正（{left}%）"
                        f"—— 土路肩应向外排水，实测样本中从无正值，请人工确认")
        if right is not None and right > 0:
            warn.append(f"过渡点 {seq}（{st:.3f} m）右侧土路肩横坡为正（{right}%）"
                        f"—— 土路肩应向外排水，实测样本中从无正值，请人工确认")

        ll, lr = p.get("lane_left_pct"), p.get("lane_right_pct")
        if ll is not None and lr is not None and ll + lr > 0:
            warn.append(f"过渡点 {seq}（{st:.3f} m）左右行车道横坡之和为正"
                        f"（{ll}% + {lr}% = {ll + lr}%）—— 路拱不可能同时向两侧排水，"
                        f"实测样本该和恒 ≤ 0，请人工确认")

        if all(p.get(name) is None for _, name in PCT_COLUMNS):
            warn.append(f"过渡点 {seq}（{st:.3f} m）六个横坡全为 9999"
                        f"—— 该行对过渡不施加任何约束，实测样本中无此情况")

        for _, name in PCT_COLUMNS:
            v = p.get(name)
            if v is not None and abs(v) > PCT_WARN_ABS:
                warn.append(f"过渡点 {seq}（{st:.3f} m）的 {name} = {v}%，"
                            f"超出常规设计量级（实测最大 6%），请人工确认")
    return warn


def check_against_stations(points: list[dict[str, Any]],
                           stations: list[dict[str, Any]]) -> list[str]:
    """过渡段必须落在**路线范围之内**。返回告警（不抛异常）。

    与 `.ZDM` 的同类检查**不同**：`.ZDM` 的设计线必须与路线**首尾对齐**（短了就是
    设计没做到头），而 `.SUP` 的过渡点**不必**从 0 开始、也不必到终点结束 ——
    没有超高过渡的路段本来就没有过渡点。所以这里只查**越界**，不查首尾对齐。
    """
    warn: list[str] = []
    if not points or not stations:
        return warn
    lo, hi = stations[0]["station_m"], stations[-1]["station_m"]
    if points[0]["station_m"] < lo - 1e-6:
        warn.append(f"超高过渡起点 {points[0]['station_m']} m 早于路线起点 {lo} m")
    if points[-1]["station_m"] > hi + 1e-6:
        warn.append(f"超高过渡终点 {points[-1]['station_m']} m 晚于路线终点 {hi} m")
    return warn


def superelev_at(points: list[dict[str, Any]], station_m: float,
                 column: str = "lane_left_pct") -> float | None:
    """某桩号某列的横坡：按「9999 不约束该列」的规则**跨过**哨兵点线性插值。

    规则来自教程 §13.5「系统跳过此数据的计算继续进行横坡的超高渐变」——
    也就是：某列的过渡**只由该列的实值点定义**，中间夹着的 9999 点对它不存在。

    桩号落在已知范围之外时返回 ``None``，**不做外推**（与 `.ZDM` 的
    `design_elevation_at` 同一理由：外推出来的横坡看着像真的，会被下游当设计值用）。
    """
    pairs = [(p["station_m"], p[column]) for p in points if p.get(column) is not None]
    if not pairs:
        return None
    if station_m < pairs[0][0] or station_m > pairs[-1][0]:
        return None
    for (s1, v1), (s2, v2) in zip(pairs, pairs[1:]):
        if s1 <= station_m <= s2:
            if s2 == s1:                        # parse 已挡住重复桩号，这里是纵深防御
                return v1
            t = (station_m - s1) / (s2 - s1)
            return v1 + (v2 - v1) * t
    return pairs[-1][1]


__all__ = ["detect", "parse", "check_superelev", "check_against_stations",
           "superelev_at", "MAGIC_RE", "SEGMENT", "FILE_KIND", "PAYLOAD_KEY",
           "FIELD_COUNT", "IGNORE_VALUE", "STATION_IDX", "PCT_COLUMNS",
           "PCT_ABS_MAX", "PCT_WARN_ABS"]
