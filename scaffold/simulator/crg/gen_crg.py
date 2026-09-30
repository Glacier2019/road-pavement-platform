"""从库中真实线形 + 高程生成 OpenCRG 路面模型（0.1 m 纵向分辨率）。

数据源（全部为库中实测/设计数据，无插值造数）：
  alignment_element      33 行  平面线形 κ(s)
  profile_grade_point    12 行  纵断面竖曲线（抛物线）
  roadbed_design_point  332 行  设计高程 + 车道/路肩宽度（20 m 间隔）
"""
import math, subprocess, sys

DSN = ["docker", "exec", "rp-pg", "psql", "-U", "rp", "-d", "road_pavement", "-t", "-A", "-F", "|", "-c"]

def q(sql):
    r = subprocess.run(DSN + [sql], capture_output=True, text=True, check=True)
    return [l.split("|") for l in r.stdout.strip().splitlines() if l.strip()]

def f(x):
    return float(x) if x not in ("", "NULL") else None

# ---------- 1. 平面线形 ----------
E = []
for p in q("SELECT element_seq, element_type, start_station_km, end_station_km, azimuth_deg, "
           "end_azimuth_deg, radius_start_m, radius_end_m FROM alignment_element ORDER BY element_seq;"):
    E.append(dict(seq=int(p[0]), t=p[1], s0=f(p[2])*1000, s1=f(p[3])*1000,
                  az0=f(p[4]), az1=f(p[5]), r0=f(p[6]), r1=f(p[7])))

def curvature(e):
    """返回段内 κ(s)=k0+(k1-k0)s/L 的 (k0,k1,L)。方向由 Δazimuth 给出（半径列无符号）。"""
    L = e["s1"] - e["s0"]
    if e["t"] == "line":
        return 0.0, 0.0, L
    R = e["r0"] if e["r0"] is not None else e["r1"]
    sign = 1.0 if e["az1"] > e["az0"] else -1.0
    if e["t"] == "circular":
        return sign/R, sign/R, L
    if e["r0"] is None:      # 入口缓和 ∞ -> R
        return 0.0, sign/R, L
    return sign/R, 0.0, L    # 出口缓和 R -> ∞

# ---------- 2. 纵断面（竖曲线为抛物线）----------
V = []
for p in q("SELECT vpi_seq, station_km, elevation_m, vertical_curve_radius_m, grade_in_pct, "
           "grade_out_pct, grade_len_m FROM profile_grade_point ORDER BY vpi_seq;"):
    V.append(dict(seq=int(p[0]), s=f(p[1])*1000, z=f(p[2]), R=f(p[3]),
                  g_in=f(p[4]), g_out=f(p[5]), glen=f(p[6])))

def elevation(s):
    """按竖曲线逐段求设计高程。坡度以小数计。"""
    if s <= V[0]["s"]:
        return V[0]["z"]
    for i in range(len(V) - 1):
        a, b = V[i], V[i+1]
        if not (a["s"] <= s <= b["s"]):
            continue
        g1 = (a["g_out"] or 0) / 100.0          # 进入 i+1 段的坡
        g2 = (b["g_in"] or 0) / 100.0           # 离开 i+1 段的坡
        L = b["s"] - a["s"]
        R = b["R"] or 0
        if R == 0 or abs(g2 - g1) < 1e-12:
            return a["z"] + g1 * (s - a["s"])
        # 抛物线竖曲线：以 (a) 为起点的切线长 T=|Δg|*R/2，曲线长 Lc=|Δg|*R
        T = abs(g2 - g1) * R / 2.0
        Lc = abs(g2 - g1) * R
        # VPI 处的切线交点
        vpi_s = a["s"] + T
        vpi_z = a["z"] + g1 * T
        if abs(s - vpi_s) <= Lc / 2.0:
            d = s - vpi_s
            return vpi_z + g1 * d + (g2 - g1) / (2.0 * Lc) * d * d
        return a["z"] + g1 * (s - a["s"])
    return V[-1]["z"]

# ---------- 3. 横断面宽度（20 m 间隔线性插值，仅用于路幅宽度）----------
W = []
for p in q("SELECT station_km, left_lane_width_m, right_lane_width_m, left_hard_shoulder_width_m, "
           "right_hard_shoulder_width_m FROM roadbed_design_point ORDER BY station_km;"):
    W.append((f(p[0])*1000, f(p[1]), f(p[2]), f(p[3]), f(p[4])))

def half_width(s):
    """左/右半幅（含路肩）"""
    if s <= W[0][0]:
        return W[0][3] + W[0][1], W[0][4] + W[0][2]
    if s >= W[-1][0]:
        return W[-1][3] + W[-1][1], W[-1][4] + W[-1][2]
    for i in range(len(W) - 1):
        if W[i][0] <= s <= W[i+1][0]:
            t = (s - W[i][0]) / (W[i+1][0] - W[i][0])
            lw = W[i][3] + t*(W[i+1][3]-W[i][3]) + W[i][1] + t*(W[i+1][1]-W[i][1])
            rw = W[i][2] + t*(W[i+1][2]-W[i][2]) + W[i][4] + t*(W[i+1][4]-W[i][4])
            return lw, rw
    return W[-1][3] + W[-1][1], W[-1][4] + W[-1][2]

# ---------- 4. 逐 0.1 m 积分 ----------
STEP = 0.1
S_TOTAL = E[-1]["s1"]
N = int(round(S_TOTAL / STEP))          # 段数
print("全长 %.6f m，0.1 m 步长 → %d 段，%d 点（含两端）" % (S_TOTAL, N, N+1), file=sys.stderr)

