#!/usr/bin/env bash
# ①-3 编译 Project Chrono（Vehicle + OpenCRG + VSG 可视化）
# 只需编译一次，之后 04 脚本直接跑。
#
# 前置：
#   ./01_build_opencrg.sh     装 OpenCRG v1.1.2
#   ./01b_build_vsg.sh        装 VSG 全家桶（VSG 是 Vulkan 后端，需 GPU + Vulkan SDK）
set -euo pipefail

SRC_DIR="${CHRONO_SRC:-$HOME/Sources/chrono}"
BUILD_DIR="${CHRONO_BUILD:-$HOME/Builds/chrono-build}"
INSTALL_DIR="${CHRONO_INSTALL:-$HOME/Packages/chrono}"
OPENCRG_DIR="${OPENCRG_DIR:-$HOME/Packages/openCRG}"
VSG_DIR="${VSG_DIR:-$HOME/Packages/vsg}"
JOBS="$(nproc)"

echo "==> Chrono 编译配置"
echo "   源码:   $SRC_DIR"
echo "   构建:   $BUILD_DIR"
echo "   安装:   $INSTALL_DIR"
echo "   OpenCRG:$OPENCRG_DIR"
echo "   VSG:    $VSG_DIR"
echo "   并行:   $JOBS"

# --- 依赖检查（官方 tutorial_install_chrono 要求的 GL 那套 + Eigen3）
#
# ★ 2026-10-09：加入 libeigen3-dev。它**不是可选项**，而且缺了以后报错方式极其
#   误导 —— 详见下面 configure 段落里的详细说明。宿主实测这台机器原本就没装。
MISSING=""
for p in libgl1-mesa-dev libglu1-mesa-dev libx11-dev libxext-dev \
         libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev libxxf86vm-dev \
         libeigen3-dev; do
  dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"
done
if [ -n "$MISSING" ]; then
  echo
  echo "!! 缺少依赖，请先安装："
  echo "   sudo apt-get install -y$MISSING"
  echo "   （CJK 字体可选：fonts-noto-cjk）"
  exit 1
fi

# --- 前置产物检查：给出明确报错，而不是让 cmake 在中途失败
[ -f "$OPENCRG_DIR/lib/libOpenCRG.a" ] || {
  echo "!! 找不到 $OPENCRG_DIR/lib/libOpenCRG.a"
  echo "   请先运行 ./01_build_opencrg.sh"; exit 1; }

# --- ★ OpenCRG 必须是 PIC 的
#
# ★ 2026-10-09：宿主实测过一次失败 —— Chrono 编到 67%、要产出
#   libChrono_vehicle.so 时炸在
#     libOpenCRG.a(crgLoader.o): relocation R_X86_64_PC32 against symbol
#       `mCrgBigEndian' can not be used when making a shared object;
#       recompile with -fPIC
#   根因：那个 .a 是 OpenCRG 自带 makefile 编的，而它的 CFLGS 里没有 -fPIC
#   （Chrono 默认把模块编成 SHARED，于是必须 PIC）。
#
#   01_build_opencrg.sh 已改为官方路线（直接 gcc -fPIC）并自带同样的探测。
#   这里再放一道，是为了兜住"有人跳过了 01、用的是上一次留下的旧 .a"——
#   那种情况下代价是白编 30 分钟才发现。探测本身只要一两秒。
if ! gcc -shared -o /dev/null \
       -Wl,--whole-archive "$OPENCRG_DIR/lib/libOpenCRG.a" -Wl,--no-whole-archive \
       -lm 2>/dev/null; then
  echo
  echo "!! $OPENCRG_DIR/lib/libOpenCRG.a 不是 PIC 的（不带 -fPIC 编出来的）。"
  echo "   继续下去会在编到 67% 链接 libChrono_vehicle.so 时失败。"
  echo "   请重跑 ./01_build_opencrg.sh 重新编译并安装 OpenCRG。"
  echo "   ★ 不要改用 OpenCRG 自带的 makefile —— 它的 CFLGS 里没有 -fPIC。"
  exit 1
