"""从 .crg 读回，导出三角网格 (.obj) + 中心线 (.csv/.bez) —— 纯 CPU，不需 GL。
这正是 Chrono CRGTerrain 的 "export road mesh" / "export center line" 能力所做的事。"""
import math

import sys
P = sys.argv[1] if len(sys.argv) > 1 else "route_0p1m.crg"
L = open(P, encoding="utf-8").read().splitlines()
ih  = next(i for i, l in enumerate(L) if l.upper().startswith("$KD_DEFINITION"))
imk = next(i for i, l in enumerate(L) if l.startswith("$$$$"))
hdr = L[:ih]
D   = [l for l in L if l.startswith("D:")]
defs= L[ih:imk]

# ★ 定宽 20 字符解析（与官方 crgLoader.c decodeRecord 一致）
def rec(line, nch, width=20):
    return [float(line[k*width:(k+1)*width]) for k in range(nch)]

NC = len([l for l in defs if l.startswith("D:")])
data = [rec(l, NC) for l in L[imk+1:] if l.strip()]

def hval(key):
    for l in hdr:
        if l.startswith(key):
            return float(l.split("=")[1])
    return None

U0, U1, UINC = hval("REFERENCE_LINE_START_U"), hval("REFERENCE_LINE_END_U"), hval("REFERENCE_LINE_INCREMENT")
VR, VL, VINC = hval("LONG_SECTION_V_RIGHT"), hval("LONG_SECTION_V_LEFT"), hval("LONG_SECTION_V_INCREMENT")

# v 值从 D: 行解析
import re
vs = [float(re.search(r"v = ([-\d.]+)", l).group(1)) for l in D if "v =" in l]
nchan = len(D)
nsec = len(vs)

# ★ phi 是【增量】还是【绝对值】？
# OpenCRG 规范：reference line phi 通道存的是参考线方位角（绝对，弧度）。
phis = [float(r[0]) for r in data]
print("phi 首=%.6f rad (%.4f°)  末=%.6f rad (%.4f°)" % (phis[0], math.degrees(phis[0]), phis[-1], math.degrees(phis[-1])))

# 由 phi 积分出平面坐标（中心线）
x = y = 0.0
cx, cy = [], []
u = U0
for i, r in enumerate(data):
    if i > 0:
        h = UINC
        # 用中点方位角
        pm = (phis[i-1] + phis[i]) / 2.0
        x += h*math.cos(pm); y += h*math.sin(pm)
    cx.append(x); cy.append(y)
print("中心线末点 x=%.3f y=%.3f  (库中线末点 x=-4521.335 y=3120.229)" % (x, y))

# 横向单位法向（垂直参考线，左侧为正）
# v 列顺序: D: 行顺序即列顺序（v 从 -vmax 到 +vmax 递增）
z_idx0 = 1   # 第 0 列是 phi，之后是各截面
# 导出 OBJ
out = ["# 2025Y095 route surface mesh, generated from DB alignment"]
out.append("# %d points along u (0.1 m), %d lateral cuts" % (len(data), nsec))
verts = []
for i, r in enumerate(data):
    a = phis[i]                     # 该点方位角
    nx, ny = -math.sin(a), math.cos(a)   # 左法向
    for j in range(nsec):
        v = vs[j]
        z = float(r[z_idx0 + j])
        verts.append((cx[i] + nx*v, cy[i] + ny*v, z))
nver = len(verts)
for vx, vy, vz in verts:
    out.append("v %.4f %.4f %.4f" % (vx, vy, vz))

# 三角形面片
def vid(i, j):
    return i*nsec + j + 1
ntri = 0
for i in range(len(data) - 1):
    for j in range(nsec - 1):
        a, b, c, d = vid(i,j), vid(i,j+1), vid(i+1,j+1), vid(i+1,j)
        out.append("f %d %d %d" % (a, b, c))
        out.append("f %d %d %d" % (a, c, d))
        ntri += 2

open("route_surface.obj", "w", encoding="utf-8").write("\n".join(out) + "\n")
print("已写出 route_surface.obj：%d 顶点, %d 三角面" % (nver, ntri))

# 中心线
with open("route_centerline.csv", "w", encoding="utf-8") as fh:
    fh.write("u_m,x_m,y_m,z_m,azimuth_deg\n")
    for i, r in enumerate(data):
        u = U0 + i*UINC
        zc = float(r[z_idx0 + vs.index(0.0)]) if 0.0 in vs else float(r[z_idx0 + nsec//2])
        fh.write("%.4f,%.4f,%.4f,%.4f,%.6f\n" % (u, cx[i], cy[i], zc, math.degrees(phis[i])))
print("已写出 route_centerline.csv：%d 点" % len(data))

# 边界曲线（左右两条），供 Bezier 用
with open("route_edges.csv", "w", encoding="utf-8") as fh:
    fh.write("u_m,left_x,left_y,left_z,right_x,right_y,right_z\n")
    for i, r in enumerate(data):
        u = U0 + i*UINC
        a = phis[i]; nx, ny = -math.sin(a), math.cos(a)
        zl = float(r[z_idx0 + nsec - 1]); zr = float(r[z_idx0 + 0])
        vl, vr = vs[-1], vs[0]
        fh.write("%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f\n" % (
            u, cx[i]+nx*vl, cy[i]+ny*vl, zl, cx[i]+nx*vr, cy[i]+ny*vr, zr))
print("已写出 route_edges.csv（左右边界 → Bezier 控制点来源）")
