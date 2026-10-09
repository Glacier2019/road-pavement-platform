#!/usr/bin/env bash
# 编译 OpenCRG v1.1.2 —— Chrono 的 CH_ENABLE_OPENCRG 指定要这个版本
#
# 依据: Chrono contrib/build-scripts/linux/buildOpenCRG.sh（硬编码 REV=1.1.2）
#       https://api.projectchrono.org/module_vehicle_installation.html
#
# ★ 2026-10-09 修正：改用官方脚本的**直接 gcc** 路线，不再走 OpenCRG 自带的 makefile。
#
#   起因是宿主实测的链接失败（Chrono 已编到 67%）：
#       libOpenCRG.a(crgLoader.o): relocation R_X86_64_PC32 against symbol
#         `mCrgBigEndian' can not be used when making a shared object;
#         recompile with -fPIC
#       collect2: error: ld returned 1 exit status
#   即 Chrono 默认把模块编成共享库（libChrono_vehicle.so），而
#   **libOpenCRG.a 里的目标文件不是位置无关代码**。原因：OpenCRG 自带
#   makefile 的 CFLGS 里没有 -fPIC。
#
#   官方 buildOpenCRG.sh 根本不碰那个 makefile，它自己直接编译：
#       ${COMP} -Wall -O3 -fPIC -I${CRG_SOURCE_DIR}/inc -c ${CRG_SOURCE_DIR}/src/*.c
#       ${AR} -r ${CRG_INSTALL_DIR}/lib/libOpenCRG.${REV}.a *.o
#   跟着官方走，-fPIC 自然就有，而且顺带绕开了 makefile 的另一个坑：
#   它的 CFLGS 含 -ansi(=C90)，而源码里有 // 注释 —— 现代 GCC 直接编不过。
#   原先本脚本为此做了一次 `-ansi` → `-std=gnu99` 的 sed；**那条路现在整条不要了**，
#   因为那个问题只存在于 makefile 路线（沙箱实测：官方旗标在 gcc 15.2 上
#   11/11 全部编过，无需任何 -std）。
#
#   教训与 Eigen3 / vsgImGui 那两次同形：**失败是静默的**。不带 -fPIC 的 .a
#   编得出来、装得上、`ls` 也一切正常，问题要等到几十分钟后链接共享库时才炸，
#   而且报错里出现的是 crgLoader.o 和 mCrgBigEndian，跟"少了个编译选项"
#   看起来毫无关系。故本脚本收尾做一次**真的链接探测**（见下），
#   几秒钟内把同一个错误在源头复现出来。
set -euo pipefail

INSTALL_DIR="${1:-$HOME/Packages/openCRG}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "==> OpenCRG v1.1.2 -> $INSTALL_DIR"
command -v gcc >/dev/null || { echo "缺 gcc"; exit 1; }
command -v ar  >/dev/null || { echo "缺 ar（binutils）"; exit 1; }
command -v unzip >/dev/null || { echo "缺 unzip"; exit 1; }

cd "$WORK"
echo "==> 下载源码 (hlrs-vis/opencrg v1.1.2)"
curl -fL --retry 5 --retry-delay 3 -o crg.zip \
  https://github.com/hlrs-vis/opencrg/archive/refs/tags/v1.1.2.zip
unzip -q crg.zip

SRC="$WORK/opencrg-1.1.2"
[ -d "$SRC/src" ] && [ -d "$SRC/inc" ] || {
  echo "!! 源码目录结构不符预期（应有 src/ 与 inc/）。实际："
  ls "$SRC" 2>/dev/null | sed 's/^/     /'
  exit 1
}

echo "==> 编译（官方路线：直接 gcc，带 -fPIC）"
# 官方脚本就是这一条：-Wall -O3 -fPIC。没有 -ansi、也没有 -std=。
mkdir -p "$WORK/obj"
( cd "$WORK/obj" && gcc -Wall -O3 -fPIC -I"$SRC/inc" -c "$SRC"/src/*.c )
ls "$WORK/obj"/*.o >/dev/null 2>&1 || { echo "!! 一个 .o 都没编出来"; exit 1; }
echo "   ✓ $(ls "$WORK"/obj/*.o | wc -l) 个目标文件"

echo "==> 归档 libOpenCRG.a"
mkdir -p "$INSTALL_DIR/lib" "$INSTALL_DIR/include"
# 先删旧的：ar -r 是「更新」，留着旧包会混进上一次的成员
rm -f "$INSTALL_DIR/lib/libOpenCRG.a"
ar -r "$INSTALL_DIR/lib/libOpenCRG.a" "$WORK"/obj/*.o >/dev/null
command -v ranlib >/dev/null && ranlib "$INSTALL_DIR/lib/libOpenCRG.a"

echo "==> 头文件 -> $INSTALL_DIR/include"
cp "$SRC"/inc/*.h "$INSTALL_DIR/include/"

# ─────────────────────────────────────────────────────────────────────
# ★ PIC 链接探测：把宿主那次失败在**几秒内**复现出来
#
# 做法：用 --whole-archive 把 .a 的**每一个**成员都强行拉进一个共享库。
# 只要有任何一个 .o 不是位置无关代码，ld 就会报出与 Chrono 那次**逐字相同**
# 的 R_X86_64_PC32 错误；反之则说明这包 .a 可以安全链进 .so。
#
# 这个探测经过双向验证：带 -fPIC 的包通过，不带 -fPIC 的包报出
#   libC.a(crgLoader.o): relocation R_X86_64_PC32 against symbol
#   `mCrgBigEndian' can not be used when making a shared object
# —— 与宿主 Chrono 那次的错误信息完全一致。
# ─────────────────────────────────────────────────────────────────────
echo "==> 链接探测：确认 libOpenCRG.a 是 PIC 的（能链进共享库）"
PROBE="$WORK/picprobe"
if gcc -shared -o "$PROBE.so" \
     -Wl,--whole-archive "$INSTALL_DIR/lib/libOpenCRG.a" -Wl,--no-whole-archive \
     -lm 2>"$PROBE.err"; then
  echo "   ✓ 通过 —— Chrono 编到 67% 时不会再炸在这一步"
else
  echo
  echo "!! libOpenCRG.a **不是** PIC 的。Chrono 会在链接 libChrono_vehicle.so 时失败。"
  echo "   探测到的原始错误（与 Chrono 那次同源）："
  sed 's/^/     /' "$PROBE.err" | head -10
  echo
  echo "   这说明上面的编译**没有带 -fPIC**。请检查本脚本那条 gcc 命令行。"
  echo "   ★ 不要改用 OpenCRG 自带的 makefile —— 它的 CFLGS 里没有 -fPIC，"
  echo "     正是这个问题的来源。"
  exit 1
fi

echo
echo "==> 完成。产物："
ls -la "$INSTALL_DIR/lib/" "$INSTALL_DIR/include/"
echo
echo "给 Chrono CMake 用："
echo "  -DOpenCRG_INCLUDE_DIR=$INSTALL_DIR/include"
echo "  -DOpenCRG_LIBRARY=$INSTALL_DIR/lib/libOpenCRG.a"
