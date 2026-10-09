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

# ============================================================================
# ★ 2026-10-09 第五次修正：改走 codeload tarball，彻底绕开 git 协议
# ============================================================================
# 下载这一步连续栽了三次，且每次症状都离根因很远：
#
#   ① 8 个 git clone 全被 ~/.gitconfig 里的镜像改写劫持到已挂掉的 ghfast.top
#      （官方脚本里 "ghfast" 出现 0 次，是用户机器上的 url.*.insteadOf 干的）
#   ② 绕开镜像走直连后拿到 6/8，剩 draco / glslang / ktx 失败
#      （超时 134s、"Error in the HTTP2 framing layer"）
#   ③ 实测确认：**本机 github.com 的 git 协议整体不可用**
#        git ls-remote github.com/vsg-dev/vsgImGui   → 超时
#        git ls-remote github.com/google/googletest  → 超时
#      而 **codeload.github.com 的 tarball 路线又快又稳**：
#        draco    60 MB / 4.3 s
#        glslang   4 MB / 1.2 s
#        KTX     212 MB / 13 s   （连测 3 次全成功）
#
# ⚠ 一个反直觉的点：`http.version = HTTP/1.1` **三个月前就配好了**
#   （~/.gitconfig 修改时间 2026-07-22，早于所有失败）。所以"HTTP/1.1 治 framing
#   错误"这条常见经验在这里**无效** —— 不是 HTTP 版本问题，是这条链路本身不通。
#   不要因为"看起来像已知问题"就跳过实测。
#
# 做法：不再 git clone。从 codeload 取 tar.gz 解压到 $HOME/Sources/，
# 再把官方脚本第 29 行的 `DOWNLOAD=ON` 改成 `OFF` —— 它就改为从这些目录读源码。
# **官方脚本的逻辑一个字都不改**，只翻它自己的开关。
#
# 附带好处：DOWNLOAD=OFF 会跳过 update_glslang_sources.py（拉 SPIRV-Tools），
# 而官方脚本对 glslang 固定传 -DENABLE_OPT=0（见其自身注释），本就不需要那些源码，
# 所以跳过零副作用 —— 反倒是之前 DOWNLOAD=ON 时这一步失败刷了两行报错。
#
# 曾担心的事（已实测否定，留此备查）：KTX-Software 4.x 带 external/ 子模块，
# tarball 里不含子模块，一度以为会编不过。**实测编过了** ——
# 沙箱完整跑通，产出 libktx.so.0 / libglslang.so.16.1.0 / libvsg.so.1.1.15 等，
# 8 个组件配置段报错数均为 0。故无需为 KTX 做任何额外处理。
# ============================================================================

SRC_ROOT="${VSG_SRC_ROOT:-$HOME/Sources}"
mkdir -p "$SRC_ROOT"

# 从 codeload 取一个仓库的源码。$1=owner/repo  $2=tag  $3=目录名
# 目录名必须与官方脚本 DOWNLOAD=OFF 分支里写死的名字**逐字一致**。
fetch_src() {
  local repo="$1" tag="$2" name="$3" dest="$SRC_ROOT/$3" i
  if [ -f "$dest/CMakeLists.txt" ]; then
    echo "   ✓ $name 已存在，跳过"
    return 0
  fi
  for i in 1 2 3 4 5; do
    rm -rf "$dest" "$dest.tmp" "$WORK/$name.tar.gz"
    printf "   ↓ %-16s %-26s @%-8s 第 %d 次\n" "$name" "$repo" "$tag" "$i"
    if curl -fL --connect-timeout 30 --retry 3 --retry-delay 5 \
         -o "$WORK/$name.tar.gz" \
         "https://codeload.github.com/$repo/tar.gz/refs/tags/$tag" \
       && mkdir -p "$dest.tmp" \
       && tar -xzf "$WORK/$name.tar.gz" -C "$dest.tmp" --strip-components=1; then
      mv "$dest.tmp" "$dest"
      echo "   ✓ $name"
      return 0
    fi
    echo "   ⚠ $name 第 $i 次失败，$((i*5))s 后重试…" >&2
    sleep $((i * 5))
  done
  rm -rf "$dest.tmp"
  echo "   ✗ $name 连续 5 次失败" >&2
  return 1
}

