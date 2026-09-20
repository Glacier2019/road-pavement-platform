"""纬地 HintCAD `.HDM` 横断面地面线文件解析器（契约⑤ 第 11 个适配器）。

文件格式（实测自 052201341 毕设工程，1334 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD5.83_HDM_SHUJU     ← 魔数，含厂商版本号
    此后**每三行一个桩号断面**：
        第 1 行  中桩号（米，单独一个数）
        第 2 行  左侧：  <该侧总点数>  [平距 高差] × N
        第 3 行  右侧：  同上
    组与组之间隔一个空行（实测 332 个分隔空行 + 末尾 1 个）。
    行尾 CRLF，列间用 TAB。

    实测：333 个断面 / 2215 个测点，桩号 0.000 → 5805.421 米。

本文件是**外业测量的地面线**，不是设计面
-------------------------------------------------------------------------------
三份独立证据一致：
  ① 教程 §13.8 首句「此文件记录**外业横断面测量**的成果数据」；
  ② 本工程文件名「052201341刘其立道路毕设**横断面地面线文件**.HDM」；
  ③ 内容实测：高差 −30.994 ~ +22.976 m、中位 0.075 m，39.6% 的测点 |高差| > 2 m ——
     这是**地形起伏**的量级，路基设计面的填挖高差不会如此。
横断面**设计**成果在 `.tf`（§13.9），由 tf.py 承接。

为什么本段没有像 `.DMX` 那样锚 station_id
-------------------------------------------------------------------------------
`profile_ground_point`（← `.DMX`）锚 `station_id`，因为 `.DMX` 的桩号**恰好是**
`station_sequence` 的子集。`.HDM` **不是**：实测 333 个断面 vs 332 个桩号，
多一个 5701.461。所以 `cross_section_ground_point` 直接带 `station_km`。
对账（`check_against_stations`）仍然要做，但只报**告警**，不阻断落库。

★ 与 `.DMX` 的另一处形状差异：`.DMX` 一行一个桩号、一行一个高程；
`.HDM` 是**一个桩号 + 两侧 + 每侧若干测点**。所以载荷是嵌套的
（`sections[].left[]/right[]`），落库时摊平成 `cross_section_ground_point` 的行。
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..errors import SourceInvalid

# HINTCAD5.83_HDM_SHUJU —— 版本号捕获出来存进 IR 的 source.vendor_version
MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_HDM_SHUJU$")

SEGMENT = "cross_section"
FILE_KIND = "横断面地面线文件"

#: 单个测点平距的合理区间（米）。实测 0.019 ~ 50.000。
#: 上限的作用是**抓列错位**：把高差读进平距列、或把桩号读进来，数值立刻越界。
OFFSET_MAX_M = 200.0
#: 单个测点高差的合理区间（米，绝对值）。实测 −30.994 ~ +22.976。
#: 地面线是地形起伏，不是路基边坡 —— 单点高差超过 200 m 只可能是读错列。
ELEV_DIFF_MAX_M = 200.0
#: 每侧点数上限。实测 2215 点 / 333 断面，单侧最多几十个。设上限是为了挡住
#: "总点数那列被读成了别的数"——那种错会让循环去读几万行，且静默吞掉后面的断面。
MAX_POINTS_PER_SIDE = 2000


def detect(text: str) -> bool:
    """格式探测：这个文件像不像纬地 `.HDM`？只认魔数，不靠扩展名。"""
    lines = text.splitlines()
    first = lines[0].strip() if lines else ""
    return bool(MAGIC_RE.match(first))


def _nums(line: str) -> list[str]:
    """按 TAB 切（实测都是 TAB）；退回到空白切分以容忍手工另存过的文件。"""
    return line.split("\t") if "\t" in line else line.split()


def _parse_side(raw: str, *, file: str | None, line_no: int, side: str) -> list[dict[str, Any]]:
    """解析一侧：``<总点数> [平距 高差] × N`` → 测点列表。

    ★ 这里做**格式自带的对账**：首列声明的总点数必须等于后面实际的「平距 高差」对数。
      这个格式给了个自校验的机会，不用白不用 —— 它抓的是"行被截断/多读了列"这类
      不会自己报错的畸形。
    """
    parts = _nums(raw)
    if not parts:
        raise SourceInvalid(f"{side}侧行是空的", file=file, line_no=line_no)

    try:
        declared = int(parts[0])
    except ValueError:
        raise SourceInvalid(
            f"{side}侧首列应为该侧总点数（整数），实为 {parts[0].strip()!r}",
            file=file, line_no=line_no,
        ) from None

    if declared < 0:
        raise SourceInvalid(f"{side}侧总点数为负：{declared}", file=file, line_no=line_no)
    if declared > MAX_POINTS_PER_SIDE:
        raise SourceInvalid(
            f"{side}侧总点数 {declared} 超出上限 {MAX_POINTS_PER_SIDE}"
            f"（疑似首列读错：那列不是点数？）",
            file=file, line_no=line_no,
        )

    rest = parts[1:]
    if len(rest) != declared * 2:
        raise SourceInvalid(
            f"{side}侧声明 {declared} 个点，应有 {declared * 2} 个数值（平距/高差成对），"
            f"实为 {len(rest)} 个 —— 行被截断或列数不符",
            file=file, line_no=line_no,
        )

    points: list[dict[str, Any]] = []
    for j in range(declared):
        o_txt, d_txt = rest[2 * j].strip(), rest[2 * j + 1].strip()
        try:
            offset_m = float(o_txt)
            elev_diff_m = float(d_txt)
        except ValueError:
            raise SourceInvalid(
                f"{side}侧第 {j + 1} 个测点不是合法数字：{o_txt!r} / {d_txt!r}",
                file=file, line_no=line_no,
            ) from None

        if not math.isfinite(offset_m) or not math.isfinite(elev_diff_m):
            raise SourceInvalid(
                f"{side}侧第 {j + 1} 个测点不是有限数：{offset_m} / {elev_diff_m}",
                file=file, line_no=line_no,
            )
        # 平距是"到前一测点的距离"，物理上不能为负。教程 §13.8 也没说会有负值。
        if offset_m < 0:
            raise SourceInvalid(
                f"{side}侧第 {j + 1} 个测点平距为负：{offset_m}"
                f"（平距是到前一测点的距离，不可能为负；疑似列错位）",
                file=file, line_no=line_no,
            )
        if offset_m > OFFSET_MAX_M:
            raise SourceInvalid(
                f"{side}侧第 {j + 1} 个测点平距 {offset_m} m 超出合理区间 "
                f"[0, {OFFSET_MAX_M}]（疑似列错位：高差或桩号读进了平距列？）",
                file=file, line_no=line_no,
            )
        if abs(elev_diff_m) > ELEV_DIFF_MAX_M:
            raise SourceInvalid(
                f"{side}侧第 {j + 1} 个测点高差 {elev_diff_m} m 超出合理区间 "
                f"±{ELEV_DIFF_MAX_M}（疑似列错位；地面线是地形起伏，不是路基边坡）",
                file=file, line_no=line_no,
            )
        points.append({"offset_m": offset_m, "elev_diff_m": elev_diff_m})

    return points


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.HDM` → ``{"vendor_version": "5.83", "sections": [{"station_m": 0.0,
    "left": [{"offset_m":…, "elev_diff_m":…}, …], "right": […]}, …]}``

    校验策略与其余适配器一致：**宁可拒绝，不要猜。**
    横断面地面线是"路基填挖、边坡、排水、土方"这一切的输入，读错一列不会报错、
    只会让整条路的横断面反着来。所以每种畸形都直接抛 `SourceInvalid`。

    ⚠ 与 `.DMX` 不同，本文件**没有计数行**（只有魔数 + 数据），所以"读了几组"
    不能自证；能自证的是**每组内部**（首列声明的点数 vs 实际对数）。
    组数是否完整，靠与 `.STA` 对账（`check_against_stations`）—— 那是告警不是异常。
    """
    if not text.strip():
        raise SourceInvalid("空文件", file=file)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_HDM_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1,
        )
    version = m.group(1)

    # 分组规则：**按每 3 个非空行一组**，空行只做结构校验、不参与分组。
    #
    # ⚠ 这里踩过一次：最初写成"遇到空行就切一组"，并在注释里写了
    #   「真正定组的是 3 的倍数这个硬约束」—— 但代码里**根本没实现那句**。
    #   实测文件有空行（DDD B DDD B …）所以真实文件能过；一旦文件不写空行
    #   （或测试夹具没写），999 行会整段读成**一组**，报"实为 999 行"。
    #   抓到它的是"应拒绝"那组用例 —— 正因为那组用例的夹具**故意写得不一样**。
    #   教训：注释里承诺的约束，必须是代码里真的在跑的那条。
    data: list[tuple[int, str]] = [
        (i, raw) for i, raw in enumerate(lines[1:], start=2) if raw.strip() != ""
    ]
    if len(data) % 3 != 0:
        raise SourceInvalid(
            f"非空数据行 {len(data)} 行，不是 3 的倍数"
            f"（每 3 行一个断面：中桩号 / 左 / 右）—— 疑似漏读或被截断",
            file=file,
        )

    # 空行只能出现在组与组**之间**。实测文件确实如此（332 个分隔空行 + 末尾 1 个）。
    # 组内出现空行意味着这一组的 3 行被拆开了，那 3 的倍数整除仍可能成立而错位，
    # 所以单独查一遍 —— 这是格式给的又一个自校验机会。
    group_start_lines = {data[k][0] for k in range(0, len(data), 3)}
    for i, raw in enumerate(lines[1:], start=2):
        if raw.strip() == "":
            continue
        prev_is_blank_or_start = (i - 1) < 2 or lines[i - 2].strip() == ""
        if prev_is_blank_or_start and i not in group_start_lines:
            raise SourceInvalid(
                f"第 {i} 行紧跟空行/文件头，却不是一个断面的起始行"
                f"（断面应从每 3 行的第 1 行开始）—— 空行位置异常",
                file=file, line_no=i,
            )

    sections: list[dict[str, Any]] = []
    for k in range(0, len(data), 3):
        start = data[k][0]
        g = [data[k][1], data[k + 1][1], data[k + 2][1]]

        s_txt = g[0].strip()
        try:
            station_m = float(s_txt)
        except ValueError:
            raise SourceInvalid(
                f"断面中桩号不是合法数字：{s_txt!r}", file=file, line_no=start,
            ) from None
        if not math.isfinite(station_m):
            raise SourceInvalid(f"断面中桩号不是有限数：{station_m}", file=file, line_no=start)
        if station_m < 0:
            raise SourceInvalid(f"断面中桩号为负：{station_m}", file=file, line_no=start)
        if sections and station_m < sections[-1]["station_m"]:
            raise SourceInvalid(
                f"桩号倒退：{sections[-1]['station_m']} → {station_m}（桩号必须单调不减）",
                file=file, line_no=start,
            )

        left = _parse_side(g[1], file=file, line_no=start + 1, side="左")
        right = _parse_side(g[2], file=file, line_no=start + 2, side="右")
        if not left and not right:
            raise SourceInvalid(
                f"断面 {station_m} 左右两侧都是 0 个点（该断面没有任何测量数据）",
                file=file, line_no=start,
            )
        sections.append({"station_m": station_m, "left": left, "right": right})

    if len(sections) < 2:
        raise SourceInvalid(
            f"有效断面不足 2 个（实为 {len(sections)} 个），构不成横断面地面线",
            file=file,
        )

    return {"vendor_version": version, "sections": sections}


