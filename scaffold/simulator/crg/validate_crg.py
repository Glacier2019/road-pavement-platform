"""按 OpenCRG 1.1.2 官方 loader 源码（crgLoader.c）的真实规则校验 .crg。
每条规则都注明其在官方源码中的依据。"""
import re, sys

P = sys.argv[1] if len(sys.argv) > 1 else "route_0p1m.crg"
L = open(P, encoding="utf-8").read().splitlines()

# 段边界：数据段从 "$$$$" 行之后开始（crgLoader.c: {"$$$$", dFileSectionDataContent}）
i_mark = next(i for i, l in enumerate(L) if l.startswith("$$$$"))
i_kd   = next(i for i, l in enumerate(L) if l.upper().startswith("$KD_DEFINITION"))
head   = L[:i_kd]                       # $ROAD_CRG 段
defs   = L[i_kd:i_mark]                 # $KD_DEFINITION 段
data   = L[i_mark+1:]
D = [l for l in defs if l.startswith("D:")]
U = [l for l in defs if l.startswith("U:")]
FMT = [l for l in defs if l.startswith("#:")]
nch = len(D)

print("总行数 %d | 头 %d 行 | 定义 %d 行 | 数据 %d 行 | 通道 %d"
      % (len(L), len(head), len(defs), len(data), nch))

results = []
def chk(name, cond, extra=""):
    results.append(bool(cond))
    print(("  ✓ " if cond else "  ✗ ") + name + ("  " + str(extra) if extra else ""))
    return cond

# --- 头段规则（官方 parseFileHeader：头行 <= 1024，dCrgLoaderBufferLen）---
chk("头行 <= 72 字节", all(len(l) <= 72 for l in head),
    [len(l) for l in head if len(l) > 72][:3])
chk("头行纯 ASCII", all(ord(c) < 128 for l in head for c in l))

# --- 段标记（官方 sLoaderCallbacksCommon 表）---
allsec = [l for l in L if l.startswith("$")]
chk("有 $ROAD_CRG 段", any(s.upper().startswith("$ROAD_CRG") for s in allsec))
chk("有 $KD_DEFINITION 段", any(s.upper().startswith("$KD_DEFINITION") for s in allsec))
# 关键：$KD_DEFINITION 必须先用单独 "$" 结束，再由 "$$$$" 进入数据段
seg_end = [i for i, l in enumerate(L) if l == "$"]
chk("$KD_DEFINITION 与 $$$$ 之间有单独的 '$' 结束行",
    any(i > i_kd and i < i_mark for i in seg_end),
    "结束行位置 %s, KD=%d, $$$$=%d" % (seg_end, i_kd, i_mark))

# --- 格式码（官方 decodeDataFormat：L/K, D, F/B 三组标志）---
flags = FMT[0][2:6] if FMT else ""
chk("有 #: 格式码行", len(FMT) == 1, FMT)
chk("格式码为 compact+double+ASCII (KDF)",
    "K" in flags and "D" in flags and "F" in flags and "L" not in flags, flags)

# --- u 轴（官方要求 U: 行；且 useIndex 时由数据首列推出）---
chk("有 U: 轴声明行", len(U) == 1, U)
chk("有 REFERENCE_LINE_INCREMENT", any(l.startswith("REFERENCE_LINE_INCREMENT") for l in head))

# --- 列布局：数据列 == 通道数（每通道 1 值）---
rowlens = set(len(d) for d in data)
chk("数据行等长", len(rowlens) == 1, rowlens)
# ★★ 官方 decodeRecord() 对 ASCII 用定宽 20 字符 strncpy → 行宽必须是 20*通道数
chk("行宽 == 20 * 通道数 (%d)" % (20 * nch), rowlens == {20 * nch}, rowlens)

# --- 每字段可解析 ---
bad = []
for i, d in enumerate(data[:300]):
    for k in range(nch):
        f = d[k*20:(k+1)*20]
        if f.strip() and not re.match(r"^[\d.+\-eEdD ]+$", f):
            bad.append((i, k, repr(f)))
chk("每 20 字符字段可被 atof 解析（抽检前 300 行）", not bad, bad[:3])

# --- u 行数（用连续里程口径）---
inc = float([l for l in L if l.startswith("REFERENCE_LINE_INCREMENT")][0].split("=")[1])
u0  = float([l for l in L if l.startswith("REFERENCE_LINE_START_U")][0].split("=")[1])
u1  = float([l for l in L if l.startswith("REFERENCE_LINE_END_U")][0].split("=")[1])
exp = int(round((u1 - u0) / inc)) + 1
chk("u 行数 == (u1-u0)/inc + 1 = %d" % exp, len(data) == exp, len(data))

# --- v 单调等距（官方 decodeDefined 解析 "long section at v = X"）---
vs = [float(re.search(r"v ?= ?([-\d.]+)", l).group(1))
      for l in D if "long section" in l and "v =" in l]
chk("v 严格递增", all(vs[i] < vs[i+1] for i in range(len(vs)-1)))
gaps = set(round(vs[i+1]-vs[i], 9) for i in range(len(vs)-1))
chk("v 等距", gaps == {0.1}, gaps)
chk("v 范围对称覆盖半幅", abs(vs[0] + vs[-1]) < 1e-9, (vs[0], vs[-1]))

print("\n结论:", "全部通过 ✓" if all(results) else "存在问题 ✗  (%d/%d)" % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