echo "==> 取 VSG 源码（codeload tarball -> $SRC_ROOT）"
SRC_FAIL=""
#        owner/repo                      tag       目录名（官方脚本要求逐字一致）
fetch_src vsg-dev/VulkanSceneGraph      v1.1.15  VulkanSceneGraph || SRC_FAIL=1
fetch_src vsg-dev/vsgXchange            v1.1.12  vsgXchange        || SRC_FAIL=1
fetch_src vsg-dev/vsgImGui              v0.7.0   vsgImGui          || SRC_FAIL=1
fetch_src vsg-dev/vsgExamples           v1.1.13  vsgExamples       || SRC_FAIL=1
fetch_src assimp/assimp                 v6.0.5   assimp            || SRC_FAIL=1
fetch_src google/draco                  1.5.7    draco             || SRC_FAIL=1
fetch_src KhronosGroup/glslang          16.1.0   glslang           || SRC_FAIL=1
fetch_src KhronosGroup/KTX-Software     v4.4.2   ktx               || SRC_FAIL=1

if [ -n "$SRC_FAIL" ]; then
  echo
  echo "!! 有源码没取到，编译无法进行。"
  echo "   可重跑本脚本（已成功的会自动跳过），或手动把源码放到："
  echo "     $SRC_ROOT/<目录名>"
  exit 1
fi

# ----------------------------------------------------------------------------
# ★ 2026-10-09 第六次修正：补 vsgImGui 的两个子模块
#
# 症状（宿主机实测，藏在几千行输出里，末尾看似"全部成功"）：
#     ------------------------ Configure vsgImGui
#     致命错误：不是 Git 仓库（或者任何父目录）：.git
#     CMake Error at CMakeLists.txt:39 (message):
#       git submodule update --init --recursive failed with 128, please checkout submodules
#     -- Configuring incomplete, errors occurred!
#     ninja: error: loading 'build-Release.ninja': No such file or directory
#
# 根因：codeload tarball 里**没有 .git**，而 vsgImGui v0.7.0 的 CMakeLists 写着
#     if ( (NOT EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/src/imgui/imgui.h) OR
#          (NOT EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/src/implot/implot.h) )
#         execute_process(COMMAND git submodule update --init --recursive ...)
#         if(NOT GIT_SUBMOD_RESULT EQUAL "0")
#             message(FATAL_ERROR "git submodule update ... failed with ...")
#         endif()
#     endif()
#   tarball 会建出 src/imgui、src/implot 两个**空目录**（内容不在包内），
#   于是那个判断成立 → 去调 git → 没有 .git → FATAL_ERROR。
#
# 修法：该判断**纯看文件在不在**，与 .git 无关。把两个子模块按 vsgImGui
#   钉死的 commit 填进去，它连 git 那一行都不会执行 —— 既不碰 git 协议
#   （本机 git 协议全通不了），也不需要 .git 目录。
#   commit 取自 GitHub API（git 协议不可用时的唯一可靠来源）：
#     api.github.com/repos/vsg-dev/vsgImGui/git/trees/<v0.7.0 的 src 子树>
#       imgui  993fa347495860ed44b83574254ef2a317d0c14f
#       implot f156599faefe316f7dd20fe6c783bf87c8bb6fd9
#
# ★ 这一步**不能**并进 fetch_src：fetch_src 见到 CMakeLists.txt 就跳过，
#   而 vsgImGui 目录在你上一轮已经下好了 —— 「复用旧目录」恰恰就是出问题的
#   场景（空壳子模块留在里面）。所以子模块必须独立判断、独立补。
#
# ★ 这不是可选项：Chrono 的 src/chrono_vsg/CMakeLists.txt 第 31 行是
#     find_package(vsgImGui REQUIRED)
#   缺了它，02_build_chrono.sh 必定失败。所以下面失败即 exit，不往下走。
# ----------------------------------------------------------------------------
echo "==> 补 vsgImGui 子模块（imgui / implot，按 v0.7.0 钉死的 commit）"

fetch_submodule() {
  local repo="$1" sha="$2" dest="$3" probe="$4" i
  if [ -f "$dest/$probe" ]; then
    echo "   ✓ $(basename "$dest") 已就位，跳过"
    return 0
  fi
  for i in 1 2 3 4 5; do
    printf "   ↓ %-18s @%-12s 第 %d 次\n" "$repo" "${sha:0:12}" "$i"
    if curl -fL --connect-timeout 30 --retry 3 --retry-delay 5 \
         -o "$WORK/sub.tar.gz" \
         "https://codeload.github.com/$repo/tar.gz/$sha"; then
      # 直接解进目标目录（tarball 里该目录是空壳，解包即填满）。
      # 刻意不用 rm -rf：$dest 是拼出来的路径，误删代价太大；
      # tar 覆盖写就够，而且对"已有部分内容"也更安全。
      mkdir -p "$dest"
      if tar -xzf "$WORK/sub.tar.gz" -C "$dest" --strip-components=1 \
         && [ -f "$dest/$probe" ]; then
        echo "   ✓ $(basename "$dest")"
        return 0
      fi
    fi
    echo "   ⚠ 第 $i 次失败，$((i*5))s 后重试…" >&2
    sleep $((i * 5))
  done
  echo "   ✗ $repo 连续 5 次失败" >&2
  return 1
}

