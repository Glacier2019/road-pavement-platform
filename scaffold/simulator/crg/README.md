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

### 给前端的抽稀网格（`export_viewer_mesh.py` → 交付目录）

```bash
python3 export_viewer_mesh.py route_0p1m.crg --out ../../../artifacts/sim
```

96.4 MiB 的 ASCII CRG **不能**直接进浏览器，抽稀成两级 LOD 写进交付目录
`artifacts/sim/`（M9 以只读方式挂载消费）：

| 产物 | u 步长 | 顶点 | 三角 | 大小 |
|---|---|---|---|---|
| `terrain_overview.bin` | 4.0 m | 1453 × 9 = 13077 | 23232 | 289 KB |
| `terrain_near.bin` | 1.0 m | 5807 × 9 = 52263 | 92896 | 1157 KB |

**★ 这个脚本不需要带号，也不需要数据库。** CRG 是曲线坐标：数据行里第一个字段
就是**方位角 φ（弧度）**，后 86 个是各 v 通道的高程，`u` 是隐含的。
于是 `X(u) = ∫cos φ du`、`Y(u) = ∫sin φ du` —— 纯局部帧积分。
第一行 `2.6086571868` rad = 149.46°，最后一行 `2.0462012385` rad = 117.24°，
与独立测得的起终点方位角一致，**这就是它可信的证据**。

**★ 端点自检会硬失败（退出码 11），只打印警告的自检等于没有自检。**
实测积分得 `(-4521.325, 3120.210)`，OpenCRG 给 `(-4521.122, 3119.874)`，
差 **0.393 m**（容差 5 m）。第四种独立实现（DB `alignment_element`）给
`(-4521.335, 3120.229)` —— 四种方法互相对上了，所以**不改数据**。

**★ v 采样不是均匀的**，9 个点落在物理上有意义的位置
（`±4.25` 路面边缘、`±3.50` 车道边、`±2.625`、`±1.75` 车道中心、`0` 中线），
这样网格的边就是路面和车道的边。**高程用线性插值而不是取最近通道** ——
通道在 `±3.45/±3.55`，取最近会让车道边差 50 mm。

**★ 退出码**：`0` 成功 / `10` CRG 不存在 / `11` 端点自检超容差（**什么都不写**）/
`12` 顶点数 ≥ 65536（**什么都不写**，否则 `uint16` 索引会静默截断）。

同时写 `manifest.json`：清单即**白名单**，M9 的 `/artifacts/{name}` 只认登记过的名字。
`bytes` 是**从磁盘量的**，不是脚本里算的。契约在
`scaffold/contracts/delivery/artifact_manifest.v0.1.schema.json`。

### 线形核对图（需要 numpy + matplotlib）

```bash
# 先把设计线形表导出到本目录（*.csv 被 gitignore，不随仓库分发）
docker exec rp-pg psql -U rp -d road_pavement -t -A -F',' -c "
SELECT element_seq, element_type, start_station_km, end_station_km,
       start_x, start_y, end_x, end_y, azimuth_deg, end_azimuth_deg,
       coalesce(radius_start_m,-1), coalesce(radius_end_m,-1)
FROM alignment_element WHERE section_id=6 ORDER BY element_seq;" > alignment_element.csv

PYTHONPATH=/data/cy/shujuku/.pylibs python3 plot_alignment.py
```

`plot_alignment.py` 把 `alignment_element` 表按方位角积分还原成平面几何，
画成 `route_0p1m_plan.png`（平面线形 + 圆曲线半径 + 线形组成），并做**两项自校验**：

1. 33 个单元的积分终点 vs 数据库 `end_x/end_y` → 实测最大偏差 **0.0007 m**；
2. 设计坐标 → `.crg` 局部坐标的换算 vs 官方 OpenCRG 库读出的末点 → 相差 **0.414 m**。

图上还标了旧相机位置及其 **500 m 远裁剪面**（见坑六），一眼能看出改前只看得见
多大一片。

### 宿主侧（需要 GPU / 显示）

**先装前置，否则下面四个脚本会依次失败**（实测踩过，见文末「三个会静默失败的坑」）：

```bash
# 1) 编译工具与 GL 头文件（02 脚本会逐个 dpkg -s 检查，缺一个就退出）
sudo apt-get install -y ninja-build cmake g++ curl unzip \
  libgl1-mesa-dev libglu1-mesa-dev libx11-dev libxext-dev \
  libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev libxxf86vm-dev \
  libeigen3-dev \
  fonts-noto-cjk

# 2) Vulkan 开发环境 —— 二选一
#    (a) 发行版包（快，推荐先试）
sudo apt-get install -y libvulkan-dev vulkan-tools glslang-tools
#    (b) LunarG SDK（官方推荐，任何发行版都可用）
#        从 https://vulkan.lunarg.com/sdk/home#linux 下 tarball，
#        解压后 source 其 setup-env.sh（VULKAN_SDK 即生效）

# 3) 确认 GPU 驱动真的能跑 Vulkan（VSG 是 Vulkan 后端，不能纯软件）
vulkaninfo --summary
```

> ★ **`libeigen3-dev` 不是可选项，而且缺了以后报错方式极其误导。**
> 它不是 GL 那套的附庸，是 Chrono 核心的硬依赖——缺了它 configure 会
> **退出码 0 地**配置出一个不含任何模块的构建。详见文末「坑二」。

> ⚠ **不要用 LunarG 的 apt 源**除非你确认自己的发行版代号在其中。
> 实测 `packages.lunarg.com/vulkan/lunarg-vulkan-<代号>.list` 只覆盖部分
> Ubuntu 代号（jammy / noble 有，26.04 的 **resolute 没有**），
> 且官方给的命令带 `wget -q`——**404 会被静默吞掉**，
> 造成"装好了"的假象，一路带到 configure 阶段才炸。

然后：

```bash
./01_build_opencrg.sh        # ①-1 编 OpenCRG v1.1.2（纯 CPU，可在任何机器上跑）
./01b_build_vsg.sh           # ①-2 编 VSG 全家桶（Vulkan 后端，需 GPU + Vulkan 开发环境）
./02_build_chrono.sh         # ①-3 编 Chrono（Vehicle + OpenCRG + VSG）
./04_run_visualize.sh        # ①-4 开窗可视化（mesh 模式）
./04_run_visualize.sh --boundary    # 边界曲线模式
```

### 车开上路面（`05_vehicle_on_crg.cpp` → `demo_vehicle_on_crg`）

```bash
# 跑完 5805.4 m 全程，并导出轮荷时间序列（无窗口，约 6 分钟）
demo_vehicle_on_crg route_0p1m.crg --headless --speed 15 --duration 600 \
  --csv wheel_loads.csv --csv-dt 0.05

# 开窗看车跑（每步渲染；只在交互时用，别拿它跑全程）
demo_vehicle_on_crg route_0p1m.crg --speed 15 --duration 60

# 抓帧录视频（详见下面的「做视频」一节）
demo_vehicle_on_crg route_0p1m.crg --speed 15 --duration 600 \
  --video frames --video-dt 0.5 --video-size 1280x720
./make_video.sh frames vehicle_on_crg.mp4 20
```

| 参数 | 默认 | 含义 |
|---|---|---|
| `<crg文件>` | 必填 | 路面文件 |
| `--speed` | 15 | 目标车速 m/s |
| `--offset` | **-1.75** | 相对**路中线**的横向偏移 m（**正 = 左**）。默认 -1.75 = **靠右行驶**，即右侧 3.5 m 车道的中心线 |
| `--headless` | 关 | 不建窗口，全速跑 |
| `--duration` | 600 | 最长仿真时长 s（到达终点会提前结束） |
| `--step` | 0.002 | 动力学步长 s |
| `--csv` | 空 | 轮荷时间序列输出路径；空 = 不导出 |
| `--csv-dt` | 0.05 | 轮荷采样间隔 s |
| `--video` | 空 | 抓帧输出目录；空 = 不抓帧。**与 `--headless` 互斥** |
| `--video-dt` | 0.2 | 抓帧间隔（仿真时间 s） |
| `--video-size` | 1280x720 | 抓帧分辨率 |
| `--render-dt` | 0 | 渲染节流（仿真时间 s）；0 = 每步都渲染 |
| `--no-texture` | 关 | 不挂路面贴图（排障用） |
| `--no-sky` | 关 | 不挂天空穹顶（排障用） |
| `--no-shadows` | 关 | 不投硬阴影（降负载） |
| `--no-markings` | 关 | 不画车道标线（排障用） |
| `--pbr` | 0 | 贴图档位：`0` 只漫反射 / `1` +法线 / `2` +法线+粗糙度 |