fi
[ -d "$VSG_DIR/lib" ] || {
  echo "!! 找不到 $VSG_DIR/lib"
  echo "   请先运行 ./01b_build_vsg.sh（需要 GPU + Vulkan SDK）"; exit 1; }

# --- 取源码
#
# ★ 2026-10-09：从 `git clone --depth 1` 改为 codeload tarball。
#
#   原因：这台机器上 **github.com 的 git 协议完全不通**（实测 `git ls-remote`
#   对 github.com 反复超时；根因是 ~/.gitconfig 里那条 `insteadOf` 把
#   https://github.com/ 改写到了已失效的镜像 ghfast.top，且 github.com 直连
#   也不通）。codeload.github.com 则快而稳。
#   `git clone --depth 1` 与「main 分支的 tarball」在内容上等价，都是该分支
#   最新一次提交的快照 —— 所以这是等价的替换，不是降级。
#
#   实测：chrono main tarball 605 MB / 约 63 s。
#
#   ★ 已知差异（已确认无影响）：Chrono 仓库带 5 个子模块
#     （googletest / googlebenchmark / flatbuffers / fmu-forge / SEA-Stack），
#     tarball 不含子模块内容。其中：
#       - googletest / googlebenchmark 在 src/CMakeLists.txt 里是
#         `if(EXISTS ...)` 保护的（L463 / L491），缺了只会打印一行
#         "not found: update git submodules" 然后关掉该功能，**不是错误**；
#         何况本脚本本来就 BUILD_TESTING=OFF。
#       详见下方 configure 段落的子模块说明。
#
#   CHRONO_REF 可覆盖（分支名 / tag / commit 均可）。
CHRONO_REF="${CHRONO_REF:-main}"
if [ -f "$SRC_DIR/CMakeLists.txt" ]; then
  echo "==> 复用已有源码 $SRC_DIR"
else
  DL_DIR="$(dirname "$SRC_DIR")"
  mkdir -p "$DL_DIR"
  echo "==> 取 Chrono 源码（codeload tarball, ref=$CHRONO_REF，约 605 MB）"
  ok=0
  for i in 1 2 3 4 5; do
    printf "   第 %d 次…\n" "$i"
    if curl -fL --connect-timeout 30 --retry 3 --retry-delay 5 \
         -o "$DL_DIR/chrono-$CHRONO_REF.tar.gz" \
         "https://codeload.github.com/projectchrono/chrono/tar.gz/$CHRONO_REF"; then
      rm -rf "$SRC_DIR.tmp"
      mkdir -p "$SRC_DIR.tmp"
      if tar -xzf "$DL_DIR/chrono-$CHRONO_REF.tar.gz" \
             -C "$SRC_DIR.tmp" --strip-components=1 \
         && [ -f "$SRC_DIR.tmp/CMakeLists.txt" ]; then
        mv "$SRC_DIR.tmp" "$SRC_DIR"
        rm -f "$DL_DIR/chrono-$CHRONO_REF.tar.gz"
        ok=1
        break
      fi
      rm -rf "$SRC_DIR.tmp"
    fi
    echo "   ⚠ 第 $i 次失败，$((i*5))s 后重试…" >&2
    sleep $((i * 5))
  done
  if [ "$ok" != 1 ]; then
    echo
    echo "!! Chrono 源码没取到。可重跑本脚本（已成功的会跳过），"
    echo "   或手动把源码放到：$SRC_DIR"
    exit 1
  fi
  echo "   ✓ 源码就位"
fi

# --- 配置
# 选项名均已对照官方源码核对：
#   src/chrono_vehicle/CMakeLists.txt: CH_ENABLE_MODULE_VEHICLE / CH_ENABLE_OPENCRG
#   src/chrono_vsg/CMakeLists.txt:     CH_ENABLE_MODULE_VSG
#   src/CMakeLists.txt:                BUILD_DEMOS / BUILD_TESTING
mkdir -p "$BUILD_DIR" && cd "$BUILD_DIR"