SUB_FAIL=""
fetch_submodule ocornut/imgui  993fa347495860ed44b83574254ef2a317d0c14f \
                "$SRC_ROOT/vsgImGui/src/imgui"  imgui.h   || SUB_FAIL=1
fetch_submodule epezent/implot f156599faefe316f7dd20fe6c783bf87c8bb6fd9 \
                "$SRC_ROOT/vsgImGui/src/implot" implot.h  || SUB_FAIL=1

if [ -n "$SUB_FAIL" ]; then
  echo
  echo "!! vsgImGui 子模块没补齐，编译无法进行（见上面原因）。"
  echo "   重跑本脚本即可（已就位的会自动跳过）。"
  exit 1
fi

# 硬校验：vsgImGui 的 CMakeLists 要 copy 这几个头，缺任何一个都是白编一场。
# 宁可在这里停，也不要让它在几千行输出里再炸一次。
for f in src/imgui/imgui.h \
         src/imgui/imconfig.h \
         src/imgui/imgui_internal.h \
         src/imgui/imstb_textedit.h \
         src/imgui/misc/cpp/imgui_stdlib.h \
         src/implot/implot.h \
         src/implot/implot_internal.h; do
  [ -f "$SRC_ROOT/vsgImGui/$f" ] || {
    echo "!! vsgImGui 缺少 $f —— 编不过，先别往下走"; exit 1; }
done
echo "   ✓ 7 个必需头文件齐备，vsgImGui 不会再碰 git"

# 翻官方脚本自己的开关：DOWNLOAD=ON -> OFF（此后它只读源码目录，不再联网）
#
# 同时要把官方脚本里**写死的** $HOME/Sources 改成 $SRC_ROOT：
#   官方 DOWNLOAD=OFF 分支里是  VSG_SOURCE_DIR="$HOME/Sources/VulkanSceneGraph"
#   若不改，一旦用 VSG_SRC_ROOT 重定向，脚本仍去 $HOME/Sources 找 → 全线
#   "source directory does not exist"。默认情况下两者恰好相同，所以这个不一致
#   **不会报错、只会静默错位** —— 正是这种"碰巧一致"最该消灭。
#   （这是实测踩出来的：第一次跑通了 8/8 下载，却在编译段全线找不到源码。）
sed -i -e 's/^DOWNLOAD=ON$/DOWNLOAD=OFF/' \
       -e "s|\\\${HOME}/Sources|$SRC_ROOT|g" \
       -e "s|\\\$HOME/Sources|$SRC_ROOT|g" "$WORK/buildVSG.sh"

grep -q '^DOWNLOAD=OFF$' "$WORK/buildVSG.sh" \
  || { echo "!! 未能把官方脚本的 DOWNLOAD 改成 OFF（其格式可能已变）"; exit 1; }
grep -q "$SRC_ROOT/" "$WORK/buildVSG.sh" \
  || { echo "!! 未能把官方脚本的源码路径改到 $SRC_ROOT（其格式可能已变）"; exit 1; }
echo "   ✓ 官方脚本已切到 DOWNLOAD=OFF，源码路径 -> $SRC_ROOT"

# （原"git 网络加固（HTTP/1.1 + clone 重试）"整块已删除：
#   既然不再 git clone，那些加固全部失效为死代码。
#   保留其结论供后来者参考：官方 buildVSG.sh 第 62 行有 `rm -rf download_vsg`
#   每次清空重来，且 8 个 clone 各跑一次、**不重试、不检查失败**，
#   挂一个就少一个源，后面几十行 CMake 报错全是连锁反应。）

echo
echo "==> 执行（下载+编译，耗时较长）"
cd "$WORK"
bash "$WORK/buildVSG.sh" "$VSG_INSTALL_DIR"

echo
echo "==> 完成。给 Chrono CMake 用："
echo "    -DCMAKE_PREFIX_PATH=$VSG_INSTALL_DIR"
