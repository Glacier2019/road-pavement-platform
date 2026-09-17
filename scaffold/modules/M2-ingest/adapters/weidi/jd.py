"""纬地 HintCAD `.JD` 平面交点文件解析器 → `alignment_pi`（契约⑤ 第二个适配器）。

文件结构（实测自 052201341 毕设工程，153 行）
-------------------------------------------------------------------------------
    第 1 行   HINTCAD5.83_PM_SHUJU_JD        ← 魔数（注意：.STA 是 5.84，本文件是 5.83）
    第 2 行   `%10d\\t%10.4f`                  ← 控制点数 = 10，起点桩号 = 0.0000
    随后按空行分块，每块描述一个控制点：
        起点 QD 块：  (5)(12)(10) 三行
        其余块    ：  (7)(8)(4) ×3  ＋ (5)(12)(10)      ← 前一交点的 3 个线形单元 + 本控制点
    字段以 TAB 分隔，行尾 CRLF。

字段语义（**不是猜的，见下方验证**）
-------------------------------------------------------------------------------
    5 字段行  ： 交点号 | 标识(QD/1..8/ZD) | 类型码 | X | Y
    12 字段行 ： A1 | Ls1 | 9999 | T1 | R | 圆弧长 | 总曲线长 | A2 | Ls2 | 9999 | T2 | 外距
    10 字段行 ： 本点桩号 | κ? | κ? | 本点桩号 | 特征点桩号 ×6

**为什么这组语义可信** —— 三重独立验证（`test_design_import.py` 第 8 组逐条断言）：
  ① 转角**由坐标独立算出**（相邻方位角之差），不读文件里的任何角度字段；
  ② 用该转角算圆弧长 `R·(α−2β)`（β=Ls/2R）= 112.809 ＝ 文件里的 `112.80867934`；
  ③ 用该转角算外距 `(R+ΔR)/cos(α/2)−R` = 8.765 ＝ 文件里的 `8.76412020`；
  ④ `√(R·Ls)` = `√(450×60)` = **164.3167672515** ＝ 文件里的 `164.31676725`。
  四项各自独立地指向同一组数值，因此这些字段的归属是**被证明的**，不是被猜的。

⚠ 由此发现 DDL v0.3 里 `alignment_pi` 的两条列注释标错了值（工单 #3 已修正）——
    `tangent_len_m ... -- 切线长（164.31676725）`：该值是 **缓和曲线参数 A = √(R·Ls)**，
        真正的切线长是 12 字段的 `T1 = 117.54242652`（＝ 交点桩号 − ZH 桩号 = 603.416−485.874）。
    `deflection_deg ... -- 转角（-60.0）`：该值是 **缓和曲线长 Ls = 60 m**，
        真正的转角是 **+22.0027°**（由坐标算出）。

  教训（比错误本身重要）：这两个错值在 DDL 和《数据字典》里**一字不差地各出现一次**，
  看起来是"两处互相印证"，实际**同源**——字典抄的 DDL。所以谁也没发现。
  真正把它们揪出来的是**几何恒等式**（上面四条），不是"交叉核对文档"。
  同理，A 本该是导出量却存成了手填列：一个"设计上就该有值"的列，没人会去质疑它的值。

  工单 #3 的处理：`alignment_pi` 整体改判为**派生表**（可由单元链推出，偏差 3×10⁻⁸ m），
  导入器只从单元链推导（见 `adapters/geom.py`），`.JD` 降为**验算**；
  `spiral_a1/a2` 改为 GENERATED 列，结构上不可能与 R、Ls 不一致。
"""
from __future__ import annotations

import math
import re
from typing import Any

from ..errors import SourceInvalid

MAGIC_RE = re.compile(r"^HINTCAD([0-9][0-9.]*)_PM_SHUJU_JD$")

SEGMENT = "alignment_pi"
FILE_KIND = "平面交点文件"

# 纬度方向的地球曲率修正？不需要——本项目坐标是**高斯投影平面坐标**（X 北 / Y 东），
# 方位角直接由 atan2(ΔY, ΔX) 得出，与纬地内部一致（已用 4 项几何量交叉验证）。


def detect(text: str) -> bool:
    first = text.splitlines()[0].strip() if text.splitlines() else ""
    return bool(MAGIC_RE.match(first))


def _num(s: str, *, file: str | None, line_no: int, what: str) -> float:
    try:
        return float(s.strip())
    except ValueError:
        raise SourceInvalid(f"{what} 不是合法数字：{s.strip()!r}", file=file, line_no=line_no) from None