# ★ 关键：以【全路线连续里程】为采样轴，段边界自然落在采样点上。
#   若逐段 round(L/STEP)，零头被截掉，会少 1 点（58054 而非 58055）。
NSEG = int(round(S_TOTAL / STEP))      # 58054 段
U = [i * STEP for i in range(NSEG + 1)]  # 0.0 ... 5805.4
U[-1] = S_TOTAL                          # 末点精确对齐全长

def az_at(u):
    """按里程查段，返回方位角 az(u)。"""
    for e in E:
        if e["s0"] <= u <= e["s1"]:
            k0, k1, LL = curvature(e)
            s = u - e["s0"]
            return math.radians(e["az0"]) + k0*s + (k1-k0)*s*s/(2*LL)
    return math.radians(E[-1]["az1"])

# 用梯形积分沿连续里程推进坐标
pts = []
x = y = 0.0
for i in range(NSEG + 1):
    u = U[i]
    if i == 0:
        pts.append((u, 0.0, 0.0, az_at(0.0)))
        continue
    u0, u1 = U[i-1], U[i]
    h = u1 - u0
    a0, a1 = az_at(u0), az_at(u1)
    am = az_at((u0+u1)/2.0)
    # Simpson 更准，但 0.1 m 下梯形已足够；用中点法保证结构对称
    x += h*math.cos(am); y += h*math.sin(am)
    pts.append((u, x, y, a1))

# 起点作为 (0,0)，把坐标整体平移
xs = [p[1] for p in pts]; ys = [p[2] for p in pts]
ss = [p[0] for p in pts]; azs = [p[3] for p in pts]

# ---------- 5. 生成 CRG ----------
# 横向：v 从 -vmax 到 +vmax，间隔 0.1 m；中心线为基准
VINC = 0.1
vmax = max(max(half_width(s)) for s in ss)
# ★ 必须让 -vmax 与 +vmax 都恰好落在列上：用 int(round(2*vmax/VINC)) 定列数，
#   否则末列会差一个 VINC（此前 4.25 变成 4.15）。
ncols = int(round(2*vmax / VINC))
v_list = [round(-vmax + j*(2*vmax/ncols), 6) for j in range(ncols + 1)]

def road_z(s, v):
    """路面高程：设计高程 + 按横坡（暂取平坡，骨架期无横坡数据）"""
    return elevation(s)

out = []
out.append("$ROAD_CRG")
out.append("* 2025Y095 road performance platform - generated from DB alignment")
out.append("* horizontal: alignment_element 33 rows, 0.1 m analytic integration")
out.append("* vertical:   profile_grade_point 12 rows, parabolic vertical curves")
out.append("* width:      roadbed_design_point 332 rows, 20 m interval")
# 保证末点精确落在全长上：最后一段不靠 round(L/STEP)，而是整体对齐
out.append("REFERENCE_LINE_START_U = %.6f" % 0.0)
out.append("REFERENCE_LINE_END_U   = %.6f" % S_TOTAL)
out.append("REFERENCE_LINE_INCREMENT = %.6f" % STEP)
out.append("LONG_SECTION_V_RIGHT = %.3f" % (-vmax))
out.append("LONG_SECTION_V_LEFT  = %.3f" % (vmax))
out.append("LONG_SECTION_V_INCREMENT = %.3f" % VINC)
out.append("$")                       # ★ 段结束符（官方格式必需）
out.append("$KD_DEFINITION")
# ★ 格式码：L=Long D=Double F=ASCII（见 crgLoader.c decodeDataFormat）
# ★ 格式码必须用 K(compact) 而非 L(long)：
#   L 会让 calcRecordSize 把每条记录强行对齐到 80 字节的整数倍（定长读），
#   而我们的 ASCII 行是变长的 → 解析错位、z 全为 nan、u 范围被截断。
#   K=compact 才是变长 ASCII 的正确选择。
out.append("#:KDF")
# ★ U: 行声明 u 轴（起点、增量）——官方格式必需，缺了它 loader 会当成"无数据段"
out.append("U:reference line u,m,%.3f,%.3f" % (0.0, STEP))
out.append("D:reference line phi,rad")          # 第 1 个通道：参考线方位角（弧度）
for v in v_list:
    out.append("D:long section at v = %.3f,m" % v)  # 之后每通道 1 个横向截面的高程

# ★★ 段结束 + 数据段起始标记（顺序不可颠倒）
#    1) 单独的 "$" 行先结束 $KD_DEFINITION 段（setSection 要求当前段为 None 才切换）
#    2) "$$$$" 行再切到 dFileSectionDataContent
#    见 crgLoader.c: { "$", setSection, dFileSectionNone } / { "$$$$", ..., dFileSectionDataContent }
out.append("$")
out.append("$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$")

# ★★ ASCII 紧凑格式要求每个通道【定宽 20 字符】：
#    crgLoader.c decodeRecord() 对 ASCII 用 length=20 的 strncpy + atof，
#    只跳过行首的 \n/\r（不跳空格）。所以必须左对齐补齐到 20 字符。
def fld(x):
    s = "%.10f" % x
    return s[:20].ljust(20)

for i, s in enumerate(ss):
    row = [fld(azs[i])]                           # 通道1: phi(rad)
    for v in v_list:
        row.append(fld(road_z(s, v)))             # 通道2..N: 各截面高程
    out.append("".join(row))

import os
OUT = os.environ.get("CRG_OUT", "route_0p1m.crg")
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")

print("已写出 %s" % OUT, file=sys.stderr)
print("  点数 %d, 横向列数 %d, 总行数 %d" % (len(ss), len(v_list), len(out)), file=sys.stderr)
print("  末点 x=%.3f y=%.3f az=%.4f°" % (xs[-1], ys[-1], math.degrees(azs[-1])), file=sys.stderr)
