#!/usr/bin/env bash
# 编译 OpenCRG v1.1.2 —— Chrono 的 CH_ENABLE_OPENCRG 指定要这个版本
#
# 依据: Chrono contrib/build-scripts/linux/buildOpenCRG.sh（硬编码 REV=1.1.2）
#       https://api.projectchrono.org/module_vehicle_installation.html
#
# 注意（实测踩过的坑）：
#   1. v1.1.2 用 makefile，不是 CMake
#   2. make 前必须先建 obj/ 与 lib/ 目录，否则汇编器报 "can't create obj/*.o"
#   3. makefile 里 CFLGS 含 -ansi(C90)，而源码有 // 注释 → 现代 GCC 编译失败，
#      需把 -ansi 换成 -std=gnu99
#   4. 装到仓库外，保持仓库干净可分发
set -euo pipefail

INSTALL_DIR="${1:-$HOME/Packages/openCRG}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> OpenCRG v1.1.2 -> $INSTALL_DIR"
command -v gcc >/dev/null || { echo "缺 gcc"; exit 1; }

cd "$WORK"
echo "==> 下载源码 (hlrs-vis/opencrg v1.1.2)"
curl -fL --retry 5 --retry-delay 3 -o crg.zip \
  https://github.com/hlrs-vis/opencrg/archive/refs/tags/v1.1.2.zip
unzip -q crg.zip
cd opencrg-1.1.2

echo "==> 修正 C90 兼容问题（-ansi -> -std=gnu99）"
sed -i 's/^CFLGS = -Wall -O3 -ansi/CFLGS = -Wall -O3 -std=gnu99/' makefile
# ★ 2026-10-09：原先这里是裸的 `grep -n '^CFLGS' makefile`。
#   本脚本有 `set -e`，grep 匹配不到就返回 1 → **静默杀掉脚本**，无任何提示。
#   这里的检查是**有意义的断言**（sed 没改成功 = 后面必然以难懂的 C90 报错失败），
#   所以不该简单地 || true 掉，而要**把它变成一条能看懂的报错**。
if ! grep -q '^CFLGS.*std=gnu99' makefile; then
  echo "!! 未能把 makefile 的 CFLGS 改成 -std=gnu99。"
  echo "   官方 makefile 的格式可能已变（本脚本按 v1.1.2 写的）。"
  echo "   请手动检查 makefile 里的 CFLGS 行，确认不含 -ansi 后重跑。"
  exit 1
fi
grep -n '^CFLGS' makefile

echo "==> 预建目录并编译"
mkdir -p obj lib
# ★ 必须串行：该 makefile 的 archive 目标未声明对 obj/*.o 的依赖，
#   并行时归档会先于编译执行 → "obj/*.o: 没有那个文件或目录"
make

echo "==> 安装到 $INSTALL_DIR"
mkdir -p "$INSTALL_DIR/lib" "$INSTALL_DIR/include"
cp -P lib/*.a "$INSTALL_DIR/lib/"
cp inc/*.h  "$INSTALL_DIR/include/"

echo
echo "==> 完成。产物："
ls -la "$INSTALL_DIR/lib/" "$INSTALL_DIR/include/"
echo
echo "给 Chrono CMake 用："
echo "  -DOpenCRG_INCLUDE_DIR=$INSTALL_DIR/include"
echo "  -DOpenCRG_LIBRARY=$INSTALL_DIR/lib/libOpenCRG.a"
