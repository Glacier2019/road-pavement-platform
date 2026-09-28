"""纬地 HintCAD `.STA` 桩号序列文件解析器（契约⑤ 的第一个适配器）。

文件格式（实测自 052201341 毕设工程，333 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD5.84_STA_SHUJU          ← 魔数，含厂商版本号
    第 2 行起 `%12.3f\t%10d`                 ← 桩号(米) TAB 序号
    行尾 CRLF

实测：332 条数据，0.000 → 5805.421 米。**并非全程等距**——
规整 20 m 间隔到 540.000，随后出现 545.874 这类不规则点。
因此解析器**不得假设等距**（假设等距会在第一个不规则点处静默错位）。

为什么这个文件是导入链路的第一站
-------------------------------------------------------------------------------
`station_sequence` 是 GE 域的**桩号锚定基准**：其余逐桩数据表全部以 FK 锚在它上面。
先把这一张表导对，才能验证「桩号一等实体」这条设计是否真的成立。
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..errors import SourceInvalid

# HINTCAD5.84_STA_SHUJU —— 版本号捕获出来存进 IR 的 source.vendor_version
MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_STA_SHUJU$")

SEGMENT = "station_sequence"
FILE_KIND = "桩号序列文件"


def detect(text: str) -> bool:
    """格式探测：这个文件像不像纬地 `.STA`？

    只认魔数，不靠扩展名——扩展名可以改，魔数不会。
    """
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.STA` → ``{"vendor_version": "5.84", "points": [{"station_m":0.0,"seq_no":1}, …]}``

    校验策略：**宁可拒绝，不要猜。**
    猜错一个字段的后果是静默写入错误桩号，而桩号是下游一切逐桩数据的对齐基准——
    错一条，挂在上面的 WIM/病害/试验数据全部错位，且**不会有任何报错**。
    所以这里对每种畸形都直接抛 `SourceInvalid`，而不是跳过或补默认值。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines:
        raise SourceInvalid("空文件", file=file)

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_STA_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1,
        )
    version = m.group(1)

    points: list[dict[str, Any]] = []
    for i, raw in enumerate(lines[1:], start=2):
        # 允许文件末尾的空行（导出工具常留一行），但**不允许中间空行**——
        # 中间空行意味着漏读了一段桩号，是静默的数据丢失。
        if raw.strip() == "":
            if all(l.strip() == "" for l in lines[i:]):
                break
            raise SourceInvalid("文件中间出现空行（疑似漏读一段桩号）", file=file, line_no=i)

        parts = raw.split("\t") if "\t" in raw else raw.split()
        if len(parts) != 2:
            raise SourceInvalid(
                f"字段数应为 2（桩号 TAB 序号），实为 {len(parts)}：{raw.strip()[:40]!r}",
                file=file, line_no=i,
            )
        s_txt, n_txt = parts[0].strip(), parts[1].strip()
        try:
            station_m = float(s_txt)
        except ValueError:
            raise SourceInvalid(f"桩号不是合法数字：{s_txt!r}", file=file, line_no=i) from None
        try:
            seq_no = int(n_txt)
        except ValueError:
            raise SourceInvalid(f"序号不是合法整数：{n_txt!r}", file=file, line_no=i) from None

        if station_m < 0:
            raise SourceInvalid(f"桩号为负：{station_m}", file=file, line_no=i)
        if points:
            prev = points[-1]
            if station_m < prev["station_m"]:
                raise SourceInvalid(
                    f"桩号倒退：{prev['station_m']} → {station_m}（桩号序列必须单调不减）",
                    file=file, line_no=i,
                )
            if seq_no <= prev["seq_no"]:
                raise SourceInvalid(
                    f"序号未递增：{prev['seq_no']} → {seq_no}",
                    file=file, line_no=i,
                )
        points.append({"station_m": station_m, "seq_no": seq_no})

    # 单点构不成"序列"——契约⑤ schema 对 station_sequence 亦要求 minItems: 2
    if len(points) < 2:
        raise SourceInvalid(f"有效数据行不足 2 条（实为 {len(points)} 条），构不成桩号序列",
                            file=file)

    return {"vendor_version": version, "points": points}


# parse() 返回值里承载"该段载荷"的键名。适配器分发表据此把结果塞进 IR 的 segments[SEGMENT]。
# ---------------------------------------------------------------- 桩号文本 ↔ 数值
#: 桩号文本正则。小数部分**至少 3 位**：`K0+000` / `K4+635.000` / `K12+345.678`。
#
#  ★ 为什么不用宽松的 `K(\d+)\+(\d+(?:\.\d+)?)`：
#    宽松版会**接受** `K0+0`、`K5+5` 这类缩写，而它们在图纸上不是合法桩号 ——
#    真实桩号的小数部分恒为 3 位（`%07.3f`）。放它们进来，下游拿到的字符串
#    就和纬地导出的一模一样不了，往返比对会「一致」而实际不同源。
STATION_TEXT_RE = re.compile(r"^K(\d+)\+(\d{3}(?:\.\d+)?)$")


def station_to_text(station_m: float, *, places: int = 3) -> str:
    """米 → 桩号文本（``545.874`` → ``K0+545.874``）。**与 design_import.station_text 孪生**。

    ★ 为什么这里再写一份而不 import 那个：
      `design_import` 依赖本模块（适配器 → IR → plan），反向 import 会成环。
      所以**约定由测试钉住**：`tests/contract/test_station_text.py` 里有一条
      逐值比对两个实现，漂了就红。这不是重复，是**带校验的重复**。

    ``places`` 只允许 3 或 6 —— 3 位是作业口径（`.STA` 文件、图纸），
    6 位是数据库口径（`station_local_km numeric(...,6)` 折回来的米）。
    """
    if places not in (3, 6):
        raise ValueError(f"places 只允许 3 或 6，实为 {places}")
    km, m = divmod(round(station_m, places), 1000.0)
    # 宽度 = places + 4：3 → 7（`000.000`）、6 → 10（`000.000000`）。
    # 写成固定 7 会让 6 位口径少三位，且**肉眼看不出**（`000.000` 看着也对）。
    return f"K{int(km)}+{m:0{places + 4}.{places}f}"


def text_to_station(text: str, *, file: str | None = None,
                    line_no: int | None = None) -> float:
    """桩号文本 → 米。不合法则抛 `SourceInvalid` 并**指出行号**（FR-009）。

    ★ FR-009 明文要求「不一致时导入失败并**指出具体行**」—— 所以这个函数
      接受 `file` / `line_no` 并一路带给 `SourceInvalid`。没有行号的报错
      在 333 行的文件里等于没说。
    """
    s = (text or "").strip()
    m = STATION_TEXT_RE.match(s)
    if not m:
        raise SourceInvalid(
            f"桩号文本不合法：{s[:32]!r}"
            f"（期望形如 K0+545.874 —— K、整数公里、「+」、至少 3 位小数部分）",
            file=file, line_no=line_no)
    km, m_part = m.group(1), m.group(2)
    return int(km) * 1000.0 + float(m_part)


def roundtrip(point: Mapping[str, Any], *, file: str | None = None,
              line_no: int | None = None, tol_m: float = 5e-4) -> str:
    """对一条桩号记录做**双向**还原校验，返回规整后的文本。不一致即抛错。

    两个方向都验，缺一不可：
      ① 数值 → 文本 → 数值：抓「数值本身写坏了」
      ② 文件里的文本 → 数值 → 文本：抓「文本与数值不是同一个桩号」

    ★ `tol_m` 默认 5e-4 而不是 0：
      桩号在库里是毫米级（`round(x, 3)`）。文本写 3 位小数，折回米以后与原始
      浮点值可能差不到 0.5 mm —— 那不是错误，是**表示精度**。写成 0 会让每一条
      都报错，然后有人把容差调大到 0.5 m，把真的错误也放过。
      5e-4 m = 0.5 mm = 桩号精度的一半，正好卡在「表示误差」与「真错」之间。
    """
    v = point.get("station_m")
    if v is None:
        raise SourceInvalid("桩号记录缺 station_m 字段", file=file, line_no=line_no)
    val = float(v)

    # ① 数值 → 文本 → 数值
    txt = station_to_text(val)
    back = text_to_station(txt, file=file, line_no=line_no)
    if abs(back - val) > tol_m:
        raise SourceInvalid(
            f"桩号往返不一致：数值 {val} → 文本 {txt} → 数值 {back}"
            f"（差 {back - val:+.6f} m，超容差 {tol_m} m）",
            file=file, line_no=line_no)

    # ② 文件自带文本（若有）→ 数值 → 文本
    src_txt = point.get("station_text_source")
    if src_txt:
        n2 = text_to_station(src_txt, file=file, line_no=line_no)
        if abs(n2 - val) > tol_m:
            raise SourceInvalid(
                f"文件里的桩号文本 {src_txt!r} 与数值 {val} 不是同一个桩号"
                f"（文本折回 {n2}，差 {n2 - val:+.6f} m）",
                file=file, line_no=line_no)
        back_txt = station_to_text(n2)
        if back_txt != src_txt:
            raise SourceInvalid(
                f"桩号文本不能原样还原：文件写 {src_txt!r}，还原得 {back_txt!r}",
                file=file, line_no=line_no)
    return txt


def station_type_of(station_m: float, *, first: float, last: float,
                    step_m: float = 20.0) -> str:
    """桩号类型：`endpoint` 起终点 / `integer` 整桩 / `jiazi` 加桩（FR-011）。

    ★ FR-011 明文：**加桩 MUST NOT 被规整**。
      实测本工程 332 个桩号 = 整桩 290 + 加桩 40 + 端点 2。那 40 个加桩
      **全部紧跟一个整桩**（曲线特征点），是真实里程，不是「应该四舍五入到
      20 m 的毛刺」。任何把它们规整回整桩的做法，都会让线形特征点对不上
      曲线要素 —— 而且**不会报错**。
    """
    if abs(station_m - first) < 1e-9 or abs(station_m - last) < 1e-9:
        return "endpoint"
    r = station_m % step_m
    return "integer" if (r < 1e-9 or step_m - r < 1e-9) else "jiazi"


PAYLOAD_KEY = "points"

__all__ = ["detect", "parse", "MAGIC_RE", "SEGMENT", "FILE_KIND", "PAYLOAD_KEY"]
