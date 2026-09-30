# CRG 路面模型生成

由库中真实线形/高程数据生成 ASAM OpenCRG 路面模型，供 Project Chrono
`CRGTerrain` 使用。详细方法与格式陷阱见
[`specs/002-station-baseline-geometry/实现-CRG路面模型生成.md`](../../../specs/002-station-baseline-geometry/实现-CRG路面模型生成.md)。

## 数据来源

| 表 | 行数 | 用途 |
|---|---|---|
| `alignment_element` | 33 | 平面线形（9 直线 + 16 缓和曲线 + 8 圆曲线） |
| `profile_grade_point` | 12 | 纵断面竖曲线 VPI |
| `roadbed_design_point` | 332 | 设计高程 + 车道/路肩宽度（20 m 间隔） |

## 产物

| 文件 | 内容 |
|---|---|
| `route_0p1m.crg` | OpenCRG 路面：58,055 点 × 86 截面，纵向 0.1 m |
| `route_surface_lite.obj` | 抽稀三角网格（1 m，~25 万顶点），供可视化 |
| `route_surface.obj` | 全分辨率三角网格（~500 万顶点，约 400 MB） |
| `route_centerline.csv` | 中心线（含桩号/坐标/高程/方位角） |
| `route_edges.csv` | 左右边界（Bezier 控制点来源） |

## 脚本

### 沙箱内可跑（纯 CPU，无需 GL）

```bash
python3 gen_crg.py                 # 生成 route_0p1m.crg（可用 CRG_OUT=... 改路径）
python3 validate_crg.py            # 按官方 loader 源码规则校验（16 条）
python3 export_mesh.py             # 导出网格 / 中心线 / 边界

# 用官方 OpenCRG 库实读验证
gcc -O2 -I$OPENCRG/include -o crg_read_test crg_read_test.c \
    $OPENCRG/lib/libOpenCRG.a -lm
./crg_read_test route_0p1m.crg
```

三个 Python 脚本都接受可选的 `.crg` 路径参数（默认 `route_0p1m.crg`），
且只依赖 Python 标准库（`export_mesh.py` 亦然），可在任意目录直接运行。

### 宿主侧（需要 GPU / 显示）

```bash
./01_build_opencrg.sh                        # 编 OpenCRG v1.1.2
./02_build_chrono.sh                         # 编 Chrono（Vehicle + OpenCRG + VSG）
./04_run_visualize.sh                        # 开窗可视化（mesh 模式）
./04_run_visualize.sh --boundary             # 边界曲线模式
```

## ★ 生成 .crg 时必须遵守的 5 条格式规则

均依据官方 `c-api/baselib/src/crgLoader.c`。**违反任何一条都会被静默拒绝。**

1. 数据段起始标记是 **`$$$$`**，不是 `$KD_DEFINITION`
2. `$$$$` **之前必须有一个单独的 `$` 行**（`setSection()` 要求当前段为 None 才切换）
3. ASCII 必须用 **`K`**(compact)，不能用 `L`(long)（`L` 强制定长 80 字节）
4. ASCII **每字段定宽 20 字符**（`decodeRecord` 按 20 字符硬切，不按空白分词）
5. `U:` 行是声明性的空操作，u 轴实际来自头段关键字

正确骨架：

```
$ROAD_CRG
REFERENCE_LINE_START_U = 0.000000
...
$
$KD_DEFINITION
#:KDF
U:reference line u,m,0.000,0.100
D:reference line phi,rad
D:long section at v = -4.250,m
...
$
$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$
<每行 20 × 通道数 字符>
```

## 线形重建要点

- `phi` 通道存**绝对方位角（弧度）**，由 loader 积分出 x/y
- 段内按**曲率线性**积分：`az(s) = az0 + k0·s + (k1−k0)·s²/(2L)`
- **缓和曲线 `Δaz = L/(2R)`，不是 `L/R`**
- 曲率列无符号，方向由 Δazimuth 判定；半径列只填一端
- 采样轴用**连续里程**（0 → 5805.421），逐段取整会丢 1 点

## 依赖版本

**必须用 hlrs-vis/opencrg v1.1.2** —— Chrono 官方 `buildOpenCRG.sh` 硬编码该版本。
装到仓库外（`$HOME/Packages/openCRG`），保持本仓库干净可分发。

## 已知限制

`CRGTerrain` 类参考明确说明：CRG 路面**不含碰撞/接触信息**，只能配
半经验轮胎模型（ChTMeasy / ChPac89 / ChPac02 / ChFiala）。要做到可变形路面
必须换路面表示。

本开发沙箱无 `/dev/dri`、无 GPU 设备节点、capabilities 全 0、`sudo` 被
`no-new-privileges` 禁用，**任何 GL 渲染都不可用**。生成/校验/导出/官方库读取
均可在沙箱内完成；**可视化窗口必须在宿主侧运行**。
