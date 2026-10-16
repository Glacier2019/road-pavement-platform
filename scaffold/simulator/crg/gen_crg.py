"""从库中真实线形 + 高程生成 OpenCRG 路面模型（0.1 m 纵向分辨率）。

数据源（全部为库中实测/设计数据，无插值造数）：
  alignment_element      33 行  平面线形 κ(s)
  profile_grade_point    12 行  纵断面竖曲线（抛物线）
  roadbed_design_point  332 行  设计高程 + 车道/路肩宽度（20 m 间隔）
  superelev_transition   76 行  横断面横坡 6 通道（左右 x 车道/硬路肩/土路肩）

三维曲面按论文 (2.16) 构造：Σ: r(l,w) = r(l) + w·N_l^rot
  r(l)      准线 —— 平面线形积分得到，高程取设计高程（论文 (2.15)）
  N_l^rot   横断面方向 —— 主法向绕切向量 T 旋转 θ*（论文 (2.14)）
  θ*        该点的横坡，逐段取值（行车道 / 硬路肩各一段）
"""
import math, os, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import xsection as XS          # ★ 横断面数学的唯一实现处（契约测试直接测它）
import alignment as AL         # ★ 平面线形积分的唯一实现处（论文 (2.15)，同上）

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

# ---------- 3. 横断面宽度（20 m 间隔线性插值）----------
# ★ 全 10 个宽度列都读 —— 横断面设计线有 11 个点，只读 4 个宽度就还原不全。
#   列序取自 xsection.POINT_WIDTH_COLUMNS（该顺序被 elev_diff 实测值独立证实）。
WCOL = list(XS.POINT_WIDTH_COLUMNS)
W = []
for p in q("SELECT station_km, " + ", ".join(WCOL) +
           " FROM roadbed_design_point ORDER BY station_km;"):
    W.append((f(p[0])*1000, [f(x) for x in p[1:11]]))

def widths_all(s):
    """该桩号的 10 个宽度（顺序同 xsection.POINT_WIDTH_COLUMNS），线性插值。"""
    if s <= W[0][0]:
        return list(W[0][1])
    if s >= W[-1][0]:
        return list(W[-1][1])
    for i in range(len(W) - 1):
        if W[i][0] <= s <= W[i+1][0]:
            span = W[i+1][0] - W[i][0]
            t = 0.0 if span == 0 else (s - W[i][0]) / span
            return [a + t*(b - a) for a, b in zip(W[i][1], W[i+1][1])]
    return list(W[-1][1])

def half_width(s):
    """左/右半幅（车道 + 硬路肩）—— 用于确定 CRG 的 v 覆盖范围。"""
    w = widths_all(s)
    return w[3] + w[1], w[6] + w[8]

# ---------- 3b. 横断面横坡（超高过渡表，6 个通道）----------
# 论文 (2.16)：Σ: r(l,w) = r(l) + w·N_l^rot
#   N_l^rot = ( −cosθ*·cosα_l , sinθ*·cosα_l , sinθ* )     —— 论文 (2.14)
# 即「横断面设计线」= 绕准线切向量 T 旋转 θ* 后的主法向量。
# 本项目路面横向分两段：行车道 + 硬路肩，各有自己的 θ*。
# ★ 用水平偏移 v 时 Δz = v·tanθ*；这与论文「沿母线量 w」的 Δz = w·sinθ*
#   完全等价（w = v/cosθ*），见 TURN-168 的核对：两者差 5.55e-11 µm。
XF = []
for p in q("SELECT station_km, earth_shoulder_left_pct, hard_shoulder_left_pct, "
           "lane_left_pct, lane_right_pct, hard_shoulder_right_pct, "
           "earth_shoulder_right_pct FROM superelev_transition ORDER BY station_km;"):
    XF.append((f(p[0])*1000, [f(x) for x in p[1:7]]))

