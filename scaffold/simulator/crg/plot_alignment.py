#!/usr/bin/env python3
"""把 alignment_element 表还原成平面几何，并和 .crg / .obj 的实测几何对照。

为什么要有这个脚本
------------------
「窗口里怎么只有一小段圆曲线」这个问题，靠肉眼看窗口答不了。
要答它，得先能把**设计数据**（33 个线形单元）和**实际渲染出来的几何**
（chrono_export/*.obj 里的网格顶点）画到同一张图上比。

数据来源三份，互相独立，正好互为校验：
  1. alignment_element.csv  —— 设计表导出（本目录，由 psql 导出）
  2. chrono_export/*.obj    —— Chrono/OpenCRG 实际生成并渲染的三角网格
  3. chrono_centerline.csv  —— Chrono GetRoadCenterLine() 的控制点

用法
----
    PYTHONPATH=/data/cy/shujuku/.pylibs python3 plot_alignment.py

输出 route_0p1m_plan.png。
"""
import csv
import math
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Wedge

HERE = os.path.dirname(os.path.abspath(__file__))
ALIGN = os.path.join(HERE, "alignment_element.csv")
OBJ = os.path.join(HERE, "chrono_export", "route_0p1m_mesh.obj")
OUT = os.path.join(HERE, "route_0p1m_plan.png")

# 中文字体（本机实测有 Noto Serif/Sans CJK SC）
_avail = {f.name for f in matplotlib.font_manager.fontManager.ttflist}
for fam in ("Noto Sans CJK SC", "Noto Serif CJK SC", "Noto Sans CJK JP",
            "AR PL UMing CN", "WenQuanYi Zen Hei"):
    if fam in _avail:
        plt.rcParams["font.family"] = fam
        print(f"字体: {fam}")
        break
else:
    print("!! 没找到中文字体，图中中文会变方框")
plt.rcParams["axes.unicode_minus"] = False

TYPE_CN = {"line": "直线", "transition": "缓和曲线", "circular": "圆曲线"}
TYPE_C = {"line": "#4C78A8", "transition": "#F58518", "circular": "#E45756"}


def load_align():
    if not os.path.exists(ALIGN):
        raise SystemExit(
            f"缺少 {ALIGN}\n"
            "它是从库里导出的设计线形表（本目录 *.csv 被 gitignore，不会随仓库分发）。\n"
            "在本目录执行：\n\n"
            "  docker exec rp-pg psql -U rp -d road_pavement -t -A -F',' -c \"\n"
            "  SELECT element_seq, element_type, start_station_km, end_station_km,\n"
            "         start_x, start_y, end_x, end_y, azimuth_deg, end_azimuth_deg,\n"
            "         coalesce(radius_start_m,-1), coalesce(radius_end_m,-1)\n"
            "  FROM alignment_element WHERE section_id=6 ORDER BY element_seq;\" \\\n"
            "    > alignment_element.csv\n")
    rows = []
    with open(ALIGN, newline="") as f:
        for r in csv.reader(f):
            if not r or len(r) < 12:
                continue
            rows.append(dict(
                seq=int(r[0]), kind=r[1],
                s0=float(r[2]) * 1000.0, s1=float(r[3]) * 1000.0,
                x0=float(r[4]), y0=float(r[5]),
                x1=float(r[6]), y1=float(r[7]),
                az0=float(r[8]), az1=float(r[9]),
                r0=float(r[10]), r1=float(r[11]),
            ))
    return rows


