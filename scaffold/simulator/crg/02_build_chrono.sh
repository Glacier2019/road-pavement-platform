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

# --- 依赖检查（官方 tutorial_install_chrono 要求的 GL 那套）
MISSING=""
for p in libgl1-mesa-dev libglu1-mesa-dev libx11-dev libxext-dev \
         libxrandr-dev libxinerama-dev libxcursor-dev libxi-dev libxxf86vm-dev; do
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
[ -d "$VSG_DIR/lib" ] || {
  echo "!! 找不到 $VSG_DIR/lib"
  echo "   请先运行 ./01b_build_vsg.sh（需要 GPU + Vulkan SDK）"; exit 1; }

# --- 取源码
if [ ! -d "$SRC_DIR/.git" ]; then
  echo "==> 克隆 Chrono（较慢，约 1-2 GB）"
  mkdir -p "$(dirname "$SRC_DIR")"
  git clone --depth 1 https://github.com/projectchrono/chrono.git "$SRC_DIR"
else
  echo "==> 复用已有源码 $SRC_DIR"
fi

# --- 配置
# 选项名均已对照官方源码核对：
#   src/chrono_vehicle/CMakeLists.txt: CH_ENABLE_MODULE_VEHICLE / CH_ENABLE_OPENCRG
#   src/chrono_vsg/CMakeLists.txt:     CH_ENABLE_MODULE_VSG
#   src/CMakeLists.txt:                BUILD_DEMOS / BUILD_TESTING
mkdir -p "$BUILD_DIR" && cd "$BUILD_DIR"
cmake "$SRC_DIR" \
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
  -DBUILD_DEMOS=OFF

echo "==> 编译（示例：24 核约 20-40 分钟）"
cmake --build . -j"$JOBS"

echo "==> 安装"
cmake --install .

echo
echo "==> 完成。校验 OpenCRG 支持是否真的编进去了："
if grep -qi "OpenCRG_FOUND\|CH_USE_OPENCRG" "$BUILD_DIR/CMakeCache.txt"; then
  # ★ 2026-10-09：`| head -5` 会在匹配行超过 5 条时提前关闭管道，给 grep 发
  #   SIGPIPE → grep 非零退出 → 配合本脚本的 `set -euo pipefail` 会**杀掉脚本**，
  #   而且死在"编译已成功、只差打印几行"的位置，极难察觉。
  #   CMakeCache 里的 opencrg 条目完全可能超过 5 行，故用 || true 兜住。
  grep -i "opencrg" "$BUILD_DIR/CMakeCache.txt" | head -5 || true
else
  echo "   (CMakeCache 中未见 OpenCRG 条目，请检查上方 configure 输出)"
fi
echo
echo "   CRGTerrain 头文件:"
ls "$SRC_DIR/src/chrono_vehicle/terrain/CRGTerrain.h" 2>/dev/null || echo "   未找到（源码路径可能有变）"
