"""平面线形积分 —— 论文 (2.15) 前两个积分的**唯一**实现处。

    Γ: r(l) = ( x_ms + ∫sin α_l dl*,  y_ms + ∫cos α_l dl*,  z_ms + ∫i_l dl* )

本文件管前两个（x、y）。第三个（z）不在积分 —— 见 `gen_crg.py` 的 `elevation()`，
那是同一个积分的**解析结果**（直线段闭式 + 抛物线竖曲线闭式）。

★ 为什么单独一个文件（2026-10-16）
--------------------------------------------------------------------------
2026-10-16 有人问：「那张直纹面是用数据库数据积分算出来的吗？」
查下去是三件事：

  ① 平面坐标**真的是积出来的** —— 中点法，0.1 m 一步，58 054 步；
  ② `alignment_element` 里**存着**六个坐标列
     （`start_x/start_y/end_x/end_y/center_x/center_y`），
     而 `gen_crg.py` **一列都没读**（搜这些名字零命中）。
     于是同一条路有**两个独立来源**；
  ③ 手工把两者对账：刚体配准后残差最大 **0.471 mm**、均方根 **0.303 mm**
     （66 个点，5.8 km）。形状也对上：积分总弦长 5493.478 m，
     库里首末点距离 5493.478 m。

★★ 但那次对账是**手工的**，下一次没人会对。
   这正是 D-032 的教训换个地方长回来：一份没人跑的检查，和没有检查，
   对后来的人是一样的。

所以积分搬到这里，走 `xsection.py` 同一个套路（数学出模块、`gen_crg.py` 调它、
契约测试直接测它）。门在 `tests/contract/test_alignment_closure.py`：
拿数据库里那套**不是我们算的**坐标当证人，配准后残差必须 < 1 mm。

★ 本文件**不碰数据库、不碰文件、不碰网络** —— 纯数学，离线可跑、可单测。
"""
from __future__ import annotations

import math

#: 积分步长（米）。0.1 m 既是 CRG 的纵横向增量，也是论文口径下的细分步。
#: ★ 实测收敛：同一条路用 0.1 m 与 0.01 m 各积一遍，终点差 0.000046 mm。
DEFAULT_STEP = 0.1


def curvature(el):
    """段内 κ(s)=k0+(k1-k0)s/L 的 ``(k0, k1, L)``。方向由 Δazimuth 给出（半径列无符号）。

    ★ 半径列的约定：`None` 表示**没有这个半径**（数据库里是 NULL）。
      直线段两个都是 None；入口缓和是 `(None, R)`；出口缓和是 `(R, None)`。
      **不要**用 -1 之类的哨兵代替 None —— 那会被当成真半径，
      把缓和曲线算成半径 1 m 的圆。本仓 `alignment_element.csv`
      用的正是 -1 哨兵，读那个文件时必须先映射回 None。
    """
    L = el["s1"] - el["s0"]
    if el["t"] == "line":
        return 0.0, 0.0, L
    R = el["r0"] if el["r0"] is not None else el["r1"]
    sign = 1.0 if el["az1"] > el["az0"] else -1.0
    if el["t"] == "circular":
        return sign / R, sign / R, L
    if el["r0"] is None:      # 入口缓和 ∞ -> R
        return 0.0, sign / R, L
    return sign / R, 0.0, L    # 出口缓和 R -> ∞


def azimuth_at(E, u):
    """按里程查段，返回方位角 az(u)，**弧度**。

    ★ 边界归属：按 ``s0 <= u <= s1`` 取**第一个**命中的段 —— 段边界上归前段。
      这决定了下一段起点的方位角取自哪一段，改不得。
    """
    for el in E:
        if el["s0"] <= u <= el["s1"]:
            k0, k1, LL = curvature(el)
            s = u - el["s0"]
            return math.radians(el["az0"]) + k0 * s + (k1 - k0) * s * s / (2 * LL)
    return math.radians(E[-1]["az1"])


def integrate(E, step=DEFAULT_STEP):
    """沿连续里程用**中点法**推进平面坐标，返回 ``[(u, x, y, az), ...]``。

    起点固定在 (0, 0) —— 得到一条**局部坐标**的线。

    ★ 为什么不锚到数据库的坐标列：两者互不相干。
      一个是我们积的，一个是设计给的。**这正是它能当证人的原因** ——
      一旦锚上去，门就变成自己验自己。局部坐标还顺带避开了
      UTM 大数（x ≈ 2 789 225 m）带来的精度损失。

    ★ 采样轴是**全路线连续里程**，不是逐段。若逐段 ``round(L/step)``，
      零头被截掉，会少 1 点（58 054 而非 58 055）。
    """
    s_total = E[-1]["s1"]
    nseg = int(round(s_total / step))
    U = [i * step for i in range(nseg + 1)]
    U[-1] = s_total                      # 末点精确对齐全长，不留零头

    pts = []
    x = y = 0.0
    for i in range(nseg + 1):
        if i == 0:
            pts.append((U[0], 0.0, 0.0, azimuth_at(E, U[0])))
            continue
        u0, u1 = U[i - 1], U[i]
        h = u1 - u0
        am = azimuth_at(E, (u0 + u1) / 2.0)     # ★ 中点法：结构对称
        x += h * math.cos(am)
        y += h * math.sin(am)
        pts.append((U[i], x, y, azimuth_at(E, U[i])))
    return pts