def build(rows, step=0.5):
    """按方位角线性变化积分还原每个单元，返回 (折线, 校验误差表)。

    ★ 方位角约定（实测确认，别再猜）：
      设计表用的投影坐标系是 **X=北、Y=东**，方位角 az 从 +X 轴起算、
      朝 +Y（东）为正。所以方向向量 = (cos az, sin az)。
      验证：单元 1（直线 485.874 m）实际 Δ 归一化 = (-0.861333, +0.508075)，
           而 (cos 149.465047°, sin 149.465047°) = (-0.861328, +0.508077)。
      我第一版写成测量学课本的 (sin az, cos az)（即假设 +Y=北），
      33 个单元**全部**算错，单元 1 偏 941 m。教训：坐标轴朝向要拿数据验，
      不能凭"一般应该是"。

    az 的变化规律（按线形分三种，这是关键）：
      直线    曲率 κ = 0            -> az 恒定
      圆曲线  κ = 1/R 恒定          -> az 线性
      缓和曲线 κ 从 κ0 线性变到 κ1  -> az 是**二次**的

    我第一版对三种都按 az 线性积分，结果 line/circular 精确到 0.01 m，
    缓和曲线却差 0.67~2.31 m（且随长度平方增长，正是被丢掉的二次项）。
    反证：单元 2 是 L=60 m / R=450 的进场缓和曲线，真值 Δaz = L/(2R)
          = 60/900 = 3.8197°，而数据库给的是 149.465047° -> 153.284766°
          = 3.8197°。**数据库本身就是按真回旋线算的**，所以这里也按
          κ 线性积分：az(s) = az0 + κ0·s + (κ1-κ0)·s²/(2L)。
    """
    pts, errs = [], []
    for e in rows:
        L = e["s1"] - e["s0"]
        n = max(int(L / step), 2)
        # 方位角增量要绕到 (-180, 180]，否则 350°->10° 会被当成 -340°
        d = (e["az1"] - e["az0"] + 180.0) % 360.0 - 180.0
        sgn = 1.0 if d >= 0 else -1.0
        k0 = sgn / e["r0"] if e["r0"] > 0 else 0.0
        k1 = sgn / e["r1"] if e["r1"] > 0 else 0.0
        if e["kind"] == "line":
            k0 = k1 = 0.0
        s = np.linspace(0.0, L, n)
        # κ 线性 -> az 二次
        az = np.radians(e["az0"]) + k0 * s + (k1 - k0) * s * s / (2.0 * L)
        # 自洽检查：积分出来的总转角应该等于数据库的 Δaz
        if abs(math.degrees(az[-1] - az[0]) - d) > 0.02:
            print(f"   !! 单元 {e['seq']} ({e['kind']}) 积分转角 "
                  f"{math.degrees(az[-1]-az[0]):.4f}° != 数据库 {d:.4f}°")
        dx, dy = np.cos(az), np.sin(az)
        # 梯形积分
        x = e["x0"] + np.concatenate([[0.0], np.cumsum((dx[1:] + dx[:-1]) * 0.5 * np.diff(s))])
        y = e["y0"] + np.concatenate([[0.0], np.cumsum((dy[1:] + dy[:-1]) * 0.5 * np.diff(s))])
        errs.append((e["seq"], e["kind"], L,
                     math.dist((x[-1], y[-1]), (e["x1"], e["y1"]))))
        pts.append(dict(kind=e["kind"], s0=e["s0"], s1=e["s1"],
                        r=e["r0"] if e["r0"] > 0 else e["r1"], x=x, y=y))
    return pts, errs


def load_obj_centerline():
    """从 .obj 抽中心线：每 5 个顶点是一条 u 站（SimplifyMesh 把 v 压到 5 个）。"""
    if not os.path.exists(OBJ):
        return None
    V = []
    with open(OBJ) as f:
        for line in f:
            if line.startswith("v "):
                p = line.split()
                V.append((float(p[1]), float(p[2]), float(p[3])))
    V = np.array(V)
    if len(V) % 5:
        return None
    R = V.reshape(-1, 5, 3)
    return R[:, 2, :]          # 中间那个 v 点 = 路中