# ★ 2026-10-09：configure 输出落盘 + 两道硬校验。
#
#   为什么必须校验：Chrono 缺 Eigen3 时的行为是**静默致残**，不是报错。
#   src/CMakeLists.txt L126-149：
#       find_package(Eigen3 5.0 QUIET)
#       if(NOT Eigen3_FOUND) find_package(Eigen3 3.3 QUIET) endif()
#       if(Eigen3_FOUND) ... else()
#           message(ERROR "Eigen3 cannot be found.\n" ...)   # ← 注意：不是 FATAL_ERROR
#           return()                                          # ← ★ 顶层 return()
#       endif()
#
#   `message(ERROR ...)` 在 CMake 里**不是致命错误**，只是打印一行带 "ERROR"
#   前缀的字样；紧接着的 `return()` 在顶层 CMakeLists 里会**终止该文件后续所有
#   内容** —— 包括 L535-561 那一串 add_subdirectory(chrono_vehicle / chrono_vsg /
#   ...)。实测后果：
#       -- Configuring done (1.4s)          ← 看着一切正常
#       -- Generating done
#       CMake Warning: Manually-specified variables were not used by the project:
#           CH_ENABLE_MODULE_VEHICLE  CH_ENABLE_MODULE_VSG  CH_ENABLE_OPENCRG ...
#       configure 退出码: 0                  ← ★ 成功退出
#   也就是说：**没有 Eigen3 时，Chrono 会"成功地"配置出一个不含任何模块的构建**，
#   编译 30 分钟只得到 core，然后 04_run_visualize.sh 在完全无关的地方报错。
#   （本机实测复现：宿主与开发沙箱都没有 libeigen3-dev。）
#
#   所以下面不能只靠 `set -e` —— 它拦不住退出码 0 的致残。
CFG_LOG="$BUILD_DIR/configure.log"
if ! cmake "$SRC_DIR" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$INSTALL_DIR" \
  -DCMAKE_PREFIX_PATH="$VSG_DIR" \
  -DCH_ENABLE_MODULE_VEHICLE=ON \
  -DCH_ENABLE_OPENCRG=ON \
  -DOpenCRG_INCLUDE_DIR="$OPENCRG_DIR/include" \
  -DOpenCRG_LIBRARY="$OPENCRG_DIR/lib/libOpenCRG.a" \
  -DCH_ENABLE_MODULE_VSG=ON \
  -DCH_ENABLE_MODULE_IRRLICHT=OFF \
  -DBUILD_TESTING=OFF \
  -DBUILD_DEMOS=OFF 2>&1 | tee "$CFG_LOG"; then
  # ★ 2026-10-09：这里原本写的是「configure 失败（退出码非 0）」，**那是会骗人的**。
  #   实测踩到：把这段单独抽出来跑时忘了先建 $BUILD_DIR，结果 tee 写不出日志 →
  #   pipefail 让整条管道非 0 → 走进这个分支，报的却是"cmake 失败"。
  #   cmake 其实成功了。**报错信息必须指向真因，否则它比不报还坏** ——
  #   你会去查 CMakeLists，而问题在一个不存在的目录。
  echo
  echo "!! configure 未成功。"
  if [ -f "$CFG_LOG" ]; then
    echo "   完整日志：$CFG_LOG"
  else
    echo "   ★ 日志文件根本没写出来（$CFG_LOG）。"
    echo "     这通常意味着失败的是 tee 那一步（目录不存在 / 不可写），"
    echo "     而不是 cmake 本身 —— 别被上面这行 'configure 未成功' 误导，"
    echo "     先确认 $BUILD_DIR 存在且可写。"
  fi
  exit 1
fi

# 校验①：Chrono 那句自己的软失败提示。直接对准已知机制，报错信息能指出真因。
if grep -q 'Eigen3 cannot be found' "$CFG_LOG"; then
  echo
  echo "!! Chrono 没找到 Eigen3。"
  echo "   它的 src/CMakeLists.txt 会在此时 return()，configure 仍返回 0，"
  echo "   但**一个模块都不会被加入构建** —— 编下去只是浪费 30 分钟。"
  echo "   修复：sudo apt-get install -y libeigen3-dev"
  exit 1
fi

