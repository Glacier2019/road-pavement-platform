#!/usr/bin/env bash
# ①-2 编译 VSG 可视化依赖（VulkanSceneGraph 全家桶）
#
# 为什么需要这一步：
#   Chrono 的 chrono_vsg 模块用裸 find_package(vsg 1.1.0 REQUIRED) /
#   vsgXchange / vsgImGui —— 没有 FetchContent，不会自动下载。
#   不先装 VSG，开 CH_ENABLE_MODULE_VSG 会在 configure 阶段直接失败。
#
# 本脚本直接复用 Chrono 官方的 contrib/build-scripts/linux/buildVSG.sh，
# 避免版本漂移。官方锁定：
#   VulkanSceneGraph v1.1.15 / vsgXchange v1.1.12 / vsgImGui v0.7.0
#   glslang 16.1.0 / assimp / draco / ktx
#
# 前置（必须先装，脚本不会代劳）：
#   - Vulkan SDK（含 glslang、SPIR-V 工具链）
#     https://vulkan.lunarg.com/sdk/home
#   - ninja-build        sudo apt install ninja-build
#   - 一块能用的 GPU + 正确安装的驱动（VSG 是 Vulkan 后端，不能纯软件跑）
set -euo pipefail

VSG_INSTALL_DIR="${1:-$HOME/Packages/vsg}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> VSG 依赖 -> $VSG_INSTALL_DIR"

# --- 前置检查
fail=0
if [ -z "${VULKAN_SDK:-}" ]; then
  echo "!! 未检测到 VULKAN_SDK 环境变量。"
  echo "   请先安装 Vulkan SDK 并 source 其 setup-env.sh："
  echo "     https://vulkan.lunarg.com/sdk/home"
  fail=1
fi
command -v ninja >/dev/null || { echo "!! 缺 ninja：sudo apt install ninja-build"; fail=1; }
command -v glslangValidator >/dev/null || echo "   (提示: 未找到 glslangValidator，若 Vulkan SDK 装好通常会有)"
[ "$fail" = 0 ] || exit 1

# --- 取官方脚本
echo "==> 取 Chrono 官方 buildVSG.sh（保证版本与 Chrono 一致）"
curl -fL --retry 5 --retry-delay 3 -o "$WORK/buildVSG.sh" \
  https://raw.githubusercontent.com/projectchrono/chrono/main/contrib/build-scripts/linux/buildVSG.sh
chmod +x "$WORK/buildVSG.sh"

echo "==> 官方锁定版本："
grep -E "^# +- *(VulkanSceneGraph|vsgXchange|vsgImGui|glslang|assimp|draco|ktx)" \
  "$WORK/buildVSG.sh" | sed 's/^# *//'

echo
echo "==> 执行（下载+编译，耗时较长）"
cd "$WORK"
bash "$WORK/buildVSG.sh" "$VSG_INSTALL_DIR"

echo
echo "==> 完成。给 Chrono CMake 用："
echo "    -DCMAKE_PREFIX_PATH=$VSG_INSTALL_DIR"
