"""纬地 HintCAD `.ZDM` 纵断面设计文件解析器（契约⑤ 第 5 个适配器）。

文件格式（实测自 052201341 毕设工程，14 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD5.83_ZDM_SHUJU          ← 魔数，含厂商版本号
    第 2 行   `%12d`                          ← **变坡点个数**
    第 3 行起 `%12.3f\t%14.8f\t%14.8f\t%10.3f\t%10.8f`
              桩号(米)  设计高程(米)  竖曲线半径(米)  ?           ?
    行尾 CRLF

实测：计数行 = 12，数据行 = 12，0.000 → 5805.421 米。

这是一个可以直接**自我对账**的文件：计数行是它自己的声明，
数据行是它的实际内容，两者必须相等。`.STA`/`.DMX` 都没有这一行，
只能靠外部对账；这个有，就别浪费。

第 4、5 个字段含义未知
-------------------------------------------------------------------------------
实测 12 行里这两列**全部为 0**（`0.000` 与 `0.00000000`）。样本里全是 0 的列
既证明不了含义，也证伪不了含义 —— 所以本解析器**不猜**：把它们按原名
`field4_raw`/`field5_raw` 收进 IR，并在出现非 0 值时**告警**（不是拒绝）。
理由是：全 0 时它们是"没信息"，非 0 时是"有信息但含义未知" ——
后者若被静默丢弃，就是丢了一份我们看不懂但确实存在的设计数据。

竖曲线几何是**派生**的
-------------------------------------------------------------------------------
文件只给三样：变坡点桩号、设计高程、竖曲线半径。纵坡与竖曲线长度都不给，
需要推：

    grade_in_pct   = (E_i − E_{i−1}) / (S_i − S_{i−1}) × 100     ← 进入该变坡点的坡
    grade_out_pct  = (E_{i+1} − E_i) / (S_{i+1} − S_i) × 100     ← 离开该变坡点的坡
    grade_len_m    = R × |grade_out − grade_in| / 100            ← 竖曲线全长 L = R·|i₂−i₁|

与交点的 T/α/E 同一类：都是"能由输入唯一确定、但源文件不写"的量。
首尾变坡点只有一个邻坡，故 grade_in/out 为 NULL —— 不是 0（0 是个坡度的值，
NULL 才是"没有这个坡"）。R = 0 时 L = 0：确实没有竖曲线，这是真值不是缺值。
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..errors import SourceInvalid

# HINTCAD5.83_ZDM_SHUJU —— 版本号捕获出来存进 IR 的 source.vendor_version
MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_ZDM_SHUJU$")

SEGMENT = "profile_grade_point"
FILE_KIND = "纵断面设计文件"

#: 设计高程合理性区间（米）。与 .DMX 同一套界限，理由也相同：
#: 抓的是**量级错位**（桩号串进高程列），抓不了"像高程其实是别的数"。
ELEV_MIN_M = -500.0
ELEV_MAX_M = 9000.0

#: 竖曲线半径上限（米）。公路竖曲线半径实测在 10³–10⁵ 量级；
#: 取 10⁷ 是"大得不合理"，用来抓列错位。
RADIUS_MAX_M = 1.0e7

#: 数据行的字段数。多一列少一列都说明格式与预期不符，宁可拒绝也不猜列义。
FIELD_COUNT = 5


def detect(text: str) -> bool:
    """格式探测：这个文件像不像纬地 `.ZDM`？只认魔数，不靠扩展名。"""
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.ZDM` → ``{"vendor_version": "5.83", "points": [...]}``

    ``points`` 每项：``{vpi_seq, station_m, elevation_m, vertical_curve_radius_m,
    field4_raw, field5_raw}``（后两个是含义未知的原始列，见模块 docstring）。

    校验策略与 `.STA`/`.DMX` 一致：**宁可拒绝，不要猜。**
    设计高程直接决定填挖方与路面结构厚度，读错一列不会报错，只会让整条路的设计
    反着来。所以每种畸形都抛 `SourceInvalid`。
    """
    if not text.strip():
        raise SourceInvalid("空文件", file=file)
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    first = lines[0].lstrip("\ufeff").strip()
    m = MAGIC_RE.match(first)
    if not m:
        raise SourceInvalid(
            f"魔数不匹配：期望形如 `HINTCAD<版本>_ZDM_SHUJU`，实为 {first[:40]!r}",
            file=file, line_no=1,
        )
    version = m.group(1)

    if len(lines) < 2 or not lines[1].strip():
        raise SourceInvalid("缺少计数行（第 2 行应为变坡点个数）", file=file, line_no=2)
    try:
        declared = int(lines[1].strip())
    except ValueError:
        raise SourceInvalid(f"计数行不是整数：{lines[1].strip()[:30]!r}",
                            file=file, line_no=2) from None
    if declared < 0:
        raise SourceInvalid(f"计数行为负：{declared}", file=file, line_no=2)

    points: list[dict[str, Any]] = []
    for i, raw in enumerate(lines[2:], start=3):
        # 末尾空行放行（导出工具常留一行），中间空行意味着漏读一段设计线。
        # 注意：空行不计入"数据行数"，所以下面的计数核对不受末尾空行影响。
        if raw.strip() == "":
            if all(l.strip() == "" for l in lines[i:]):
                break
            raise SourceInvalid("文件中间出现空行（疑似漏读一段设计线）", file=file, line_no=i)

        parts = raw.split("\t") if "\t" in raw else raw.split()
        if len(parts) != FIELD_COUNT:
            raise SourceInvalid(
                f"字段数应为 {FIELD_COUNT}（桩号/设计高程/竖曲线半径/两列未知），"
                f"实为 {len(parts)}：{raw.strip()[:40]!r}",
                file=file, line_no=i,
            )

        def _f(idx: int, what: str) -> float:
            txt = parts[idx].strip()
            try:
                v = float(txt)
            except ValueError:
                raise SourceInvalid(f"{what}不是合法数字：{txt!r}",
                                    file=file, line_no=i) from None
            if not math.isfinite(v):
                raise SourceInvalid(f"{what}不是有限数：{v}", file=file, line_no=i)
            return v

        station_m = _f(0, "桩号")
        elevation_m = _f(1, "设计高程")
        radius_m = _f(2, "竖曲线半径")
        f4 = _f(3, "第 4 列（含义未知）")
        f5 = _f(4, "第 5 列（含义未知）")

        if station_m < 0:
            raise SourceInvalid(f"桩号为负：{station_m}", file=file, line_no=i)
        if not (ELEV_MIN_M <= elevation_m <= ELEV_MAX_M):
            raise SourceInvalid(
                f"设计高程 {elevation_m} m 超出合理区间 [{ELEV_MIN_M}, {ELEV_MAX_M}]"
                f"（疑似列错位）", file=file, line_no=i)
        if radius_m < 0:
            raise SourceInvalid(f"竖曲线半径为负：{radius_m}", file=file, line_no=i)
        if radius_m > RADIUS_MAX_M:
            raise SourceInvalid(
                f"竖曲线半径 {radius_m} m 超出合理区间 [0, {RADIUS_MAX_M:g}]"
                f"（疑似列错位）", file=file, line_no=i)
        if points and station_m <= points[-1]["station_m"]:
            # 变坡点必须**严格**递增：两个同桩号的变坡点在几何上无法解释
            raise SourceInvalid(
                f"桩号未严格递增：{points[-1]['station_m']} → {station_m}"
                f"（同桩号的两个变坡点无法解释）", file=file, line_no=i)

        points.append({
            "vpi_seq": len(points) + 1,
            "station_m": station_m,
            "elevation_m": elevation_m,
            "vertical_curve_radius_m": radius_m,
            "field4_raw": f4,
            "field5_raw": f5,
        })

    # 自带计数行是一件礼物：拿它对自己的内容，不对外部文件。
    if declared != len(points):
        raise SourceInvalid(
            f"计数行声明 {declared} 个变坡点，实际读到 {len(points)} 个"
            f"（文件自我矛盾，说明漏读或多读）", file=file)
    if len(points) < 2:
        raise SourceInvalid(f"变坡点不足 2 个（实为 {len(points)} 个），构不成纵断面",
                            file=file)

    return {"vendor_version": version, "points": points}


