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

echo "==> 配置并编译（用 CMake，让 Chrono 自己解析依赖）"
cmake -S "$HERE" -B "$BUILD" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$CHRONO_INSTALL;$VSG_DIR;$OPENCRG_DIR"
cmake --build "$BUILD" -j"$(nproc)"

echo "==> 运行（模式 $MODE：$([ "$MODE" = 0 ] && echo mesh || echo boundary)）"
# 让运行期能找到 Chrono / VSG 的动态库
export LD_LIBRARY_PATH="$CHRONO_INSTALL/lib:$VSG_DIR/lib:${LD_LIBRARY_PATH:-}"
cd "$HERE"
"$BUILD/demo_CRG_visualization" "$CRG" "$MODE"