# ★ 逐通道建断点表：超高过渡表里某些桩号只填了部分通道（其余为 NULL），
#   不能整行丢弃 —— 那会在桩号上留洞。逐通道各自插值。
XF_SER = [[] for _ in range(6)]          # [ [ (s, pct), ... ] × 6 ]
for s_m, vals in XF:
    for c in range(6):
        if vals[c] is not None:
            XF_SER[c].append((s_m, vals[c]))

def crossfall(s):
    """返回该桩号的 6 个横坡（小数，非百分数）：左土/左硬/左车道/右车道/右硬/右土。

    逐通道在自己有值的桩号间线性插值；超出首末则取端点值（常值外延）。
    某通道整列为空时返回 0.0（平坡）—— 这是显式的、可被测试抓到的兜底。
    """
    out6 = []
    for ser in XF_SER:
        if not ser:
            out6.append(0.0); continue
        if s <= ser[0][0]:
            out6.append(ser[0][1] / 100.0); continue
        if s >= ser[-1][0]:
            out6.append(ser[-1][1] / 100.0); continue
        v = None
        for i in range(len(ser) - 1):
            if ser[i][0] <= s <= ser[i+1][0]:
                span = ser[i+1][0] - ser[i][0]
                t = 0.0 if span == 0 else (s - ser[i][0]) / span
                v = (ser[i][1] + t*(ser[i+1][1] - ser[i][1])) / 100.0
                break
        out6.append(0.0 if v is None else v)
    return out6

# ---------- 4. 逐 0.1 m 积分 ----------
STEP = 0.1
S_TOTAL = E[-1]["s1"]
N = int(round(S_TOTAL / STEP))          # 段数
print("全长 %.6f m，0.1 m 步长 → %d 段，%d 点（含两端）" % (S_TOTAL, N, N+1), file=sys.stderr)

# ★ 平面坐标的积分在 `alignment.py`（论文 (2.15) 前两个积分）。
#   2026-10-16 之前它写在本文件里，而**没有任何测试 import 它** ——
#   同一天手工对账证明积分是对的（对库坐标残差 0.471 mm），但手工对账不留痕。
#   门在 tests/contract/test_alignment_closure.py。
#   采样轴是【全路线连续里程】，不是逐段：逐段 round(L/STEP) 会截掉零头，
#   少 1 点（58054 而非 58055）。
pts = AL.integrate(E, STEP)

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
    """路面高程 —— 论文 (2.16) 的直纹面 Σ: r(l,w) = r(l) + w·N_l^rot。

    ★ 横断面的数学**不在这里** —— 在 simulator/crg/xsection.py。
      这样契约测试能直接测那段代码，而不是靠读 CRG 产物间接猜。
      （2026-10-15：正因数学曾写在本文件里且没人测，v 被整个忽略，
        101 MB 的 CRG 横向 100% 平坦，见 D-032。）
    """
    w_all = widths_all(s)
    return elevation(s) + XS.z_offset(v, crossfall(s), w_all)

out = []
out.append("$ROAD_CRG")
out.append("* 2025Y095 road performance platform - generated from DB alignment")
out.append("* horizontal: alignment_element 33 rows, 0.1 m analytic integration")
out.append("* vertical:   profile_grade_point 12 rows, parabolic vertical curves")
out.append("* width:      roadbed_design_point 332 rows, 20 m interval")
out.append("* crossfall:  superelev_transition 76 rows, 6 channels (paper eq. 2.16)")
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

OUT = os.environ.get("CRG_OUT", "route_0p1m.crg")
with open(OUT, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")

print("已写出 %s" % OUT, file=sys.stderr)
print("  点数 %d, 横向列数 %d, 总行数 %d" % (len(ss), len(v_list), len(out)), file=sys.stderr)
print("  末点 x=%.3f y=%.3f az=%.4f°" % (xs[-1], ys[-1], math.degrees(azs[-1])), file=sys.stderr)