**为什么默认是 -1.75**：这是**二级公路**，横断面直接读自设计表
`roadbed_design_point`（`section_id = 6`）——左车道 3.5 + 右车道 3.5 +
左硬路肩 0.75 + 右硬路肩 0.75 = **8.5 m**，与 CRG 报的 v 覆盖宽度一致；
中央分隔带为 0。右侧车道中心距路中线 1.75 m，而 `--offset` **正 = 左**
⇒ 靠右就是 **-1.75**。

★ 默认值本身就是一个物理断言。原来默认 `0` = 压着中线跑，等于悄悄
声称"这车在对向车道上"，没有任何设计文件支持它。

**轮荷 CSV 有 21 列**：`t_s, s_m, lat_m, speed_mps, chassis_z_m`，
后面接四个轮子 × `Fz_kN, Fy_kN, Fx_kN, pz_m`（顺序 `a0_L, a0_R, a1_L, a1_R`）。

> ★ `pz_m` 是**接触点的高程**，不是冗余列。`Fz = 0` 有两种含义——
> 「没有接触」和「载荷为零」——Chrono 的 `TerrainForce` 没有接触标志位，
> 唯一能区分的办法就是看 `pz`：**同时为 0 ⇒ 没有接触**。
> 详见「坑十三」。

### 做视频

**整段录制（推荐）** —— 带自动重试，崩了就重录：

```bash
./capture_full_video.sh /path/to/输出目录 4    # 第 2 个参数是重试次数，默认 4
```

为什么要重试：路面贴图会让渲染进程**不确定地**段错误（见「坑十九」）。
脚本把"崩了就换目录重录"自动化，直到帧数 ≥ 780 才认成功。
退出码 `0` = 录成，`20` = 重试次数用尽，`10` = 参数/二进制问题。

**手工分两步**：

```bash
# 1) 抓帧。★ 必须先确认这轮能接受 1280x720 的分辨率与时长（见「坑十四」）
demo_vehicle_on_crg route_0p1m.crg --speed 15 --duration 600 \
  --video frames --video-dt 0.5 --video-size 1280x720 --pbr 0 --no-shadows

# 2) 合成。帧率 = (1 / 抓帧间隔) × 倍速
./make_video.sh frames vehicle_on_crg.mp4 20     # 0.5 s 间隔 + 20 fps = 10 倍速
```

`--video` 会自动打开 `SimplifyMesh`（v 通道 86 → 5，面数除以 17），
否则软件渲染下是 **13 s/帧**、根本录不成视频。它只改画出来的网格，
**物理不受影响**。实测简化后约 **0.95 s/帧**（含抓帧）。

播放倍速的换算：`fps = (1 / --video-dt) × 倍速`。
`--video-dt 0.5` 配 `fps 20` 就是 10 倍速，5.8 km 的路约 40 秒看完；
配 `fps 2` 就是实时。

`make_video.sh` 会**核对帧号连续性**——中途缺一帧的话 ffmpeg 会静默
把它当成序列结束，成片少一大截且不报错，所以这一步必须在合成前拦下来。

**默认建议 `--pbr 0 --no-shadows`**：漫反射贴图 + 天空穹顶已经能把
画面从"平涂色块"救回来（唯一色数 1919 → 11076，最大单色占比
49.65 % → 3.79 %，见「坑十六」），而法线/粗糙度与硬阴影只增加
驱动侧风险，对"看得清路和车"帮助有限。


`01b_build_vsg.sh` 会打印它选中的 Vulkan 来源（`LunarG SDK` 或 `系统包`），
两条路 VSG 的 `find_package(Vulkan REQUIRED)` 都认。

**必须先装 Vulkan SDK 与 `ninja-build`**：Chrono 的 `chrono_vsg` 用裸
`find_package(vsg 1.1.0 REQUIRED)`，不会自动下载。官方锁定版本为
VulkanSceneGraph v1.1.15 / vsgXchange v1.1.12 / vsgImGui v0.7.0。
`01b_build_vsg.sh` 直接复用 Chrono 官方的 `buildVSG.sh` 以避免版本漂移。

CMake 选项名均已对照官方源码核对：

| 选项 | 出处 |
|---|---|
| `CH_ENABLE_MODULE_VEHICLE` / `CH_ENABLE_OPENCRG` | `src/chrono_vehicle/CMakeLists.txt` |
| `CH_ENABLE_MODULE_VSG` | `src/chrono_vsg/CMakeLists.txt` |
| `BUILD_DEMOS` / `BUILD_TESTING` | `src/CMakeLists.txt` |

链接目标名是 `Chrono::vehicle` / `Chrono::vsg`（下划线，非驼峰），
见 `add_library(Chrono_vehicle)` + `ALIAS Chrono::vehicle`。

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

### 这条长缓弯路会被 OpenCRG 判成"可闭合"（上游启发式，关不掉）

`CRGTerrain::IsPathClosed()` 不是读文件里的标志，而是取 OpenCRG 的
`crgDataSetGetUtilityDataClosedTrack()`。那个值由 `crgStatistics.c:258-350`
的**几何猜测**算出，规则是：

> 起点航向与终点航向夹角 **< 60°**（`divisor = cos(Δφ) > 0.5`），
> 且两条延长线的交点落在合适位置 → `uIsClosed = 1`。

我们这条路首尾航向只差 **32.2°**（149.465° → 117.239°），于是中招。
注意它**不是**一段圆弧：按 0.5 m 等距积分 5805.4 m 实测——

| 指标 | 值 | 说明 |
| --- | --- | --- |
| 净转角 | 32.2° | 首尾航向差，**闭合误判的直接触发条件** |
| 累计转角 Σ\|Δaz\| | 264.0° | 全程弯的量 |
| 转向翻转次数 | **15** | 左右来回摆 15 次 = 盘山路 |
| 路线长 / 弦长 | 1.0568 | 弯道附加系数 5.7% |

（`plot_alignment.py` 会把这四项连同平面图一起算出来。）
探针实测（`.crg` 直接读）：

```
start at x/y:      0.0000 / 0.0000      with phi [rad]: 2.6087   (149.5°)
end   at x/y:  -4521.3053 / 3120.2292   with phi [rad]: 2.0462   (117.2°)
crgCalcUtilityData: lines are (almost) parallel
crgCalcUtilityData: reference line may be closed:
crgCalcUtilityData: uMin = 0.000,  uMinClosed = -2260.653
crgCalcUtilityData: uMax = 5805.400, uMaxClosed = 8066.053
```

注意日志原文是 **"may be closed"**，而 `uCloseMin/Max` 落在路面范围**之外**
——它的意思是"这条路往前接 2260 m、往后接 2260 m 就能闭合成环"，是个纯粹的
猜测。**任何长而缓弯的路都会触发。**

**文件层面关不掉**：`dCrgRefLineCloseTrack` 是"请求闭合"选项，而
`crgLoader.c:2455` 规定已判闭合的路不许再请求闭合，直接 `FATAL`。
没有反向开关。

**后果**（Chrono 直接采信 `m_isClosed`，两处都会动）：

- `GenerateMesh`（`CRGTerrain.cpp:486` / `520`）：整排 `i == nu-1` 的顶点被
  换成第 0 排 → 路末端多出一片跨 **5495 m** 的假面。实测导出的 `.obj`：
  464,432 个面里**只有 8 个**面边长 > 100 m（中位边长 4.19 m），正是它。
  `8 = 2 × (nv-1)`，`nv = 5`（`SimplifyMesh(true)` 把 `m_v` 压成 5 个值）。
- `GenerateCurves`（`CRGTerrain.cpp:409-411`）：`pl.back() = pl[0]` →
  边界曲线也多一段跨 5.5 km 的收尾。**所以 `--boundary` 并不能躲开**，
  只是把"8 个面"换成"2 条线"。

**当前处置**：`03_visualize.cpp` 把这件事**打印出来**而不是当事实报，
并给出一个真事实（中心线首尾控制点跨度 4521 m）供对照。
要彻底消掉，得在消费侧自建网格（不用 `UseMeshVisualization(true)`），
那是下一步的事。

## ★ 六个会静默失败的坑（实测，均已修）

这三个都不是"报错"，而是**看起来成功、实际残缺**。凡在宿主上跑这几个脚本，
先读这一节，能省一次 30 分钟的无效编译。

### 坑一：vsgImGui 缺子模块 → `致命错误：不是 Git 仓库`

**症状**（埋在几千行输出中间，末尾还会打印"完成"）：

```
------------------------ Configure vsgImGui
致命错误：不是 Git 仓库（或者任何父目录）：.git
CMake Error at CMakeLists.txt:39 (message):
  git submodule update --init --recursive failed with 128
ninja: error: loading 'build-Release.ninja': No such file or directory
```

