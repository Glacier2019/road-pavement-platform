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

## ★ 三个会静默失败的坑（实测，均已修）

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

### 一条通用教训

> **一个症状看起来像已知问题，不等于它就是那个问题。**

本项目反复踩到的形态都是同一个：**失败是静默的，所以症状出现在离根因很远
的地方**。已归档的实例：`wget -q` 吞掉 LunarG 的 404 → "装好了"；
`ldconfig` 不在普通用户 PATH 且 `2>/dev/null` → "没环境"；`grep` 无匹配 +
`set -e` → "跑完了"；`~/.gitconfig` 把 github.com 改写到了失效镜像 →
"脚本地址写错了"；官方脚本硬编码 `$HOME/Sources` → "重定向生效了"；
一次打印 `Configuring done` 且 0 错误的 CMake 运行其实**一个模块都没处理**
→ "configure 通过了"；`libOpenCRG.a` 编得出、装得上、检查全过，直到 67%
链接共享库时才报 `mCrgBigEndian` → "OpenCRG 装好了"。

**这条教训的操作化**：凡"检查通过"的结论，都要问一句**这个检查真的会失败吗**。
`ls 源码里的 CRGTerrain.h` 恒真；`ls lib/libOpenCRG.a` 恒真；而
`gcc -shared --whole-archive libOpenCRG.a` 会失败——所以要选后者。
能失败的检查才是检查。