def main():
    rows = load_align()
    total = rows[-1]["s1"]
    print(f"线形单元 {len(rows)} 个，总长 {total:.1f} m")
    by = {}
    for e in rows:
        by.setdefault(e["kind"], [0, 0.0])
        by[e["kind"]][0] += 1
        by[e["kind"]][1] += e["s1"] - e["s0"]
    for k, (n, L) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print(f"  {TYPE_CN[k]:6s} {n:2d} 段  {L:8.1f} m")

    pts, errs = build(rows)
    worst = max(e[3] for e in errs)
    print(f"\n★ 几何自校验：33 个单元积分终点 vs 数据库 end_x/end_y，最大偏差 {worst:.4f} m")
    for seq, kind, L, err in errs:
        if err > 0.01:
            print(f"   单元 {seq} ({kind}, {L:.1f} m) 偏差 {err:.4f} m")

    # 平移到 .crg 局部坐标（纯平移，起点归零）
    X0, Y0 = rows[0]["x0"], rows[0]["y0"]
    for p in pts:
        p["x"] = p["x"] - X0
        p["y"] = p["y"] - Y0
    ref_end = (rows[-1]["x1"] - X0, rows[-1]["y1"] - Y0)
    print(f"★ 换算：设计末点 -> 局部 ({ref_end[0]:.3f}, {ref_end[1]:.3f})")
    print(f"        .crg 官方读出末点  (-4521.122, 3119.874)"
          f"   相差 {math.dist(ref_end, (-4521.122, 3119.874)):.3f} m")

    obj_cl = load_obj_centerline()
    if obj_cl is not None:
        print(f"\n★ .obj 中心线：{len(obj_cl)} 个 u 站")
        # 与积分出的参考线比：取每个 u 站最近的积分点太慢，改为比总长与端点
        print(f"   首点 ({obj_cl[0][0]:.3f}, {obj_cl[0][1]:.3f})"
              f"  末点 ({obj_cl[-1][0]:.3f}, {obj_cl[-1][1]:.3f})")

    # ---------------- 画图 ----------------
    fig = plt.figure(figsize=(16, 10.5))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.45, 1.0],
                          hspace=0.28, wspace=0.22)

    # (a) 平面图
    ax = fig.add_subplot(gs[0, :])
    for p in pts:
        ax.plot(p["x"], p["y"], color=TYPE_C[p["kind"]], lw=3.2,
                solid_capstyle="round", zorder=3)
    if obj_cl is not None:
        ax.plot(obj_cl[:-1, 0], obj_cl[:-1, 1], color="k", lw=0.7, alpha=0.55,
                zorder=4, label=".obj 网格中心线（实测）")
        # ★ 末点被 OpenCRG 的闭合误判补回了起点，这最后一跳就是窗口里
        #   那片跨 5.5 km 的多余三角形。画出来，别藏。
        ax.plot([obj_cl[-2, 0], obj_cl[-1, 0]], [obj_cl[-2, 1], obj_cl[-1, 1]],
                color="#D81B60", lw=2.0, ls=":", zorder=4.5,
                label="末点被补回起点（闭合误判，8 个多余三角形）")

    # 起点 / 终点
    ax.plot(0, 0, "o", ms=13, mfc="w", mec="#2E7D32", mew=3, zorder=6)
    ax.annotate("起点 u=0", (0, 0), textcoords="offset points", xytext=(14, 14),
                fontsize=12, color="#2E7D32", fontweight="bold")
    ax.plot(*ref_end, "o", ms=13, mfc="w", mec="#B71C1C", mew=3, zorder=6)
    ax.annotate(f"终点 u=5805.4 m\n({ref_end[0]:.0f}, {ref_end[1]:.0f})",
                ref_end, textcoords="offset points", xytext=(-10, 26),
                fontsize=12, color="#B71C1C", fontweight="bold", ha="right")

    # 旧相机：500 m 远裁剪面之内才看得见
    old_eye = (-120, -260)
    ax.add_patch(Circle(old_eye, 500, fill=True, color="#FFC107", alpha=0.30,
                        zorder=2, label="旧相机 500 m 远裁剪面内（改前唯一可见区）"))
    ax.add_patch(Circle(old_eye, 500, fill=False, color="#F57C00", lw=2.0,
                        ls="--", alpha=0.9, zorder=2.5))
    ax.plot(*old_eye, "X", ms=15, color="#F57C00", zorder=7)
    ax.annotate("旧相机 (-120, -260)", old_eye, textcoords="offset points",
                xytext=(12, -30), fontsize=11, color="#E65100", fontweight="bold")

    # 新相机
    new_eye = (-5988.91, -3187.15)
    new_tgt = (-2259.97, 1558.78)
    ax.plot(*new_eye, "*", ms=26, color="#1565C0", zorder=7)
    ax.annotate("新相机（自动取景）\n(-5989, -3187)  距目标 6763 m",
                new_eye, textcoords="offset points", xytext=(16, -6),
                fontsize=11, color="#0D47A1", fontweight="bold")
    ax.annotate("", xy=new_tgt, xytext=new_eye,
                arrowprops=dict(arrowstyle="-|>", color="#1565C0",
                                lw=2.2, alpha=0.85), zorder=5)
    # 视锥只画到目标距离为止。第一版画到 13.5 km，把坐标轴撑到 ±2 万米，
    # 路面和 500 m 圆圈都缩成了小点 —— 图能出，但没法看。
    half = math.radians(30.0)          # 60° 垂直视场，水平更宽
    base = math.atan2(new_tgt[1] - new_eye[1], new_tgt[0] - new_eye[0])
    reach = math.dist(new_eye, new_tgt)
    for s in (+1, -1):
        ax.plot([new_eye[0], new_eye[0] + reach * math.cos(base + s * half)],
                [new_eye[1], new_eye[1] + reach * math.sin(base + s * half)],
                color="#1565C0", lw=1.1, ls="--", alpha=0.5, zorder=2)

    # 显式定范围：把路面、两个相机都框进来，再留 8% 余量
    xs = [p["x"].min() for p in pts] + [p["x"].max() for p in pts] + \
         [old_eye[0] - 500, old_eye[0] + 500, new_eye[0]]
    ys = [p["y"].min() for p in pts] + [p["y"].max() for p in pts] + \
         [old_eye[1] - 500, old_eye[1] + 500, new_eye[1]]
    x0l, x1l = min(xs), max(xs)
    y0l, y1l = min(ys), max(ys)
    mx, my = 0.08 * (x1l - x0l), 0.08 * (y1l - y0l)
    ax.set_xlim(x0l - mx, x1l + mx)
    ax.set_ylim(y0l - my, y1l + my)

    ax.set_aspect("equal")
    ax.grid(alpha=0.25, ls=":")
    ax.set_xlabel("x / m（.crg 局部坐标）")
    ax.set_ylabel("y / m")
    ax.set_title("(a) 平面线形：33 个单元，总长 5805.4 m —— 不是一段圆弧，是来回摆的盘山路",
                 fontsize=14, fontweight="bold")
    h, l = ax.get_legend_handles_labels()
    h = [plt.Line2D([], [], color=TYPE_C[k], lw=3.5, label=TYPE_CN[k])
         for k in ("line", "transition", "circular")] + h
    ax.legend(handles=h, loc="upper left", fontsize=10.5, framealpha=0.92)

    # (b) 半径 vs 桩号
    ax2 = fig.add_subplot(gs[1, 0])
    for p in pts:
        m = p["s0"] / 1000.0
        if p["kind"] == "circular":
            ax2.plot([m, p["s1"] / 1000.0], [p["r"]] * 2,
                     color=TYPE_C["circular"], lw=6, solid_capstyle="butt")
    ax2.set_ylim(0, 560)
    ax2.set_xlim(0, total / 1000.0)
    ax2.set_xlabel("桩号 / km")
    ax2.set_ylabel("圆曲线半径 R / m")
    ax2.grid(alpha=0.3, ls=":")
    ax2.set_title("(b) 8 段圆曲线的半径（255–500 m，都是实打实的弯）",
                  fontsize=12.5, fontweight="bold")

    # (c) 线形组成
    ax3 = fig.add_subplot(gs[1, 1])
    ks = ["line", "transition", "circular"]
    Ls = [by[k][1] for k in ks]
    ns = [by[k][0] for k in ks]
    bars = ax3.barh([TYPE_CN[k] for k in ks], Ls,
                    color=[TYPE_C[k] for k in ks], height=0.6)
    for b, L, n in zip(bars, Ls, ns):
        ax3.text(b.get_width() + 60, b.get_y() + b.get_height() / 2,
                 f"{L:.0f} m   {n} 段", va="center", fontsize=11.5)
    ax3.set_xlim(0, max(Ls) * 1.42)
    ax3.set_xlabel("累计长度 / m")
    ax3.grid(alpha=0.3, axis="x", ls=":")
    ax3.set_title("(c) 线形组成", fontsize=12.5, fontweight="bold")

    fig.suptitle("2025Y095 路面性能数智化平台 · CRG 线形实况",
                 fontsize=17, fontweight="bold", y=0.985)
    fig.savefig(OUT, dpi=115, bbox_inches="tight", facecolor="white")
    print(f"\n==> 已写出 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
