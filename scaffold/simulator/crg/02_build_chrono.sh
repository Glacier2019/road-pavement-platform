#!/usr/bin/env bash
# ①-2 编译 Project Chrono（Vehicle + OpenCRG + VSG 可视化）
# 只需编译一次，之后 03 脚本直接跑。
set -euo pipefail

SRC_DIR="${CHRONO_SRC:-$HOME/Sources/chrono}"
BUILD_DIR="${CHRONO_BUILD:-$HOME/Builds/chrono-build}"
INSTALL_DIR="${CHRONO_INSTALL:-$HOME/Packages/chrono}"
OPENCRG_DIR="${OPENCRG_DIR:-$HOME/Packages/openCRG}"
JOBS="$(nproc)"

echo "==> Chrono 编译配置"
echo "   源码:   $SRC_DIR"
echo "   构建:   $BUILD_DIR"
echo "   安装:   $INSTALL_DIR"
echo "   OpenCRG:$OPENCRG_DIR"
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

# --- 取源码
if [ ! -d "$SRC_DIR/.git" ]; then
  echo "==> 克隆 Chrono（较慢，约 1-2 GB）"
  mkdir -p "$(dirname "$SRC_DIR")"
  git clone --depth 1 https://github.com/projectchrono/chrono.git "$SRC_DIR"
else
  echo "==> 复用已有源码 $SRC_DIR"
fi

# --- 配置
mkdir -p "$BUILD_DIR" && cd "$BUILD_DIR"
cmake "$SRC_DIR" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX="$INSTALL_DIR" \
  -DCH_ENABLE_MODULE_VEHICLE=ON \
  -DCH_ENABLE_OPENCRG=ON \
  -DOpenCRG_INCLUDE_DIR="$OPENCRG_DIR/include" \
  -DOpenCRG_LIBRARY="$OPENCRG_DIR/lib/libOpenCRG.a" \
  -DCH_ENABLE_MODULE_IRRLICHT=OFF \
  -DCH_ENABLE_MODULE_VSG=ON \
  -DBUILD_TESTING=OFF \
  -DBUILD_DEMOS=OFF

echo "==> 编译（24 核约 20-40 分钟）"
cmake --build . -j"$JOBS"

echo "==> 安装"
cmake --install .

echo
echo "==> 完成。校验 OpenCRG 支持是否真的编进去了："
grep -i "opencrg" "$BUILD_DIR/CMakeCache.txt" | head -5
echo
echo "   头文件: $(ls "$SRC_DIR/src/chrono_vehicle/terrain/CRGTerrain.h" 2>/dev/null || echo '未找到')"