**根因**：`01b` 用 codeload tarball 取源码（本机 `github.com` 的 git 协议
完全不通），而 tarball 里**没有 `.git`**。vsgImGui v0.7.0 的 CMakeLists
第 25-43 行判断"子模块在不在"用的是**纯文件存在性检查**：

```cmake
if ( (NOT EXISTS src/imgui/imgui.h) OR (NOT EXISTS src/implot/implot.h) )
    execute_process(COMMAND git submodule update --init --recursive ...)
    if(NOT GIT_SUBMOD_RESULT EQUAL "0")
        message(FATAL_ERROR "... please checkout submodules")
```

tarball 会建出 `src/imgui`、`src/implot` 两个**空目录**（内容不在包内），
判断成立 → 去调 `git` → 没有 `.git` → FATAL_ERROR。

**修法**：既然判断只看文件，就把两个子模块按 v0.7.0 钉死的 commit 填进去，
它连 `git` 那一行都不会执行。commit 取自 `api.github.com` 的 `git/trees`
端点（本机 git 协议不通，这是唯一可靠来源）：

| 子模块 | commit |
|---|---|
| `ocornut/imgui` | `993fa347495860ed44b83574254ef2a317d0c14f` |
| `epezent/implot` | `f156599faefe316f7dd20fe6c783bf87c8bb6fd9` |

**这一步不能并进 `fetch_src`**：`fetch_src` 见到 `CMakeLists.txt` 就跳过，
而 vsgImGui 目录在上一轮已经下好了——"复用旧目录"恰恰就是出问题的场景。

这不是可选项：Chrono 的 `src/chrono_vsg/CMakeLists.txt` 第 31 行是
`find_package(vsgImGui REQUIRED)`，缺了它 `02_build_chrono.sh` 必定失败。

### 坑二：缺 Eigen3 → configure 退出码 0，但一个模块都不编

**症状**——本项目见过最会骗人的一种：

```
ERROREigen3 cannot be found.
  Provide Eigen3_DIR (location of Eigen3Config.cmake) or else ...
-- Configuring done (1.4s)          ← 看着一切正常
-- Generating done
CMake Warning:
  Manually-specified variables were not used by the project:
    CH_ENABLE_MODULE_VEHICLE  CH_ENABLE_MODULE_VSG  CH_ENABLE_OPENCRG ...
configure 退出码: 0                 ← 成功退出
```

**根因**：Chrono 的 `src/CMakeLists.txt` L126-149：

```cmake
find_package(Eigen3 5.0 QUIET)
if(NOT Eigen3_FOUND)
   find_package(Eigen3 3.3 QUIET)
endif()
if(Eigen3_FOUND) ...
else()
  message(ERROR "Eigen3 cannot be found.\n" ...)   # ← 不是 FATAL_ERROR
  return()                                          # ← 顶层 return()
endif()
```

`message(ERROR ...)` 在 CMake 里**不是致命错误**，只打印一行带 `ERROR`
前缀的字样；紧接着的 `return()` 在顶层 CMakeLists 里会**终止该文件后续
所有内容** —— 包括 L535-561 那一串 `add_subdirectory(chrono_vehicle /
chrono_vsg / ...)`。

**后果**：没有 Eigen3 时 Chrono 会"成功地"配置出一个**不含任何模块**的
构建，编译 30 分钟只得到 core，然后 `04_run_visualize.sh` 在完全无关的
地方报错。`set -e` 拦不住它——退出码是 0。

**修法**：`02_build_chrono.sh` 加了三道防线——依赖门查 `libeigen3-dev`；
configure 输出落盘并断言 `Eigen3 found` 且无 "not used" 警告；收尾断言
`Chrono_vehicle` / `Chrono_vsg` / `CRGTerrain.h` **真的装出来了**。

> 为什么收尾要查"装出来没有"：原来那句 `ls 源码里的 CRGTerrain.h` 是
> **恒真**的——那个文件在 tarball 里必然存在，无论模块有没有被编。等于没查。

### 坑三：OpenCRG 少了 `-fPIC` → 编到 67% 才在链接共享库时炸

**症状**（Chrono 已经编了 20 多分钟，一路绿灯）：

```
[ 67%] Linking CXX shared library ../../lib/libChrono_vehicle.so
/usr/bin/x86_64-linux-gnu-ld.bfd: libOpenCRG.a(crgLoader.o):
    relocation R_X86_64_PC32 against symbol `mCrgBigEndian'
    can not be used when making a shared object; recompile with -fPIC