# 校验②：不依赖 Chrono 的提示文字（将来它改措辞也不失效），
#        改为断言"它确实走完了模块处理"。
grep -q 'Eigen3 found' "$CFG_LOG" || {
  echo
  echo "!! configure 日志里没有 'Eigen3 found' —— 说明 src/CMakeLists.txt 很可能"
  echo "   在中途 return() 了。完整日志：$CFG_LOG"
  exit 1
}
grep -q 'Manually-specified variables were not used' "$CFG_LOG" && {
  echo
  echo "!! configure 报告我们传的开关「未被使用」—— 典型原因就是它提前 return() 了，"
  echo "   模块根本没被处理。看一眼上面那份警告里列了哪些变量。"
  echo "   完整日志：$CFG_LOG"
  exit 1
}
echo "   ✓ Eigen3 就位，模块开关均已被消费"

echo "==> 编译（示例：24 核约 20-40 分钟）"
cmake --build . -j"$JOBS"

echo "==> 安装"
cmake --install .

echo
echo "==> 完成。OpenCRG 解析结果（来自 CMakeCache）："
# ★ 2026-10-09 第二次修正 —— 这里原来是：
#     if grep -qi "OpenCRG_FOUND\|CH_USE_OPENCRG" CMakeCache.txt; then ...
#   宿主实测：CMakeCache 里**没有这两个名字**。Chrono 实际用的是
#   CH_ENABLE_OPENCRG / OpenCRG_INCLUDE_DIR / OpenCRG_LIBRARY，于是条件恒假，
#   模块**已经编成功**却打印「未见 OpenCRG 条目，请检查上方 configure 输出」。
#   又是「检查本身是错的」——和上一版 `ls 源码里的 CRGTerrain.h` 恒真同类，
#   只是这次错在恒假，会凭空制造一次假警报。
#   故：按真实存在的名字匹配，并且这只是一条**信息**，不再扮演门；
#   真正的门是下面那段"装出来了吗"的产物断言。
# 2>/dev/null 只吞掉 grep 自己那句"没有那个文件"：文件不在的情况由下面的
# else 分支正式报告，再冒一行原始报错只是噪音，会让人以为出错了。
OPENCRG_CACHE="$(grep -i 'opencrg' "$BUILD_DIR/CMakeCache.txt" 2>/dev/null | head -5 || true)"
if [ -n "$OPENCRG_CACHE" ]; then
  # `| head -5` 会提前关闭管道给 grep 发 SIGPIPE，配合 set -o pipefail 会杀掉脚本，
  # 而它死在「编译已成功、只差打印几行」的位置，极难察觉。故必须 || true 兜住。
  printf '%s\n' "$OPENCRG_CACHE" | sed 's/^/   /'
else
  echo "   (CMakeCache 里没有 OpenCRG 条目)"
fi

# ★ 2026-10-09：改成断言**安装出来的东西**。
#   原来这里只是 `ls 源码里的 CRGTerrain.h` —— 那个文件在 tarball 里必然存在，
#   无论模块有没有被编，所以它恒真、等于没查。真正该问的是"装出来了吗"。
echo
echo "==> 关键产物断言（防止「configure 成功但模块没编」这种静默致残）"
INSTALL_FAIL=""
for pat in Chrono_vehicle Chrono_vsg; do
  if find "$INSTALL_DIR" -name "*${pat}*" -print -quit 2>/dev/null | grep -q .; then
    echo "   ✓ $pat"
  else
    echo "   ✗ $pat  **未安装**"
    INSTALL_FAIL=1
  fi
done
for h in chrono_vehicle/terrain/CRGTerrain.h; do
  if [ -f "$INSTALL_DIR/include/$h" ]; then
    echo "   ✓ include/$h"
  else
    echo "   ✗ include/$h  **未安装**"
    INSTALL_FAIL=1
  fi
done

if [ -n "$INSTALL_FAIL" ]; then
  echo
  echo "!! 有模块没编出来。最可能的原因仍是 Eigen3（见 configure 段落），"
  echo "   其次看 $CFG_LOG 里 chrono_vehicle / chrono_vsg 两段的状态。"
  echo "   ★ 别急着往下跑 04 —— 它会报一个与真因无关的错。"
  exit 1
fi
echo
echo "   ✓ Chrono::Vehicle + Chrono::VSG 均已安装"
echo "   04_run_visualize.sh 用的："
echo "     CHRONO_INSTALL=$INSTALL_DIR"