def derive_grades(points: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    """就地补出 ``grade_in_pct`` / ``grade_out_pct`` / ``grade_len_m``，返回 (points, 告警)。

    这些都是**派生量**（源文件不写，但由输入唯一确定），与交点的 T/α/E 同类。
    边界语义：
      · 首个变坡点没有"进入"的坡 → ``grade_in_pct = None``；末个反之。
      · ``R = 0`` 表示**确实没有竖曲线** → ``grade_len_m = 0.0``（真值，非缺值）。
      · 若 R > 0 却缺一个邻坡 → ``grade_len_m = None`` 并告警（不能算，也不假装是 0）。
    """
    warn: list[str] = []
    for i, p in enumerate(points):
        p["grade_in_pct"] = None
        p["grade_out_pct"] = None
        p["grade_len_m"] = None
        if i > 0:
            ds = p["station_m"] - points[i - 1]["station_m"]
            if ds <= 0:                       # parse 已挡住，这里是纵深防御
                warn.append(f"变坡点 {p['vpi_seq']} 与前点桩号未递增（{ds} m），坡度为 None")
            else:
                p["grade_in_pct"] = (p["elevation_m"] - points[i - 1]["elevation_m"]) / ds * 100.0
        if i < len(points) - 1:
            ds = points[i + 1]["station_m"] - p["station_m"]
            if ds <= 0:
                warn.append(f"变坡点 {p['vpi_seq']} 与后点桩号未递增（{ds} m），坡度为 None")
            else:
                p["grade_out_pct"] = (points[i + 1]["elevation_m"] - p["elevation_m"]) / ds * 100.0

        r = p["vertical_curve_radius_m"]
        if r == 0:
            p["grade_len_m"] = 0.0            # 没有竖曲线，长度确实是 0
        elif p["grade_in_pct"] is None or p["grade_out_pct"] is None:
            warn.append(f"变坡点 {p['vpi_seq']} 半径 R={r} 但缺一侧纵坡，竖曲线长无法推得 → None")
        else:
            p["grade_len_m"] = r * abs(p["grade_out_pct"] - p["grade_in_pct"]) / 100.0

        for col in ("field4_raw", "field5_raw"):
            if p[col]:
                warn.append(f"变坡点 {p['vpi_seq']} 的 {col} = {p[col]}（样本里该列恒为 0，"
                            f"含义未知，不可静默丢弃，请人工确认）")
    return points, warn


def check_against_stations(points: list[dict[str, Any]],
                           stations: list[dict[str, Any]]) -> list[str]:
    """设计线必须**覆盖整条路**：首尾桩号要与桩号序列一致。返回告警（不抛异常）。

    为什么只查首尾、不查逐条：`.ZDM` 只有十几个变坡点，本来就不该逐桩对齐；
    但设计线起终点与路线起终点必须一致 —— 短了就是设计没做到头，
    长了就是设计越出了路线范围，两种都是真问题。
    """
    warn: list[str] = []
    if not points or not stations:
        return warn
    if abs(points[0]["station_m"] - stations[0]["station_m"]) > 1e-6:
        warn.append(f"设计线起点 {points[0]['station_m']} m 与桩号序列起点 "
                    f"{stations[0]['station_m']} m 不一致")
    if abs(points[-1]["station_m"] - stations[-1]["station_m"]) > 1e-6:
        warn.append(f"设计线终点 {points[-1]['station_m']} m 与桩号序列终点 "
                    f"{stations[-1]['station_m']} m 不一致")
    return warn


PAYLOAD_KEY = "points"

__all__ = ["detect", "parse", "derive_grades", "check_against_stations", "MAGIC_RE",
           "SEGMENT", "FILE_KIND", "PAYLOAD_KEY", "FIELD_COUNT",
           "ELEV_MIN_M", "ELEV_MAX_M", "RADIUS_MAX_M"]
