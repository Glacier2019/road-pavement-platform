#!/usr/bin/env bash
# ①-4 编译并运行可视化
# 用法:
#   ./04_run_visualize.sh                 # 有显示：开窗口（mesh 模式）
#   ./04_run_visualize.sh --boundary      # 有显示：边界曲线模式
#   ./04_run_visualize.sh --headless      # 无显示：只导出网格/曲线，不开窗
set -euo pipefail

CRG="${CRG:-$(pwd)/route_0p1m.crg}"
CHRONO_INSTALL="${CHRONO_INSTALL:-$HOME/Packages/chrono}"

MODE=0          # 0=mesh 1=boundary
OPEN_WINDOW=1
for a in "$@"; do
  case "$a" in
    --boundary) MODE=1 ;;
    --headless) OPEN_WINDOW=0 ;;
  esac
done

[ -f "$CRG" ] || { echo "找不到 CRG 文件: $CRG"; exit 1; }

echo "==> 编译 03_visualize.cpp"
g++ -std=c++17 -O2 -o visualize 03_visualize.cpp \
  -I"$CHRONO_INSTALL/include" \
  -L"$CHRONO_INSTALL/lib" \
  -lChronoVehicle -lChronoPhysics -lChronoCore -lChronoVSG \
  -lOpenCRG -lpthread

if [ "$OPEN_WINDOW" = "0" ]; then
  echo "==> headless 模式：只导出，不开窗"
  # 无显示时 Chrono::VSG 会失败，这里靠环境变量让它跳过
  DISPLAY= ./visualize "$CRG" "$MODE" || {
    echo "!! headless 下 VSG 仍尝试开窗。"
    echo "   若只需导出，建议直接用 .crgwork 里的纯 Python 导出脚本"
    exit 1
  }
else
  echo "==> 打开窗口（模式 $MODE）"
  ./visualize "$CRG" "$MODE"
fi