def parse(text: str, *, file: str | None = None) -> dict[str, Any]:
    """解析 `.JD` → ``{"vendor_version","declared_count","control_points":[...]}``。

    **宁可拒绝，不要猜**：结构不符（块形状不对、点数不符、桩号倒退）一律抛 SourceInvalid，
    因为 `alignment_pi` 是全线几何控制点的真值，猜错一个交点会静默带偏整条路。
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines:
        raise SourceInvalid("空文件", file=file)

    m = MAGIC_RE.match(lines[0].lstrip("\ufeff").strip())
    if not m:
        raise SourceInvalid(f"魔数不匹配（期望 HINTCAD<版本>_PM_SHUJU_JD），实为 {lines[0][:40]!r}",
                            file=file, line_no=1)
    version = m.group(1)

    if len(lines) < 2:
        raise SourceInvalid("缺少第 2 行（控制点数 + 起点桩号）", file=file, line_no=2)
    head = lines[1].split("\t")
    if len(head) != 2:
        raise SourceInvalid(f"第 2 行应为 2 字段，实为 {len(head)}", file=file, line_no=2)
    declared = int(_num(head[0], file=file, line_no=2, what="控制点数"))

    # 每个控制点由「5 字段行」标识；其 +1 行是 12 字段、+2 行是 10 字段
    cps: list[dict[str, Any]] = []
    for i, raw in enumerate(lines):
        f = raw.split("\t")
        if len(f) != 5 or not raw.strip():
            continue
        ln = i + 1
        if i + 2 >= len(lines):
            raise SourceInvalid("5 字段行之后不足 12/10 字段行", file=file, line_no=ln)
        f12, f10 = lines[i + 1].split("\t"), lines[i + 2].split("\t")
        if len(f12) != 12 or len(f10) != 10:
            raise SourceInvalid(
                f"控制点记录形状不符（应为 5+12+10，实为 5+{len(f12)}+{len(f10)}）",
                file=file, line_no=ln)

        tag = f[1].strip()
        cps.append({
            "seq": len(cps),
            "tag": tag,
            # 归一化类别：DDL 的 alignment_pi.pi_type 就是这一列。
            # tag 是**厂商给的标签**（QD / 1…8 / ZD），pi_type 是**平台要的类**。
            # 少了它，交点表那一列没人填——测试第 10 组正是这么把它揪出来的。
            "pi_type": "QD" if tag == "QD" else ("ZD" if tag == "ZD" else "JD"),
            "type_code": int(_num(f[2], file=file, line_no=ln, what="类型码")) if f[2].strip().isdigit() else None,
            "x": _num(f[3], file=file, line_no=ln, what="X 坐标"),
            "y": _num(f[4], file=file, line_no=ln, what="Y 坐标"),
            "station_m": _num(f10[0], file=file, line_no=ln + 2, what="本点桩号"),
            # ⚠ 这 6 个值**不是 6 个桩号**。实测排布（8 个交点逐一核对）为
            #   [前段直线长, ZH, HY, 曲中点, YH, HZ]
            # 第 0 个是**长度**不是桩号：PI2 的 674.493 ≠ 它的 ZH 1393.176，
            # 而是它前面那段直线（718.682→1393.176）的长度。PI1 恰好相等（485.874）
            # 只是因为全线起点桩号是 0，纯属巧合——差点因此蒙混过去。
            # 原样保留这 6 个值，但换算成有名有姓的字段，免得下游当成同一类量。
            "prev_tangent_len_m": _num(f10[4], file=file, line_no=ln + 2, what="前段直线长"),
            "feat_stations_m": [_num(v, file=file, line_no=ln + 2, what="特征点桩号") for v in f10[5:10]],
            "spiral_a1": _num(f12[0], file=file, line_no=ln + 1, what="A1"),
            "spiral_ls1": _num(f12[1], file=file, line_no=ln + 1, what="Ls1"),
            "tangent_len_m": _num(f12[3], file=file, line_no=ln + 1, what="T1"),
            "radius_m": _num(f12[4], file=file, line_no=ln + 1, what="R"),
            "arc_len_m": _num(f12[5], file=file, line_no=ln + 1, what="圆弧长"),
            "curve_len_m": _num(f12[6], file=file, line_no=ln + 1, what="总曲线长"),
            "spiral_a2": _num(f12[7], file=file, line_no=ln + 1, what="A2"),
            "spiral_ls2": _num(f12[8], file=file, line_no=ln + 1, what="Ls2"),
            "tangent_len2_m": _num(f12[10], file=file, line_no=ln + 1, what="T2"),
            "external_m": _num(f12[11], file=file, line_no=ln + 1, what="外距"),
        })

    if len(cps) != declared:
        raise SourceInvalid(f"控制点数不符：文件头声明 {declared}，实际解析出 {len(cps)}",
                            file=file)
    if len(cps) < 3:
        raise SourceInvalid(f"控制点不足 3 个（实为 {len(cps)}），构不成线形", file=file)

    # 桩号必须单调不减；起终点须为 0 与全线里程
    for a, b in zip(cps, cps[1:]):
        if b["station_m"] < a["station_m"]:
            raise SourceInvalid(f"控制点桩号倒退：{a['tag']} {a['station_m']} → "
                                f"{b['tag']} {b['station_m']}", file=file)
    if cps[0]["station_m"] != 0.0:
        raise SourceInvalid(f"起点桩号应为 0，实为 {cps[0]['station_m']}", file=file)

    # 转角：**由相邻控制点坐标独立算出**，不读文件里的角度字段
    #   约定：X 为北、Y 为东（高斯平面），方位角 = atan2(ΔY, ΔX)，顺时针为正。
    #        转角 = 后一段方位角 − 前一段方位角，归一化到 (−180, 180]。
    for k, cp in enumerate(cps):
        if k == 0 or k == len(cps) - 1:
            cp["azimuth_deg"] = None
            cp["deflection_deg"] = None
            continue
        px, py = cps[k - 1]["x"], cps[k - 1]["y"]
        nx, ny = cps[k + 1]["x"], cps[k + 1]["y"]
        az_in = math.degrees(math.atan2(cp["y"] - py, cp["x"] - px))
        az_out = math.degrees(math.atan2(ny - cp["y"], nx - cp["x"]))
        cp["azimuth_deg"] = round(az_in % 360, 6)
        cp["deflection_deg"] = round((az_out - az_in + 180) % 360 - 180, 6)

    return {"vendor_version": version, "declared_count": declared, "control_points": cps}


# parse() 返回值里承载"该段载荷"的键名，供适配器分发表使用。
PAYLOAD_KEY = "control_points"


__all__ = ["detect", "parse", "MAGIC_RE", "SEGMENT", "FILE_KIND", "PAYLOAD_KEY"]
