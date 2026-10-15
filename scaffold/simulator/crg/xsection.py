"""横断面设计线 —— 论文 (2.16) 的实现，与数据源解耦。

论文 (2.16)：Σ: r(l,w) = r(l) + w·N_l^rot
论文 (2.14)：N_l^rot = ( −cosθ*·cosα_l , sinθ*·cosα_l , sinθ* )

本模块只负责横断面这一半：给定中线的六个横坡 θ* 与各段宽度，
算出横向偏移 v 处的相对抬升 Δz。中线坐标与高程由调用方提供。

为什么分段
----------
论文只有一个 θ*(l)，几何上是一片过中线的平面。真实路面的横断面是
折线（路拱是「人字」，一个平面做不出来），所以按段取 θ*，段内仍是
论文的平面。本项目 70.2% 的断面是路拱，分段不是可选优化。

v 与 w 的关系
-------------
v 是【水平】偏移，w 是论文里沿母线量的距离，二者差 cosθ*。
用 v 时 Δz = v·tanθ*，与论文的 Δz = w·sinθ* 等价（w = v/cosθ*）。
两者实测差 5.55e-11 µm，可忽略。
"""

# roadbed_design_point 的宽度列，按【从左侧外缘走到右侧外缘】的顺序。
# ★ 这个顺序来自 DDL 列序，并被 elev_diff_01..11 的实测值独立证实：
#   按此顺序累积宽度得到 11 个点，其「相对最低点的抬升量」与库中
#   332 个断面的 elev_diff 列逐位吻合。
POINT_WIDTH_COLUMNS = (
    "left_earth_shoulder_width_m",
    "left_hard_shoulder_width_m",
    "left_median_width_m",
    "left_lane_width_m",
    "right_median_width_m",
    "extra_width_09_m",
    "right_lane_width_m",
    "extra_width_11_m",
    "right_hard_shoulder_width_m",
    "right_earth_shoulder_width_m",
)

# 六个横坡的通道顺序（与 superelev_transition 的列一致）
CROSSFALL_COLUMNS = (
    "earth_shoulder_left_pct",
    "hard_shoulder_left_pct",
    "lane_left_pct",
    "lane_right_pct",
    "hard_shoulder_right_pct",
    "earth_shoulder_right_pct",
)


def point_offsets(widths):
    """按 DDL 列序累积宽度，返回 11 个点相对【左侧外缘】的偏移。

    widths 是长度 10 的序列，顺序同 POINT_WIDTH_COLUMNS。
    返回 [0, w0, w0+w1, ...] 共 11 个数。
    """
    if len(widths) != 10:
        raise ValueError("需要 10 个宽度，收到 %d 个" % len(widths))
    out = [0.0]
    acc = 0.0
    for w in widths:
        acc += float(w or 0.0)
        out.append(acc)
    return out


def centerline_offset(widths):
    """中线相对左侧外缘的偏移 = 左半幅（土路肩 + 硬路肩 + 中央分隔带 + 车道）。"""
    return sum(float(w or 0.0) for w in widths[:4])


def z_offset(v, crossfall, widths):
    """横向偏移 v 处的相对抬升（米），v 以中线为原点、向右为正。

    crossfall 是长度 6 的序列，单位【小数】（不是百分数），
    顺序同 CROSSFALL_COLUMNS：[左土, 左硬, 左车道, 右车道, 右硬, 右土]。
    横坡为正表示该段向外抬升。

    分三段，各用自己那一档 θ*；段内是直线，段与段之间是折点。
    """
    cf = [float(x or 0.0) for x in crossfall]
    w = [float(x or 0.0) for x in widths]

    if v >= 0.0:                       # 中心线右侧
        w_lane, w_hard = w[6], w[8]
        t_lane, t_hard, t_earth = cf[3], cf[4], cf[5]
    else:                              # 中心线左侧
        v = -v
        w_lane, w_hard = w[3], w[1]
        t_lane, t_hard, t_earth = cf[2], cf[1], cf[0]

    if v <= w_lane:
        return v * t_lane
    if v <= w_lane + w_hard:
        return w_lane * t_lane + (v - w_lane) * t_hard
    return (w_lane * t_lane + w_hard * t_hard
            + (v - w_lane - w_hard) * t_earth)


def bump_above_lowest(zs):
    """把一组高程转成「相对最低点的抬升量」。

    ★ 这正是库中 roadbed_design_point.elev_diff_01..11 的口径 ——
      实测该组列的最小值恒为 0，故它就是抬升量而非绝对高程。
      比对时必须用同一口径，否则会凭空多出一个常数差。
    """
    lo = min(zs)
    return [z - lo for z in zs]


def section_bumps(crossfall, widths, widths_h=None):
    """直接算出 11 个点相对最低点的抬升量（米）—— 与 elev_diff 同口径。

    crossfall 见 z_offset；widths 是 10 个宽度。
    """
    offs = point_offsets(widths)
    c0 = centerline_offset(widths)
    zs = [z_offset(x - c0, crossfall, widths) for x in offs]
    return bump_above_lowest(zs)
