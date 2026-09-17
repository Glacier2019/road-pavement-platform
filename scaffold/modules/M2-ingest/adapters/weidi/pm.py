"""纬地 HintCAD `.pm` 平面线形文件解析器 → `alignment_element`（契约⑤ 第三个适配器）。

文件结构（实测自 052201341 毕设工程，105 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD5.83_PM_SHUJU_PM
    第 2 行   `%10d\\t%6s\\t%6s\\t%10.6f`   ← 单元数 = 33、NULL、NULL、起点桩号 = 0.000000
    第 3 行   `%12d\\t%10.4f\\t%12.12f\\t%12.12f\\t%14.8f\\t%14.8f`
                                          ← 起点记录：1 / 0.0000 / 起始方位角(rad) / ? / X / Y
    第 4 行起 **每 3 行一个线形单元，扁平排列，没有块套嵌**（这是它比 .JD 好解析的地方）：
        (7) `%12d \\t %10.6f \\t %14.8f \\t %12.8f \\t %12.8f \\t %12.8f \\t %10d`
             转向 | 0.000000 | 单元长/Ls/弧长 | 缓和曲线参数 A | R1 | R2 | 类型码
        (8) 4 组 X/Y：起点 · ? · 终点 · **圆心**（直线或切线端为 0）
        (4) 起点桩号 | 终点桩号 | 起始方位角(rad) | 终止方位角(rad)
    末尾      `0` + 6 字段收尾记录 + 空行

字段语义**不是猜的**，三条不变量在真文件上量化验证（`test_design_import.py` 第 9 组）：
  ① **终点 ≡ 下一单元起点**：32/32 组坐标**逐位相等**（<1e-6）⇒ (8) 的 [0] 是起点、[2] 是终点；
  ② **第 4 点到终点的距离 ≡ R**：450.00 / 260.00 / 500.00 / 255.00 … 全部精确等于该单元的 R
     ⇒ 第 4 点是**圆心**；到「起点」的距离在缓和曲线上略大于 R（起点在切线上），亦符合几何；
  ③ **弦长 = 2R·sin(弧长/2R)**：如 #3 `900·sin(112.809/900) = 112.514` ＝ 实测 `112.514`；
     直线单元弦长 ≡ 单元长（差值 0.000）。
  另外第 0 列（±1）＝**该单元所属交点的转向符号**，与 `.JD` 由坐标独立算出的转角符号一致
  —— 这条跨文件印证放在 `build_ir` 里做（需要同时拿到两段）。

✅ 与 .JD 相反，本表在 DDL 里的列注释**经核对全部属实**：
  `element_seq ... -- 线形单元序号（.PM 共 33 段）` → 实测正是 33 个单元；
  `element_type ... -- line 直线 / circular 圆曲线 / transition 缓和曲线` → 与本模块
  `CODE_TYPE` 产出的三个字符串逐字相同。
  （`alignment_pi` 那两条注释是错的，工单 #3 已修正并顺势把该表改判为派生表；
    见 `jd.py` 文档头与契约⑤ README「3.」节。）
  ★ 更关键的一点（工单 #3）：本表是平面几何的**真源表** —— `alignment_pi` 的
    全部字段都能由本表推出的单元链算回来（见 `adapters/geom.py`，偏差 3×10⁻⁸ m）。
    所以 `.JD` 不是数据入口，是**验算**。
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..errors import SourceInvalid

MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_PM_SHUJU_PM$")

SEGMENT = "alignment_element"
FILE_KIND = "平面线形文件"
PAYLOAD_KEY = "elements"

# 类型码 → 单元类型。实测：1 直线 / 21 缓→圆 / 3 圆 / 22 圆→缓
CODE_TYPE: dict[str, str] = {"1": "line", "21": "transition", "3": "circular", "22": "transition"}

INF = 9999.0                 # 半径字段的"无穷大"哨兵（直线 R1=R2=9999）
_CHAIN_TOL = 1e-6            # 链连续性容差：实测为逐位相等，故取得很紧


def detect(text: str) -> bool:
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def _need_len(fields: list[str], want: int, *, file: str | None, line_no: int, what: str) -> None:
    if len(fields) != want:
        raise SourceInvalid(f"{what}字段数应为 {want}，实为 {len(fields)}", file=file, line_no=line_no)


def _num(s: str, *, file: str | None, line_no: int, what: str) -> float:
    try:
        return float(s.strip())
    except ValueError:
        raise SourceInvalid(f"{what}不是合法数字：{s.strip()!r}", file=file, line_no=line_no) from None


def _radius(v: float) -> float | None:
    """半径哨兵 9999 表示无穷大（直线），返回 None 而不是 9999——否则 DAO 会算出 κ=0.0001。"""
    return None if v >= INF else v


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.pm` → ``{"vendor_version","declared_count","start_station_m","elements":[...]}``。

    **宁可拒绝，不要猜**：链不连续（单元 k 的终点 ≠ 单元 k+1 的起点）一律拒绝——
    那说明块错位了，而错位的线形单元会算出一条不存在的路。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines:
        raise SourceInvalid("空文件", file=file)

    m = MAGIC_RE.match(lines[0].lstrip("\ufeff").strip())
    if not m:
        raise SourceInvalid(f"魔数不匹配（期望 HINTCAD<版本>_PM_SHUJU_PM），实为 {lines[0][:40]!r}",
                            file=file, line_no=1)
    version = m.group(1)

    if len(lines) < 3:
        raise SourceInvalid("行数不足（至少需要魔数 + 计数行 + 起点行）", file=file)
    _need_len(lines[1].split("\t"), 4, file=file, line_no=2, what="计数行")
    declared = int(_num(lines[1].split("\t")[0], file=file, line_no=2, what="单元数"))
    head_station = _num(lines[1].split("\t")[3], file=file, line_no=2, what="起点桩号")

    _need_len(lines[2].split("\t"), 6, file=file, line_no=3, what="起点记录")

    body, ln = lines[3:], 4
    elements: list[dict[str, Any]] = []
    i = 0
    # 由**文件头声明的单元数**驱动，正好解析 declared 个，每个都必须是严格的 7/8/4。
    # 不用"形状不对就当作收尾"那种写法——那会让一个被写坏的单元被静默当成文件尾，
    # 于是少一个线形单元却一切正常。宁可在这里报错。
    while len(elements) < declared:
        if i + 2 >= len(body):
            raise SourceInvalid(f"单元数不足：文件头声明 {declared}，只够解析 {len(elements)} 个",
                                file=file, line_no=ln)
        if not body[i].strip():
            # 提前撞上收尾空行 —— 明确报"单元数不足"，别让它退化成"字段数不符"那种
            # 碰巧也对、但说不清病因的报错。
            raise SourceInvalid(f"单元数不足：文件头声明 {declared}，只解析出 {len(elements)} 个"
                                f"（第 {ln} 行已是收尾）", file=file, line_no=ln)
        h, c, s = body[i].split("\t"), body[i + 1].split("\t"), body[i + 2].split("\t")
        _need_len(h, 7, file=file, line_no=ln, what="单元头")
        _need_len(c, 8, file=file, line_no=ln + 1, what="单元坐标")
        _need_len(s, 4, file=file, line_no=ln + 2, what="单元桩号/方位角")

        code = h[6].strip()
        if code not in CODE_TYPE:
            raise SourceInvalid(f"未知的类型码 {code!r}（已知 {'/'.join(CODE_TYPE)}）",
                                file=file, line_no=ln)
        pts = [(_num(c[j], file=file, line_no=ln + 1, what="坐标 X"),
                _num(c[j + 1], file=file, line_no=ln + 1, what="坐标 Y")) for j in range(0, 8, 2)]
        center = None if pts[3] == (0.0, 0.0) else {"x": pts[3][0], "y": pts[3][1]}

        elements.append({
            "seq": len(elements) + 1,
            "type": CODE_TYPE[code],
            "code": int(code),
            "turn_flag": int(_num(h[0], file=file, line_no=ln, what="转向符号")),
            "start_station_m": _num(s[0], file=file, line_no=ln + 2, what="起点桩号"),
            "end_station_m": _num(s[1], file=file, line_no=ln + 2, what="终点桩号"),
            "length_m": abs(_num(h[2], file=file, line_no=ln, what="单元长")),
            "start_x": pts[0][0], "start_y": pts[0][1],
            # ⚠ 第 2 组坐标的用途**未定论**，故不叫 mid 只叫 aux。
            # 第一版我把它当"中点"写进 docstring，那是**没验证的推断**，后来被推翻：
            #  · 直线单元：它确实是中点（QD→它 = 243.0，单元长 485.874，正好一半）✓
            #  · 曲线单元：它到圆心的距离是 453.5，而该圆 R=450 —— 在圆**外**，
            #    所以它不是弧中点（弧中点必在圆上）。它到单元起止点等距（各 56.4），
            #    是弦中垂线上的一点，但比弦中点更靠外。
            # 没有第二条独立证据前，不猜它是什么，也不让下游以为它可用。
            "aux_x": pts[1][0], "aux_y": pts[1][1],
            "end_x": pts[2][0], "end_y": pts[2][1],
            "center": center,
            "azimuth_deg": round(math.degrees(_num(s[2], file=file, line_no=ln + 2, what="起始方位角")), 9),
            "end_azimuth_deg": round(math.degrees(_num(s[3], file=file, line_no=ln + 2, what="终止方位角")), 9),
            "radius_start_m": _radius(_num(h[4], file=file, line_no=ln, what="R1")),
            "radius_end_m": _radius(_num(h[5], file=file, line_no=ln, what="R2")),
            "spiral_a": (lambda a: None if a == 0.0 else a)(
                _num(h[3], file=file, line_no=ln, what="缓和曲线参数 A")),
        })
        i += 3
        ln += 3

    if len(elements) != declared:
        raise SourceInvalid(f"单元数不符：文件头声明 {declared}，实际解析出 {len(elements)}", file=file)
    if len(elements) < 3:
        raise SourceInvalid(f"线形单元不足 3 个（实为 {len(elements)}），构不成线形", file=file)

    # 收尾：解析完 declared 个单元后，剩余非空行至多一条 `0` 收尾记录。
    # 多出来就说明**单元数被少声明了**——那会悄悄丢掉路尾，必须拒绝。
    rest = [x for x in body[i:] if x.strip()]
    if len(rest) > 1:
        raise SourceInvalid(f"解析完 {declared} 个单元后仍有 {len(rest)} 行非空残余，"
                            f"疑似单元数被少声明", file=file, line_no=ln)
    if rest and rest[0].split("\t")[0].strip() != "0":
        raise SourceInvalid(f"收尾记录首字段应为 0，实为 {rest[0][:30]!r}", file=file, line_no=ln)

    if elements[0]["start_station_m"] != head_station:
        raise SourceInvalid(f"首个单元起点桩号 {elements[0]['start_station_m']} "
                            f"与文件头 {head_station} 不符", file=file)

    # 链连续性 —— 本解析器最硬的一条检查。实测真文件 32/32 组逐位相等，
    # 所以容差取 1e-6：不连续就说明块错位，宁可拒绝也不要一条错位的路。
    for a, b in zip(elements, elements[1:]):
        if abs(b["start_station_m"] - a["end_station_m"]) > 1e-9:
            raise SourceInvalid(f"桩号链断裂：单元 {a['seq']} 止于 {a['end_station_m']}，"
                                f"单元 {b['seq']} 起于 {b['start_station_m']}", file=file)
        if abs(b["azimuth_deg"] - a["end_azimuth_deg"]) > 1e-6:
            raise SourceInvalid(f"方位角链断裂：单元 {a['seq']} 止于 {a['end_azimuth_deg']}°，"
                                f"单元 {b['seq']} 起于 {b['azimuth_deg']}°", file=file)
        gap = math.dist((a["end_x"], a["end_y"]), (b["start_x"], b["start_y"]))
        if gap > _CHAIN_TOL:
            raise SourceInvalid(f"坐标链断裂：单元 {a['seq']} 终点与单元 {b['seq']} 起点"
                                f"相距 {gap:.6f} m", file=file)

    return {"vendor_version": version, "declared_count": declared,
            "start_station_m": head_station, "elements": elements}


__all__ = ["detect", "parse", "MAGIC_RE", "SEGMENT", "FILE_KIND", "PAYLOAD_KEY", "CODE_TYPE"]
