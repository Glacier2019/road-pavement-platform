#!/usr/bin/env bash
# ①-4 编译并运行 CRGTerrain 可视化
#
# 用法:
#   ./04_run_visualize.sh                     # mesh 模式（路面三角网格带）
#   ./04_run_visualize.sh --boundary          # 边界曲线模式（中心线 + 左右边界）
#   ./04_run_visualize.sh --crg /path/x.crg   # 指定 CRG 文件
#
# 注意：本程序会开窗口，需要可用 GPU 与显示（VSG 是 Vulkan 后端）。
#       若只想导出网格/中心线（不需要 GL），请用纯 Python 的 export_mesh.py。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRG="${CRG:-$HERE/route_0p1m.crg}"
CHRONO_INSTALL="${CHRONO_INSTALL:-$HOME/Packages/chrono}"
OPENCRG_DIR="${OPENCRG_DIR:-$HOME/Packages/openCRG}"
VSG_DIR="${VSG_DIR:-$HOME/Packages/vsg}"
BUILD="$HERE/build"

MODE=0          # 0=mesh 1=boundary
while [ $# -gt 0 ]; do
  case "$1" in
    --boundary) MODE=1 ;;
    --crg) shift; CRG="$1" ;;
    *) echo "未知参数: $1"; exit 1 ;;
  esac
  shift
done

[ -f "$CRG" ] || { echo "找不到 CRG 文件: $CRG"; exit 1; }
[ -d "$CHRONO_INSTALL" ] || { echo "找不到 Chrono 安装: $CHRONO_INSTALL"; exit 1; }
# ★ 2026-10-09：这里**不需要**传 OpenCRG_INCLUDE_DIR / OpenCRG_LIBRARY。
#   我一度以为要 —— 因为 configure 打出过
#     "-- The provided OpenCRG library file does not exist"。
#   但那句话是**我自己**在旧 CMakeLists.txt 里调 find_package(OpenCRG)
#   触发的，不是 Chrono 要的。实测宿主产物：
#     libChrono_vehicle.so 里 crg 符号 已定义 138 个 / 未定义 0 个
#   OpenCRG 是静态库，编 Chrono 时就链进去了，用的人不必再找它。
#   硬传反而换来 CMake 的 "Manually-specified variables were not used" 警告，
#   白白吓人一跳。
#
#   但"Chrono 究竟带没带 CRG 支持"值得查 —— 这才是真会失败的那种检查：
#   没带的话路面会退化成平地，窗口照样能开，问题要到最后才看得见。
CRG_SYMS="$(nm -D --defined-only "$CHRONO_INSTALL/lib/libChrono_vehicle.so" 2>/dev/null | grep -ci crg || true)"
if [ "${CRG_SYMS:-0}" -eq 0 ]; then
  echo "!! $CHRONO_INSTALL/lib/libChrono_vehicle.so 里没有 CRG 符号。"
  echo "   说明当初编 Chrono 时没开 -DCH_ENABLE_OPENCRG=ON，路面会退化成平地。"
  echo "   请重跑 ./02_build_chrono.sh。"
  exit 1
fi
echo "==> libChrono_vehicle.so 带 CRG 支持（$CRG_SYMS 个 crg 符号）"

echo "==> 配置并编译（用 CMake，让 Chrono 自己解析依赖）"
# 清掉上一次 configure 的缓存：失败留下的 CMakeCache 里可能有空变量，
# 带着它重配会继续出错。这个 demo 只有一个源文件，重配的代价可忽略，
# 不值得为省这点时间去和 cache 较劲。
rm -rf "$BUILD"
cmake -S "$HERE" -B "$BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$CHRONO_INSTALL;$VSG_DIR;$OPENCRG_DIR"
cmake --build "$BUILD" -j"$(nproc)"

echo "==> 运行（模式 $MODE：$([ "$MODE" = 0 ] && echo mesh || echo boundary)）"
# 让运行期能找到 Chrono / VSG 的动态库
export LD_LIBRARY_PATH="$CHRONO_INSTALL/lib:$VSG_DIR/lib:${LD_LIBRARY_PATH:-}"
cd "$HERE"
"$BUILD/demo_CRG_visualization" "$CRG" "$MODE"
