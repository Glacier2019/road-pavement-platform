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
# ★ 2026-10-09 第三次修正：这里原先是裸的 `grep ... | sed ...`。
#   本脚本开头有 `set -euo pipefail`，而官方 buildVSG.sh 的**注释格式会变** ——
#   grep 匹配不到就返回 1，配合 pipefail 整条管道失败，**set -e 当场杀掉脚本**。
#   症状极具迷惑性：只打印到"官方锁定版本："就静默回到提示符，无任何报错，
#   于是"下载+编译"那段从未执行，~/Packages/vsg/lib 从未生成。
#   这段输出纯属**显示版本号**，它的失败绝不该是致命的 —— 故用 || 兜住。
grep -E "^# +- *(VulkanSceneGraph|vsgXchange|vsgImGui|glslang|assimp|draco|ktx)" \
  "$WORK/buildVSG.sh" | sed 's/^# *//' \
  || echo "   (未能从官方脚本注释抓到版本号——仅影响这行显示，继续编译)"

# --- ★ 2026-10-09 第四次修正：绕过 git 全局的 GitHub 镜像改写
#
# 很多国内机器在 ~/.gitconfig 里配了：
#     [url "https://<镜像>/https://github.com/"]
#         insteadOf = https://github.com/
# 而官方 buildVSG.sh 用的是**干净的** https://github.com/...（实测 "ghfast" 出现 0 次），
# 于是 8 个 git clone 全被改写到镜像站。镜像站一旦挂掉，症状是
# 8 个仓库依次超时 / "连接被对方重置"，紧接着几十行 CMake 报错 —— 全是连锁反应，
# 根因（一条 git 配置）离症状极远，极易误判成"脚本地址写错了"。
#
# 处理：检测到改写时**实测** github.com 直连是否可用；
#   可用 → 本次编译忽略该改写（GIT_CONFIG_GLOBAL 指向空配置，只影响本进程及子进程，
#          **不改用户配置**，可逆）；
#   不可用 → 沿用用户配置（说明那个镜像确实是他需要的）。
if git config --global --get-regexp '^url\..*\.insteadof$' 2>/dev/null | grep -q 'https://github\.com/'; then
  echo "==> 检测到 git 全局配置把 github.com 改写到了镜像："
  git config --global --get-regexp '^url\..*\.insteadof$' 2>/dev/null | sed 's/^/     /' || true
  if GIT_CONFIG_GLOBAL=/dev/null timeout 25 git ls-remote --exit-code \
       https://github.com/vsg-dev/VulkanSceneGraph HEAD >/dev/null 2>&1; then
    echo "    实测 github.com 直连可用 → 本次编译忽略该改写"
    echo "    （仅本进程及其子进程生效，不动你的 ~/.gitconfig）"
    export GIT_CONFIG_GLOBAL=/dev/null
  else
    echo "    ⚠ github.com 直连不可用 → 沿用你的镜像配置"
    echo "      若镜像本身也连不上，需先修好网络再重跑"
  fi
fi

echo
echo "==> 执行（下载+编译，耗时较长）"
cd "$WORK"
bash "$WORK/buildVSG.sh" "$VSG_INSTALL_DIR"

echo
echo "==> 完成。给 Chrono CMake 用："
echo "    -DCMAKE_PREFIX_PATH=$VSG_INSTALL_DIR"