/usr/bin/x86_64-linux-gnu-ld.bfd: final link failed: bad value
collect2: error: ld returned 1 exit status
```

**根因**：Chrono 默认把模块编成**共享库**（`libChrono_vehicle.so`），而
`libOpenCRG.a` 里的目标文件**不是位置无关代码**。报错里出现的是
`crgLoader.o` 和 `mCrgBigEndian`——跟"少了个编译选项"看起来毫无关系，
这是这个坑最难认的地方。

`01_build_opencrg.sh` 原先走的是 OpenCRG **自带的 makefile**，而那个
makefile 的 `CFLGS` 里**没有 `-fPIC`**。更糟的是：这个 `.a` 编得出来、
装得上、`ls` 一切正常，**要等到几十分钟后链接共享库时才暴露**。

**修法**：改成官方 `buildOpenCRG.sh` 的**直接 gcc** 路线——它根本不碰那个
makefile：

```bash
gcc -Wall -O3 -fPIC -I$SRC/inc -c $SRC/src/*.c
ar -r $INSTALL_DIR/lib/libOpenCRG.a *.o
```

顺带说明一件事：原先 `01` 里那句 `-ansi` → `-std=gnu99` 的 `sed`
**整条都不要了**。`-ansi`（=C90）与源码里的 `//` 注释冲突，是 **makefile
路线独有的问题**；官方直编从来不需要 `-std`。沙箱实测：官方旗标在
gcc 15.2 上 11/11 全部编过，加不加 `-std=gnu99` 都能过。

**并且加了探测**（`01` 与 `02` 各一道）。因为"编得出来"不代表"能链"，
所以不做静态检查，而是**真的去链一次**——用 `--whole-archive` 把 `.a` 的
每个成员都强行拉进一个共享库：

```bash
gcc -shared -o /dev/null \
  -Wl,--whole-archive libOpenCRG.a -Wl,--no-whole-archive -lm
```

只要有一个 `.o` 不是 PIC，ld 就会报出**与上面逐字相同**的错误。这个探测
经过双向验证：带 `-fPIC` 的包放行，不带 `-fPIC` 的包报出同一句
`recompile with -fPIC`。**一两秒**换掉一次 30 分钟的无效编译。

`02` 里那道是给"跳过了 `01`、用的还是上次留下的旧 `.a`"兜底的。

### 坑四：核对"生产者"不等于核对"消费者"（目标名 / 命名空间 / 方法名）

前三个坑都在**构建侧**（`01`/`01b`/`02`），这第四个在**消费侧**——
`04_run_visualize.sh` 的 CMake 与 C++。共同点是：拿官方源码或文档当依据，
而**源码里的名字和装出来的名字不是一回事**。

**(1) 目标名。** 源码 `src/chrono_vehicle/CMakeLists.txt:985` 确实写着

```cmake
add_library(Chrono::vehicle ALIAS Chrono_vehicle)
```

但 **ALIAS 目标永远不会被 `install(EXPORT ...)` 导出**。装出来的
`lib/cmake/Chrono/ChronoTargets.cmake` 里，导出的真名是这八个：

```
Chrono::Chrono_core            Chrono::Chrono_vsg
Chrono::Chrono_vehicle         Chrono::Chrono_vehicle_vsg
Chrono::Chrono_vehicle_cosim   Chrono::ChronoModels_vehicle
Chrono::ChronoModels_robot     Chrono::yaml
```

实测 `grep -c 'Chrono::vehicle\b' ChronoTargets.cmake` → **0**。
所以消费侧必须写 `Chrono::Chrono_vehicle` / `Chrono::Chrono_vsg`。
原注释写的是"目标名已核对官方源码"——**对生产者核对过了，对消费者没核对**。

**(2) 命名空间。** `ChVisualSystemVSG` 在 **`chrono::vsg3d`**，不在 `chrono`
（`include/chrono_vsg/ChVisualSystemVSG.h:47`）。而 `CameraVerticalDir`
在 `chrono`（`include/chrono/assets/ChVisualSystem.h:42`）。

**(3) 方法名。** `EnableAbsFrameCoords()` **根本不存在**——是照着"应该有"
编出来的。真名是 `SetAbsFrameScale(double)` 与 `ToggleAbsFrameVisibility()`
（`ChVisualSystemVSG.h:166-167`），而且后者**只有切换形式、没有显式 bool
重载**（默认 `m_show_abs_frame(false)`，`ChVisualSystemVSG.cpp:322`，
所以 `Toggle` 一次即打开）。

**修法**：`CMakeLists.txt` 与 `03_visualize.cpp` 里，每个用到的成员都标上
**头文件行号**；方法名一律以**装出来的头文件**为准，文档链接只能当索引、
不能当签名。写"非猜测"三个字之前，先真的 `grep` 一遍。

**(4) 附带一个。** `CRGTerrain::ExportMeshWavefront` **不建目录**。
`CRGTerrain.cpp:572-575` 就是

```cpp
ChTriangleMeshConnected::WriteWavefront(out_dir + "/" + m_mesh_name + ".obj", meshes);
```

目录不存在时 `WriteWavefront` 只打印一句 `Unable to create output .OBJ file`
就返回，而原来的 `03_visualize.cpp` **紧接着照样打印「已导出网格」**——
又一次"报告成功但没成功"。现在改成先 `create_directories`，写完再核实
文件真的存在、并打印字节数。

### 坑五：Chrono 数据目录 —— 开窗阶段的两次段错误

**症状**：编译、链接、CRG 读取、网格导出、CSV 全部正常，最后开窗那一步
`段错误（核心已转储）`，退出码 139。`04_run_visualize.sh` 只报"第 67 行段错误"，
看不出崩在哪。

**根因一：`GetChronoDataPath()` 的默认值是 `"../data/"`。**

`chrono/core/ChDataPath.cpp` 里就这一句：

```cpp
static std::string chrono_data_path("../data/");
```

**没有任何东西会自动改它** —— 是**应用程序**的责任。`ChVisualSystemVSG`
构造时把它塞进 VSG 的搜索路径：

```cpp
m_options->paths.push_back(GetChronoDataPath());
```

于是 `Initialize()` 拿它去读 `vsg/fonts/OpenSans-Bold.vsgb`，相对**当前工作
目录**，读不到，打印

```
Failed to read font : vsg/fonts/OpenSans-Bold.vsgb
```

然后**直接 `return`** —— 此时窗口和 viewer 都还没建。而 `Run()` 的实现就是

```cpp
bool ChVisualSystemVSG::Run() { return m_viewer->active(); }
```

空指针解引用 → 段错误。

**我原先把 `Failed to read font` 当成"无害且不可避免的警告"写进了本 README。**
它不无害 —— 它是整条崩溃链的起点。

**根因二：`lexically_normal()` 会吃掉结尾斜杠，而 `GetChronoDataFile` 是字符串拼接。**

第一版修复我写成

```cpp
SetChronoDataPath(std::filesystem::absolute(CHRONO_DATA_DIR, ec).lexically_normal().string());
```

`lexically_normal()` 把 `.../share/chrono/data/` 规范成 `.../share/chrono/data`。
而 `GetChronoDataFile` 是

```cpp
return chrono_data_path + filename;
```

拼出来是 `.../chrono/datalogo_chrono_alpha.png`。`ChMainGuiVSG` 构造时读不到 logo：

```cpp
auto texData = vsg::read_cast<vsg::Data>(m_app->m_logo_filename, options);
m_app->m_logo_texture = vsgImGui::Texture::create_if(texData, texData);  // texData 空 → 返回空
```

紧接着 `compile()` 的第一句无条件解引用：

```cpp
m_app->m_logo_texture->compile(context);   // → 段错误
```

**崩在哪，靠反汇编确认**（`ChMainGuiVSG::compile` 前 30 字节）：

```
+16:  mov 0x18(%rdi),%rax      ; rax = this->m_app
+20:  mov 0x5c0(%rax),%rdi     ; rdi = m_app->m_logo_texture
+27:  mov (%rdi),%rax          ; rdi = 0 → 段错误
+30:  call *0x88(%rax)         ; Texture::compile
```

**★ 这里最值得记的是自检本身错了。** 我加的那道"字体就位"检查**通过了**，
因为它用的是

```cpp
std::filesystem::path(GetChronoDataPath()) / "vsg/fonts/OpenSans-Bold.vsgb"
```

`operator/` 会自动补斜杠，而真正出事的那条路径用的是字符串拼接。
**又一次用另一种方法验证了被测代码。** 现在两道自检都改走真实函数
（`GetChronoDataFile`），字体和 logo 一起查。

**正确写法**（官方同款见 `chrono/template_project/CMakeLists.txt:107`）：

```cmake
target_compile_definitions(demo_CRG_visualization PRIVATE
  "CHRONO_DATA_DIR=\"${CHRONO_DATA_DIR}\"")
```

```cpp
std::string data_dir = CHRONO_DATA_DIR;
if (data_dir.empty() || data_dir.back() != '/') data_dir += '/';   // ★ 斜杠不能丢
SetChronoDataPath(data_dir);
```

**判据**：沙箱里 `timeout 25 ./demo_CRG_visualization route_0p1m.crg 0`
退出码 **124**（窗口开着没崩）即通过；修之前是 **139**。

**顺带一条备用路**：万一将来 Chrono 可视化系统本身出问题，
`chrono_export/route_0p1m_mesh.obj` 可以直接用官方 `vsgviewer` 打开
（纯 VSG，绕开 Chrono），实测能开窗。

### 坑六：Chrono 把远裁剪面写死 500 m —— 5.8 km 的路只看得见开头一小截

**症状**：窗口能开、不崩、路面也在，但**怎么感觉只有一小段圆曲线**。
第一反应会怀疑"是不是路只生成了这么点"——不是。`GetLength()` 报 5805.4 m，
导出的 `.obj` 也是满的。**是渲染时被裁掉了。**

**根因**（`chrono_vsg/ChVisualSystemVSG.cpp`，实测非推测）：

```cpp
:709    double radius = 50.0;          // ← 全文件再无第二次赋值
:710    vsg::dbox bound;               // ← 声明了，一次都没用过（死代码）
...
:843    double nearFarRatio = 0.001;
:844    auto perspective = vsg::Perspective::create(m_camera_angle_deg, width/height,
:845                                    nearFarRatio * radius,   // near = 0.05 m
:846                                    radius * 10.0);          // far  = 500 m
```

本该由场景包围盒算出 `radius` 的那段代码**没写**，于是 `radius` 永远是 50，
远裁剪面永远是 **500 m**。程序自己会把这两个数打出来，可以当场核对：

```
    原裁剪面 near=0.05 far=500
```

**为什么官方 demo 不炸**：`demo_VEH_CRGTerrain_VSG.cpp` 用的是
`ChWheeledVehicleVisualSystemVSG`，跟着车走、只看前方百米，500 m 绰绰有余。

**为什么没有接口**：`ChVisualSystemVSG.h` 里跟相机有关的公开 setter 只有
`SetCameraAngleDeg(double)`，**没有** `SetNearFarClip` 之类，也没有 `GetCamera()`。

**绕法**（`03_visualize.cpp` 里已实现，实测有效）：相机挂在命令图里，
顺着公开的 `GetRenderCommandGraph()` 就能摸到，改 `vsg::Perspective` 的两个
**public 字段**即可，`lookAt` / 轨迹球完全不受影响：

```cpp
// vsg::createRenderGraphForView() 会挂一个 View::create(camera)
//   —— VSG 源码 src/vsg/app/RenderGraph.cpp:221
// View::camera -> Camera::projectionMatrix
//   —— include/vsg/app/View.h:67 / Camera.h:34
// vsg::Perspective 在 include/vsg/app/ProjectionMatrix.h（没有单独的 Perspective.h）
//   —— nearDistance / farDistance 都是 public 可写
auto* p = dynamic_cast<vsg::Perspective*>(v->camera->projectionMatrix.get());
p->nearDistance = 1.0;
p->farDistance  = 100000.0;
```

**为什么改了立刻生效、不用重建对象**：投影矩阵在**每次录制命令图时**重新读取
—— `src/vsg/app/RecordTraversal.cpp:613`：

```cpp
state->setProjectionAndViewMatrix(view.camera->projectionMatrix->transform(), ...);
```

**两个实现细节（都会编译失败，别踩）**：

- `vsg::ref_ptr` **不是** `std::shared_ptr`，VSG 也没提供 `dynamic_pointer_cast`。
  写 `std::dynamic_pointer_cast<vsg::View>(ref_ptr)` 会报
  `no matching function for call to 'dynamic_pointer_cast<vsg::View>(const vsg::ref_ptr<vsg::Node>&)'`。
  **正确做法是在裸指针上 `dynamic_cast`**：`dynamic_cast<vsg::View*>(n.get())`。
- 相机顺带要一起改的还有**取景**：原来写死 `AddCamera((-120,-260,120), (0,0,0))`，
  眼位离路起点只有 306 m。现在按中心线包围盒自动算：取 `lo/hi` 中心为目标，
  `dist = 1.5 × 跨度`，眼位放在 `(-0.55, -0.70, +0.45) × dist` 方向。

**判据**：运行输出里出现

```
==> 路面范围 X[...] Y[...]  跨度 4519.93 m
==> 相机 眼(...) -> 目标(...)  距离 6762.93 m
    原裁剪面 near=0.05 far=500
==> 远裁剪面已改 near=1 far=100000 m（命中 1 个相机）
```

若出现 `!! 没在命令图里找到相机`，说明 VSG 的命令图结构变了，这段绕法要重写。


### 坑七：出生穿透 + TMeasy 高刚度 = 车被弹射出去

**现象**：车一落地就跳起来，横向偏差瞬间冲到 4 m，5 s 内就"飞出路面"。

**根因**：官方 demo 把车放在 `地形高度 + 0.5 * Vertical()`。对刚性轮胎，
0.5 m 的余量只是"掉下去"；对半经验轮胎（TMeasy），它的垂向刚度是**解析**
算出来的，几十毫米的穿透就能造出近 20 kN 的单轮力。实测：

```
出生穿透 0.023 m  →  单轮 18.8 kN  →  弹射
```

**修法**：把出生高度改成**自校准**——按 `轮心 z − 轮胎半径 − 地形高度`
反算，只留 0.02 m 余量（`spawn_clearance`）。

**验证**：`出生几何自检: 最小轮底离地间隙 = 0.0114281 m`（正值 = 在路面之上）。

> ★ 顺带纠正一个**我自己写错的注释**：`ChPathFollowerDriver` 的第 6 个参数
> `ramp_duration`，我当时注释成「再用 8 s 线性升到目标速度」。**它不压目标
> 速度**——`ChClosedLoopDriver::Advance` 里它乘的是**油门和转向**：
> ```cpp
> if (m_ramp_duration > 0) { t -= m_zero_duration;
>     double alpha = std::min(t / m_ramp_duration, 1.0);
>     m_throttle *= alpha;  m_steering *= alpha; }
> ```
> 也就是说它是在**限制控制器的输出上限**，不是在限速。
> **一个参数的名字不是它的语义。**


### 坑八：把车放在 u = 0 起点，轮子落在 CRG 覆盖之外

**现象**：轮胎力算出来是零，或者车斜着被推出去。诊断时打印出这样一对数：

```
轮心 xy = (1.64858, 0.909701)
GetPoint().xy = (-1.88214, 0.0540404)      ← 差 3.65 m
而两者的 z 完全相等
```

**根因**：`CRGTerrain::GetPoint` 和 `GetHeight` 的开头都做同一件事——
`crgEvalxy2uv` 之后把 u/v **钳**到 `[ubeg, uend] / [vbeg, vend]`。
车放在 u=0 的起点时，轮心略在覆盖外，钳位后 `GetPoint` 返回的是
`uv → xy` **往返换算**出来的点，已经不是输入的 xy 了。这个偏移量
（3.65 m）在坡面上带出法向的一个微小水平分量，于是造出 7 mm 的假穿透，
再被 TMeasy 放大成弹射。

**修法**：出生点沿路径推进 `start_offset_m = 10.0`，让四个轮子都落在覆盖内。

**验证**：修完后轮心 xy 与 `GetPoint().xy` 一致。

> ★ **为什么这件事花了三轮才定位**：我先怀疑法向倾斜（实测 0.297°，排除），
> 再怀疑 z 不一致（实测差 0，排除）。真正定案的是**一次把两个 API 在同一个
> 输入下的 xy 摆在一起看**。
> **两个函数必须一致时，要比它们真正参与计算的那个字段（xy），
> 而不是那个恰好容易打印的字段（z）。**


### 坑九：`terrain.GetStartPosition().rot` 与路径方向差 149.4°

**现象**：修完坑八之后**更糟了**——车沿 **+X 直线**冲出去，而路径朝向是
`(−0.861, +0.508)`。整程 5.358 s、只走了 2.987 m，横向偏差 4.254 m。

**根因**：车的初始朝向直接抄了地形的 `GetStartPosition()`，它给出的是
**地形局部坐标系的朝向**，与路径切线无关。本路段起点方位角 149.465°，
两者差 149.4°——车基本是**垂直于路**起步的。

**修法**：初始 `init_csys.rot` 改成**取路径切线**：
`poly.pts[k+1] − poly.pts[k]`，用 `ChQuaternion<>(cos(h/2), 0, 0, sin(h/2))`
构造（`Q_from_AngZ` 不在作用域内）。

**验证**：`==> 起点朝向取自路径切线: 149.465 度`。

> ★ 这是一次典型的「**修好了已知根因，症状反而更重**」。
> 它说明**还有一个独立成因**。坑八→坑九→坑十，连续三次都是这个形态。


### 坑十：转向控制器增益默认为 0，不设就静默不转向

**现象**：车能起步、能加速，但从不转向，直接开出路面。
最刺眼的证据是**每一次进度报告里 `转向=0`**。

**根因**：`ChPathSteeringControllerPID` 的构造函数把增益初始化成 0：

```cpp
// ChSteeringController.cpp:109
m_Kp(0), m_Ki(0), m_Kd(0)
```

而头文件把责任写在一句注释里（`ChSteeringController.h:159`）：

```
/// The user is responsible for calling SetGains and SetLookAheadDistance.
```

不设 = 静默失效。官方 CRGTerrain demo 用的是
`SetLookAheadDistance(5); SetGains(0.5, 0, 0);`。

**修法**：照抄官方值。

**验证**：车全程横向偏差 ≤ 0.039 m（半路宽 4.25 m）。

> ★ **一个构造函数把控制器增益清零、把职责写在头文件注释里，
> 就是一个静默失效制造机。**


### 坑十一：Chrono 导出的 `CHRONO_VEHICLE_DATA_DIR` 指向不存在的路径

**现象**：`cmake --build` 里冒出一句

```
warning: 'CHRONO_VEHICLE_DATA_DIR' redefined
```

**根因**：`ChronoConfig.cmake` 导出的 `CHRONO_VEHICLE_DATA_DIR` 是
`<前缀>/share/chrono/data/vehicle/`（**存在**），但
`ChronoTargets.cmake:83` 又单独设了一遍 `INTERFACE_COMPILE_DEFINITIONS`：

```cmake
CHRONO_VEHICLE_DATA_DIR="${_IMPORT_PREFIX}/data/vehicle/"   ← 不存在
```

`ls /home/zhanghe/Packages/chrono/data` → `No such file or directory`。
两个宏同名，后一个覆盖前一个，于是车型 JSON 读不到。

**修法**：把项目自己的宏**改名**成 `WIM_CHRONO_VEHICLE_DATA_DIR`，
并写成三路回退（先项目宏 → 再 Chrono 的 → 最后从 `CHRONO_DATA_DIR` 推）。

**验证**：干净重建 → `编译退出码: 0`，且**零警告**。

> ★ **一个构建系统的警告，可能是某个库自己导出的配置写错了的唯一信号。**
> 别把它当噪音。


### 坑十二：轮胎力在走完第一步动力学之前是 0

**现象**：轮荷 CSV 的**前几行全是 0.000 kN**。

**三次失败的修法**（都记在代码注释里，避免后人重走）：

1. 用 `time > 0.0` 当判据 → `t=0.002` 仍是 0；
2. 在 `vehicle.Synchronize(...)` 之后置标志 → 仍是 0；
3. 在 `sys.DoStepDynamics(step)` 之后置标志 → 仍是 0。

**根因**：`Synchronize` 在 t=0 时步长为 0，TMeasy 返回 0。
**"有没有同步过/步进过"这类标志，与"轮胎模型报没报出载荷"不是一回事。**

**修法**：**判据改成数据本身**——等轮胎模型真的报出非零载荷再开始采样。
而且第一版用「**合计**非零」还是漏了（首行 16.08 kN，两个后轮是 0），
收紧成「**每个轮子都非零**」。

**验证**：CSV 首行 `t=0.02`，四轮 13.68 / 13.71 / 10.34 / 10.39 kN。

> ★ **用一个时间阈值去保护采样是猜；用"让数据成立的那个状态"去保护才是对的。**
> ★ 同一个缺陷在下一层复现时，判据也要跟着收紧一层。


### 坑十三：`Fz = 0` 有两种含义，必须靠 `point.z()` 区分

**现象**：7985 行里有 17 行出现 `0.000 kN`。到底是"没有接触"还是"载荷为零"？

**根因**：Chrono 的 `TerrainForce` 只有三个成员
（`force` / `point` / `moment`），**没有接触标志位**，
`ChTire` 也没有 `IsInContact()`。默认构造时 `force` 和 `point`
**同时是零向量**。所以只看 `Fz` 无法区分。

**判据**：`Fz == 0 且 pz == 0` ⇒ 没有接触（默认构造）。
路面高程是 54~84 m，真实接触点不可能在 z=0。
而**每一个**零力帧上，同帧另一根轴的 `pz` 都是正常的（82.4 / 81.1 / 84.19 m）。

**结论**：那 17 行是**真的过坡顶时整根轴同时离地**，不是零载荷。
实测形态是前轴先离、约 0.25 s 后后轴再离——**典型的过坡顶签名**。
最差一帧合计只有 7.63 kN（静态 24 kN）。

**落盘设计**：CSV 里同时存 `Fz_kN` 和 `pz_m`；画图时 `pz=0` 处**画成断线**，
不画成 0。

> ★ **一个"零"如果可能表示"没有数据"，它就不能长得像"数值为零"。**
> 必须让两者在数据里可区分，否则下游一定会误读。


### 坑十四：抓帧不是瓶颈，渲染才是；而渲染的成本全在那 46 万个面上

这一坑是**做视频**时才踩出来的，值得单独记，因为**我一开始量错了对象**。

**现象**：开窗跑 20 s 仿真，**15 分钟没跑完**。

**第一次误判**：我以为开销在"每步都渲染"。于是把 `Render()` 改成只在
抓帧的那几步调 —— **完全没用**，4 帧的那一轮从 79.3 s 只降到 78.8 s。
又顺手把 `Run()/Synchronize()/Advance()` 也一并节流 —— **还是没用**。

**量对之后**（这才是关键的一步）：

| 配置 | 耗时 |
|---|---|
| headless 20 s（基准） | 8.3 s |
| 开窗，渲染 4 帧 | 78.8 s |
| 开窗，渲染 20 帧 | 271 s |
| 开窗，渲染 200 帧 | > 600 s（超时） |

20 帧 271 s ⇒ **约 13 s/帧**。而"4 帧 78.8 s"里也**不存在**什么一次性开销
能同时解释这两行——一次性开销若是 71 s，20 帧就该是 71+20×? 对不上。
**两行数据互相矛盾时，说明模型错了，不要挑一行信。**

**根因**：`CRGTerrain` 的视觉网格按 v 通道全分辨率建。
本 CRG 有 **86 条 v 通道**、u 方向 58055 站 ⇒ **46.4 万个三角面**，
而且是**一整个 mesh**，视锥剔除对它无效，软件光栅化（llvmpipe）
每帧都要完整过一遍。头文件对这件事的原话就是：

```
/// Set optional mesh simplification, ... Default: show original mesh, maybe slow
```

**修法**：`terrain.SimplifyMesh(true)`。
它把 v 通道换成一张**固定 5 条**的表（`vbeg, −0.05, 0, 0.05, vend`，
见 `CRGTerrain.cpp:159-163`）⇒ 面数除以 17。
★ 它**只改画出来的网格**，物理仍走 `crgEvaluv2z` 的解析查询，**轮荷和轨迹一个字都不变**。

**验证**：

| 配置 | 简化前 | 简化后 |
|---|---|---|
| 渲染 20 帧 | 271 s | — |
| 渲染 40 帧 | — | 41.3 s（≈ **0.82 s/帧**） |
| 40 帧 + 抓帧 | — | 45.2 s（≈ **0.95 s/帧**） |

**16 倍**。全程 800 帧从"不可行"变成约 19 分钟。

> ★ **优化一个热点之前，先量清楚热点在哪。**
> 我按"渲染慢"的直觉去关渲染，方向对了一半，主因却完全在别处。
> ★ **一次测量的解释力不够时，就再测一个点。** 上表那四行里，
> 真正定案的是第 2 行和第 3 行放在一起看。


### 抓帧的两个上游陷阱

`ChVisualSystemVSG::WriteImageToFile(path)` **只是置了个标志**
（`m_image_filename` + `m_capture_image`），真正的抓帧发生在**下一次
`Render()` 内部**——`recordAndSubmit()` 之后取 `imageIndex(1)`，
也就是**上一帧**的色缓冲。所以：

1. **顺序必须照抄官方 demo**：先 `BeginScene()/Render()/EndScene()`，
   **再** `WriteImageToFile()`。落盘的是这次 Render 之前的那一帧。
2. **第一帧抓不到**。官方 `demo_VSG_assets.cpp:457` 的原话就是
   `// does not work with frame == 0!`。本程序用 `video_rendered > 0` 跳过它。
3. **循环结束后要补一次渲染**。最后一次 `WriteImageToFile` 只置了标志，
   循环一结束就没有"下一次 Render"了；不补这一下，**最后一张永远停在标志位里**，
   文件不会出现。这不是理论担忧——少一张就是少一张。

> ★ 顺带一条参数卫生：`--video` 与 `--headless` 是**互斥**的
> （headless 根本不建窗口，没有帧可抓）。这种自相矛盾的参数必须**当场拒绝**，
> 而不是跑完 400 s 才发现一个文件都没写。


### 坑十五：按 `--render-dt` 节流 `vis->Advance()`，追随相机会**定住**

`ChVehicleVisualSystemVSG::Advance(double step)` 内部是

```cpp
double t = 0;
while (t < step) {
    double h = std::min<>(m_stepsize, step - t);
    m_camera->Update(h);          // ← 追随相机的积分步
    t += h;
}
```

它是**相机自己的积分步**，隐含假设「每一步都调一次，于是 Σstep = 已过时间」。
一旦把 `Advance` 挂在渲染节流上（0.5 s 才调一次），相机每 0.5 s 只走
`m_stepsize` ≈ 0.002 s，**只有正常速度的 0.4%** —— 等于定住。

**实测证据（地平线所在行，1280×720）**：

| | t=0 | 1.5 s | 3.0 s | 4.5 s | 6.0 s | 7.5 s | 18 s |
|---|---|---|---|---|---|---|---|
| **节流版** | 279 | 288 | 331 | 514 | 530 | 538 | **547** |
| **不节流版** | 282 | 280 | 280 | 280 | 279 | 279 | **278** |

节流版爬了 268 行然后彻底不动；不节流版全程稳在 278~282 行。

> ★ 这个坑**骗了我很久**，因为症状方向是反的：节流版的**帧间差异反而更大**
> （0.0065 vs 0.0005）—— 那不是"画面在动"，那是**相机在慢动作追赶**。
> 追随相机跑在一条路上，地平线**本来就该**固定在屏幕同一行；
> 真正露馅的是"先爬 268 行再不动"这个**过程**，不是"不动"这个**结果**。
> ★ **只比一个标量（帧间差异）会把人带沟里；要看它随时间的形状。**


### 坑十六：无纹理的平涂地形，视频看着像静止的

`CRGTerrain` 默认不给路面贴纹理，渲染出来是**一整片均匀灰色**。
追随相机看 0.5 s（15 m/s 走 7.5 m），如果路面没有纹理，画面**几乎不变**：

| | 相邻帧平均像素差 | 全图最大单色占比 | 唯一色数 |
|---|---|---|---|
| 无纹理 | **0.0002 / 255** | 45% ~ 56% | ~1 200 |
| 有纹理 | **0.009 / 255** | 1.1% | **14 320** |

0.0002/255 是**肉眼不可分辨**的变化 —— 我据此两次误判"画面卡死了"，
两次都去修相机，而相机一直是好的。

**修法**（两条都必须在 `Initialize()` 之前调用）：

```cpp
terrain.SetRoadDiffuseTextureFile("vehicle/terrain/textures/Concrete002_2K-JPG/Concrete002_2K_Color.jpg");
terrain.SetRoadNormalTextureFile("vehicle/terrain/textures/Concrete002_2K-JPG/Concrete002_2K_NormalGL.jpg");
terrain.SetRoadRoughnessTextureFile("vehicle/terrain/textures/Concrete002_2K-JPG/Concrete002_2K_Roughness.jpg");
vis->EnableSkyTexture(SkyMode::DOME);
```

纹理参数名由 `GetChronoDataFile()` 解析（`share/chrono/data/` 之下）；
重复次数 = `0.5 × 路长 / 路宽` = **341 次**，即**每 17 m 一个循环**。
`--no-texture` 可关掉。

> ★ **一个"低差异"指标不能证明画面静止，只要画面里大半是纯色。**
> 判定动没动，要么去看**应该变的那块区域**，要么去看**一个能锚定姿态的
> 特征**（这里就是地平线所在行）；整帧求平均会被背景稀释掉。


### 坑十七：`Initialize()` 之前调 `GetLength()` 返回 0

```cpp
CRGTerrain terrain(&sys);
double r = 0.5 * terrain.GetLength() / terrain.GetWidth();   // 0 / 0 = NaN !
terrain.Initialize(crg_file);                                 // 到这里才真正读 CRG
```

`static_cast<int>(NaN)` 是 `INT_MIN`，于是日志打印出
「沿路重复 **-2147483648** 次」。

> ★ 这次是一个**荒唐到刺眼的数字**救了我。假如当初打印的是
> 「每 **0** m 一个循环」这类**看着还算合理**的值，这个错误会一直留着。
> ★ **打印派生量时，宁可选那个"错了就很离谱"的形式**，别选"错了也看不出来"的形式。


### 坑十八：车在画面里**完全隐形** —— 而它并不是没有模型

现象：视频能录、路能看、相机在动，**但整段视频里没有车**。追随视角下
你只会看到一段空路平推过去，很容易误判成"视频没动"。

根因：**Chrono 9 把 `VisualizationType` 的默认值改成了 `NONE`**。
HMMWV 从来就不缺视觉模型 —— `HMMWV_Chassis.cpp:72` 写着
`m_geometry.vis_model_file = "hmmwv/hmmwv_chassis.obj"`，
`HMMWV_Wheel.cpp:38` 写着 `hmmwv/hmmwv_rim.obj`，
`HMMWV_TMeasyTire` 写着 `hmmwv/hmmwv_tire_left/right.obj`。
但那份几何要有人**主动物化**：`ChRigidChassis::AddVisualizationAssets()`
会调 `m_geometry.CreateVisualizationAssets(m_body, vis)`
（`ChRigidChassis.cpp:67-72`），而它的上游开关没人拨。

```cpp
// ★ ChWheeledVehicle 上**没有**一个总的 SetVisualizationType，
//   必须按部件分别设（ChVehicle.h:254 + ChWheeledVehicle.h:137-153）。
//   只设底盘不设轮子，车会像一块"浮在路上的板子"。
vehicle.SetChassisVisualizationType(VisualizationType::MESH);
vehicle.SetWheelVisualizationType(VisualizationType::MESH);
vehicle.SetTireVisualizationType(VisualizationType::MESH);
```

**怎么在没有看图能力的情况下证明"车画出来了"**：做**受控对比**，
而不是肉眼看。

| 对比 | 差异均值 | 显著差异像素（>0.1） |
|---|---|---|
| **同一二进制跑两次**（对照组） | **0.00010** | **0.03 %** |
| 开车辆可视化 vs 不开（同 t 帧） | **0.02552** | **9.72 %** |

对照组几乎为零 ⇒ 那 9.7 % 的差异**只能来自车**。
顺带得到一个有用的结论：**这个仿真是逐帧可复现的**（无随机种子），
所以"两次跑同一时刻的帧"可以直接当对照用。

### 坑十九：路面纹理会让渲染进程**不确定地**段错误

现象：加上路面 PBR 纹理后，进程以 `exit 139` 死掉，**崩溃时刻每次都不一样**
—— 实测在 t≈2 s、20 s、46 s 都出现过（帧数 4 / 40 / 92）。
不带纹理时跑满 797 帧从不崩。

已排除的：
- **天空穹顶是安全的**。`EnableSkyTexture(SkyMode::DOME)` 只置一个标志，
  真正的球体在 `Initialize()` 里建（`ChVisualSystemVSG.cpp:712-719`），
  且对纹理缺失做了 null 检查。70 s 全程稳定。
- **`SimplifyMesh` 造成的顶点/UV 数量错配 —— 不成立**。
  `GenerateMesh()` 本身就按 `m_simplified_mesh` 分支并用 `m_v.size()` 定
  `nv`（`CRGTerrain.cpp:457`），UV 与顶点同步。
- **`ChVisualSystemVSG.cpp:2951` 那条"开阴影后不能新建节点"的 TODO —— 无关**。
  它注释的是一段**已被注释掉**的接触力可视化代码，我们没走那条路。

**这是 lavapipe（软件 Vulkan）侧的故障，本仓库不假装能修它。**
处理办法是让它不影响交付：`capture_full_video.sh` 崩了就换目录重录，
直到帧数达标。诚实的表述是「**重试后能录成**」，
不是「**这个 bug 修好了**」。

### 坑二十：段错误是 139，**动态库缺失是 127** —— 别混

`capture_full_video.sh` 第一次上线时四次全挂在 `exit 127`、每次 0 秒。
日志正文是：

```
error while loading shared libraries: libvsgImGui.so.0: cannot open shared object file
```

根因不在脚本逻辑，而在一行"看起来很稳"的写法：

```bash
# ✗ 错的：本机 LD_LIBRARY_PATH 本来就有一长串 CUDA 路径（已设、非空），
#   `:-` 只在未设或为空时才取默认值 => 套用了外层的 CUDA 路径，把 vsg 丢了。
export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-/home/zhanghe/Packages/chrono/lib:/home/zhanghe/Packages/vsg/lib}"

# ✓ 对的：前置，而不是替换。
export LD_LIBRARY_PATH="/home/zhanghe/Packages/chrono/lib:/home/zhanghe/Packages/vsg/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

★ 我一开始把 127 读成"又崩了"，还怀疑是四张贴图的问题 —— 又一次
**拿一个像已知问题的症状当证据**。区分办法很便宜：`127` = 加载失败，
`139` = 段错误，`124` = 超时，`1` = 判据未过，`2` = 路径自检拒绝。

### 坑二十一：标线跟着"误闭合"的跳变段走 ⇒ `crgEvalxy2uv` 迭代不收敛，程序卡死

加车道标线时，我照着 `terrain.GetRoadCenterLine()` 沿中线铺黄虚线。
结果程序**卡死 5 分钟超时（`exit 124`、0 帧）**，日志停在
「路面纹理已挂」之后、连「车道标线已加」都没打出来。

我当时的判断是"每站每个顶点都调 `GetHeight`，2.9 万次太贵"。
**这个判断是错的**——实测：

```
[诊断] GetHeight 单次代价 = 0.0083782 ms（300 次共 2.51 ms）
```

≈ 12 万次/秒，2.9 万次只要 **0.24 s**。

真因是**坑之十七那一族**：`GetRoadCenterLine()` 里含"误闭合"造成的那段
约 8.7 km 跳变（折线总长 14499.8 m vs 路长 5805.4 m）。跳变段上的点落在
CRG 网格之外，`crgEvalxy2uv`（逆变换、**迭代**搜索）在那里反复迭代不收敛，
才是真正把程序拖死的那个。

**修法**：标线必须建立在**修复后**的中线上，判据与主路径那处完全一致
（折线长 > 1.2 × 路长 ⇒ 丢掉末控制点重建开放曲线）。

★ 教训：**同一个"上游数据是坏的"根因，会在每一个新的消费点上重新爆一次。**
主路径修好了，不代表从同一棵树上下来的另一条支路也修好了。

★ 另一条：**成本猜测连错两次（先是"节流回调贵"，后是"逐顶点反查贵"），
两次都是实测推翻的。** 在动手优化之前，先量的不是"哪里慢"，而是
"我打算省掉的那一步到底值多少钱"。

### 坑二十二：真实横断面**几乎没有横坡** —— 设计表里的 `elev_diff` 不能当横坡用

我一度根据 `roadbed_design_point` 的 `elev_diff_01..11`
（边缘 0、中部最大 0.1075 m）推出"双向路拱 2.53%"，并用它给标线算高程。
自检直接打脸：**最大偏差 111 mm**（11 cm，标线会明显浮空或陷入路面）。

实测真实值：

```
标线高程自检：抽 48 点；路拱高差最大 0.000245571 mm；
              中线控制点 z 与地形实测最大差 200.09 mm
```

- **路拱高差 0.0002 mm ⇒ 路面横断面是平的**，那 0.1075 m 不是路拱。
- **中线控制点的 z 与地形实测恒差 200 mm** ⇒ 中线曲线的 z **不能**当路面
  高程用（它是 Bezier 拟合出来的，不是逐点采样）。所以标线的 z 一律走
  `terrain.GetHeight()` 实测值 + 抬 2 cm 防 z-fighting。

★ 教训：**从一张字段名叫 `elev_diff` 的表里推出几何量，是在读名字而不是读语义。**
把它算出来的东西拿去做自检，比"看起来合理就发布"便宜得多。

### 坑二十三：想让"靠右行驶"一眼可辨，就得把标线画出来 —— 而且它能反过来验车道

路面只有一张混凝土贴图时，8.5 m 宽的路面是一整片**无参照的灰**，
车在哪条车道上根本看不出来。加上标线后（黄虚线中线 v=0、
白实线车道边缘 v=±3.50，正好是 3.5 m 车行道与 0.75 m 硬路肩的分界），
**画面本身就成了车道位置的独立证据**：

| `--offset` | 近场黄像素落在画面左半边的比例 | 读出来的事实 |
|---|---|---|
| **-1.75**（靠右） | **0.9753** | 黄线在车**左** ⇒ 车在右（对） |
| 0.00（中线） | 0.4560 | 黄线几乎均分 ⇒ 车压线 |
| **+1.75**（靠左） | **0.0033** | 黄线在车**右** ⇒ 车在左（错） |

三个值单调、方向明确。**这条验证不依赖任何源码注释**——它量的是渲染出来的
像素，所以它同时证实了 `--offset` 的"正 = 左"约定和默认车道位置。
整段全程视频（797 帧）在 -1.75 下：可判帧 364 帧，其中 357 帧正确，
中位 0.9494。

★★ 这里有个**度量本身**的坑，值得单独记：一开始我用的是"黄色像素质心 x"。
结果整片统计出 **44.8% 的帧"不合格"**，其中第 198 帧甚至跑到画面右侧。
但看像素数就明白了 —— 那些"不合格"帧的近场黄像素只有 **160~210 个**
（虚线空隙），这么稀疏又对称的一小撮点，质心必然落在画面中央附近，
**那个数字根本没有含义**；而黄像素 3500~4000 的帧，质心全部正确。

修法是给统计量加**可判门槛**（近场黄像素 ≥ 500），并换成一个不依赖质心的
量（左侧像素占比）。换完之后 0.98 / 0.46 / 0.00 干净单调。

★ 教训：**一个统计量在样本稀疏时会退化成常数，而那个常数看起来像个结论。**
先把"这帧的数据够不够支撑这个统计量"判掉，再谈统计量的值。

★ 教训：**"靠右行驶"是个可测量的断言，就该用渲染结果去测它**，
而不是在注释里写一遍然后相信它。顺带：中心线用**虚线**还有第二个好处——
它自带运动视差，让人一眼确认车在往前走。

### 一条通用教训

> **一个症状看起来像已知问题，不等于它就是那个问题。**

本项目反复踩到的形态都是同一个：**失败是静默的，所以症状出现在离根因很远
的地方**。已归档的实例：`wget -q` 吞掉 LunarG 的 404 → "装好了"；
`ldconfig` 不在普通用户 PATH 且 `2>/dev/null` → "没环境"；`grep` 无匹配 +
`set -e` → "跑完了"；`~/.gitconfig` 把 github.com 改写到了失效镜像 →
"脚本地址写错了"；官方脚本硬编码 `$HOME/Sources` → "重定向生效了"；
一次打印 `Configuring done` 且 0 错误的 CMake 运行其实**一个模块都没处理**
→ "configure 通过了"；`libOpenCRG.a` 编得出、装得上、检查全过，直到 67%
链接共享库时才报 `mCrgBigEndian` → "OpenCRG 装好了"；一个变量名猜错的
`grep`（查 `OpenCRG_FOUND`，而 Chrono 用的是 `CH_ENABLE_OPENCRG`）恒假，
模块明明编成功却报"未见 OpenCRG 条目" → "OpenCRG 没链上"；
在**生产者**的源码里核对 `add_library(Chrono::vehicle ALIAS ...)` 成功，
却从没看**消费者**拿到的 `ChronoTargets.cmake` → "目标名已核对官方源码"；
凭印象写下一个 `EnableAbsFrameCoords()`，编译器说不存在 → "API 都来自
官方类参考，非猜测"；`WriteWavefront` 刚失败，下一行照样打印「已导出网格」
→ "导出成功了"；`ExportMeshWavefront` 失败被当成"boundary 模式的问题"，
其实是目录没建 → 又一个认错方向的症状；`Failed to read font` 被我归档成
"无害且不可避免的警告"，其实它直接终止了 `Initialize()`，是整条段错误链的
起点 → "那条警告可以忽略"；`lexically_normal()` 悄悄吃掉了结尾斜杠，而我的
自检用的是会自动补斜杠的 `operator/` → "自检通过"；探针编译失败、我接着跑的
却是上一次的旧二进制，那句"探针退出码 0"属于新进程 → "探针验证过了"；
`cmake --build … | head` 之后取到的 `$?` 是 `head` 的 → "编译退出码 0"；
按测量学课本把方位角写成 `(sin az, cos az)`（即默认 +Y 为北），而设计表用的是
**X=北、Y=东**的投影坐标，方向向量应是 `(cos az, sin az)` —— 33 个线形单元
**全部**算错，单元 1 偏 941 m，而我先只看了输出尾部，误判成"前 10 个是好的"
→ "积分器写好了"；用 `alpha=0.16` 画出的圆圈混色后早已不是那个 RGB，我却拿
原始 RGB 去数像素，得到 0 就以为没画上 → "圆圈没渲染"；反过来，一个真正画坏
的图（视锥虚线画到 13.5 km，把坐标轴撑到 ±2 万米）光看"文件存在、尺寸正常"
是发现不了的 → "图出来了"；把 `vis->Advance()`（相机积分步）挂在渲染节流上，
相机只走正常速度的 0.4% 而静默失效，我却先怪"渲染回调开销大"、再怪"相机没跟车"，
两次都错了，因为**我先信了一个标量指标（帧间差异），没去看它随时间的形状**
→ "画面卡死了，是相机的问题"；一块无纹理的平涂地形在追随视角下
帧间差异只有 0.0002/255，我据此**第二次**误判画面静止 → 同一个方向错两遍；
`Initialize()` 之前 `GetLength()` 返回 0，`0/0 = NaN` 转 `int` 成了 `INT_MIN`，
日志打印「重复 -2147483648 次」→ 幸好这个数字离谱到刺眼，换个"看着合理"的
打印形式就会被长期忽略；
**把一次"运行中"的进程快照读成了"崩溃"** —— 逐张贴图做对照实验时，
我在第二轮还没跑完就去数它的帧目录，看到「132 帧 / t=64 s」就宣布
"法线贴图是崩溃源"，而它其实只是**当时还没跑到 139 帧**；等它跑完，
三种贴图组合全部 `exit 1` 通过。同一个错误在同一轮里我甚至对着
第一轮的数据又犯了一遍 → "逐张定位成功，法线贴图是元凶"；
**RSS 量错了进程** —— 想查纹理是不是内存泄漏，我采样的是 `$!` 那个
**外壳/bash 的 `/proc/<pid>/status`**，全程稳定 7 MB，看着像"完全不泄漏"，
其实渲染进程根本不在那个 pid 里；★ 这类"量到了别的对象"的错误不会报错，
只会给你一个**平静的、错误的**结论；
**把 `exit 127` 当成段错误** —— `libvsgImGui.so.0` 加载失败被我在心里
归档成"又是贴图崩溃"，直到看了日志正文才发现整晚在追一个不存在的方向
→ "第四次重试也崩，贴图果然不能要"。

**这条教训的操作化**：检查要**双向**都对，不只是"会失败"。

- 恒真的检查等于没查：`ls 源码里的 CRGTerrain.h` 在 tarball 里必然存在，
  与模块有没有编无关；`ls lib/libOpenCRG.a` 在文件装上了但非 PIC 时也通过。
- 恒假的检查会**凭空制造假警报**，比不查更坏——它会让人去修一个不存在的问题。
  变量名、字段名这类"猜出来的标识符"，必须拿真实产物对一遍再用。
- 所以要选 `gcc -shared --whole-archive libOpenCRG.a`：该失败时失败，
  该通过时通过，两个方向都在沙箱里验过。

**这一轮新增的三条操作化教训**（都来自车道标线这一件事）：

- **同一个坏的上游数据，会在每一个新的消费点上重新爆一次。** 主路径修好了
  "误闭合"跳变，不代表从同一棵中线上下来的标线也修好了——标线自己又踩了一遍
  （坑二十一）。
- **优化之前要先量"我打算省掉的那一步值多少钱"**，而不是先猜"哪里慢"。
  这一轮成本猜测**连错两次**（先前"节流回调贵"，这次"逐顶点反查贵"），
  两次都是实测推翻的：`GetHeight` 实测 0.0084 ms/次。
- **一个字段名不是它的语义。** 从 `elev_diff` 推出"路拱 2.53%"，
  自检立刻报 111 mm 偏差；实测路拱高差是 **0.0002 mm**，路面就是平的（坑二十二）。
  把它算出来的东西**拿去做自检**，比"看起来合理就发布"便宜得多。
