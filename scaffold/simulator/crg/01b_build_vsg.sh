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
#
# ★ 2026-10-09 放宽：原先硬性要求 VULKAN_SDK 环境变量，实测这**过严**了。
#   VSG 的 CMake 用 find_package(Vulkan REQUIRED)，而 CMake 自带的 FindVulkan
#   模块**同时认**发行版包（libvulkan-dev 提供 vulkan/vulkan.h + libvulkan.so）
#   与 LunarG SDK（提供 VULKAN_SDK 环境变量）。两者都能编过。
#
#   触发这次放宽的具体情况：用户在 Ubuntu 26.04 (resolute) 上，
#   LunarG 的 apt 源**没有** resolute 这个代号
#   （packages.lunarg.com/vulkan/lunarg-vulkan-resolute.list 返回 404），
#   而 `wget -q` 会把 404 也静默吞掉 —— 于是"装好了"的假象一路带到 configure 失败。
#   故这里改成「有 SDK 就用，没有则回退到系统包」，并**明确打印走的是哪条路**。
fail=0
VULKAN_MODE=""
VULKAN_EVIDENCE=""
if [ -n "${VULKAN_SDK:-}" ] && [ -d "${VULKAN_SDK}" ]; then
  VULKAN_MODE="LunarG SDK"
  VULKAN_EVIDENCE="VULKAN_SDK=$VULKAN_SDK"
elif [ -f /usr/include/vulkan/vulkan.h ]; then
  # ★ 2026-10-09 第二次修正：原先这里还要求 `ldconfig -p | grep libvulkan.so`，
  #   但 ldconfig 装在 /sbin，**普通用户的 PATH 通常不含 /sbin** —— 于是
  #   "command not found" 被 2>/dev/null 吞掉、&& 链断开，误报"没有 Vulkan 环境"。
  #   （在 root 下测不出来，所以第一次没发现。）
  #   现在只认头文件：libvulkan-dev 同时提供 vulkan/vulkan.h 与 libvulkan.so，
  #   头文件在 = 开发包在，库由 CMake 的 FindVulkan 自己找。
  VULKAN_MODE="系统包 (libvulkan-dev)"
  VULKAN_EVIDENCE="/usr/include/vulkan/vulkan.h 存在"
else
  echo "!! 找不到 Vulkan 开发环境。二选一："
  echo "   (a) 发行版包（更快，推荐先试）:"
  echo "         sudo apt install -y libvulkan-dev vulkan-tools glslang-tools"
  echo "   (b) LunarG SDK（官方推荐，任何发行版都可用）:"
  echo "         从 https://vulkan.lunarg.com/sdk/home#linux 下 tarball，"
  echo "         解压后 source 其 setup-env.sh"
  echo "   ⚠ 注意: LunarG 的 apt 源只覆盖部分 Ubuntu 代号，"
  echo "     新版本（如 resolute）没有；且 wget -q 会静默吞掉 404。"
  echo
  echo "   诊断（把这几行贴出来即可定位）："
  echo "     ls -l /usr/include/vulkan/vulkan.h"
  echo "     dpkg -l | grep -E 'libvulkan|glslang'"
  fail=1
fi
if [ -n "$VULKAN_MODE" ]; then
  echo "==> Vulkan 来源: $VULKAN_MODE  [$VULKAN_EVIDENCE]"
fi

command -v ninja >/dev/null || { echo "!! 缺 ninja：sudo apt install ninja-build"; fail=1; }
command -v glslangValidator >/dev/null || {
  echo "!! 缺 glslangValidator（VSG 编译着色器要用）："
  echo "     sudo apt install -y glslang-tools"
  fail=1; }
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