def check_against_stations(sections: list[dict[str, Any]],
                           stations: list[dict[str, Any]]) -> list[str]:
    """把断面桩号与桩号序列对账，返回**告警**（不抛异常：可疑 ≠ 非法）。

    ⚠ 与 `.DMX` 的对账**结论不同**，这里要小心别照抄：
      `.DMX` 与 `.STA` 实测逐条相同（332 = 332），所以那边"条数不等"就是**错**。
      `.HDM` 实测是 333 vs 332 —— **多一个 5701.461 是正常的**（测量断面比设计桩号
      多一个加密点很常见）。所以这里**不把"条数不等"当错误**，只报事实，
      由预检报告展示、由用户判断。

    真正值得报的是**方向性异常**：
      · `.HDM` 有、`.STA` 无 —— 正常（加密断面），但要说清有几个、是哪些；
      · `.STA` 有、`.HDM` 无 —— 更可疑：桩号序列里有断面没测。
    """
    warn: list[str] = []
    st_set = {round(s["station_m"], 3) for s in stations}
    hd_set = {round(s["station_m"], 3) for s in sections}

    only_hdm = sorted(hd_set - st_set)
    only_sta = sorted(st_set - hd_set)

    if only_sta:
        head = "、".join(f"{x:.3f}" for x in only_sta[:5])
        warn.append(
            f"{len(only_sta)} 个桩号序列里的桩号**没有对应的横断面**（{head}…）"
            f"—— 这些桩号测不到地面线，横断面在库里的覆盖是不完整的"
        )
    if only_hdm:
        head = "、".join(f"{x:.3f}" for x in only_hdm[:5])
        warn.append(
            f"{len(only_hdm)} 个断面桩号**不在桩号序列里**（{head}…）"
            f"—— 正常（测量加密断面），但这些断面无法经 station_id 关联，"
            f"故本表直接带 station_km"
        )
    if not warn:
        warn.append(f"横断面 {len(sections)} 个与桩号序列 {len(stations)} 个逐条一致")
    return warn


# parse() 返回值里承载"该段载荷"的键名。适配器分发表据此把结果塞进 IR 的 segments[SEGMENT]。
PAYLOAD_KEY = "sections"

__all__ = ["detect", "parse", "check_against_stations", "MAGIC_RE", "SEGMENT", "FILE_KIND",
           "PAYLOAD_KEY", "OFFSET_MAX_M", "ELEV_DIFF_MAX_M", "MAX_POINTS_PER_SIDE"]
