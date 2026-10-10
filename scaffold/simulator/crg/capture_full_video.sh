#!/usr/bin/env bash
# =============================================================================
# capture_full_video.sh —— 录一整趟 5805 m 仿真，并自动重试
# -----------------------------------------------------------------------------
# 为什么需要"自动重试"：
#   带路面纹理渲染时，进程会**不确定地**段错误（exit 139）。
#   实测崩溃时刻各不相同：t≈2 s / 20 s / 46 s 都出现过；不带纹理时从不崩。
#   这是 lavapipe（软件 Vulkan）侧的故障，本脚本不假装能修它，
#   而是把它挡在交付之外：崩了就换一个目录重录，直到帧数够。
#
# 用法：
#   ./capture_full_video.sh <输出目录> [重试次数，默认 4]
#
# 退出码：
#   0  录成功（帧数达标的目录已就位）
#   20 重试次数用尽仍未录成
#   10 参数错 / 二进制不存在
# =============================================================================
set -u

OUT_DIR="${1:-}"
MAX_TRY="${2:-4}"

if [ -z "$OUT_DIR" ]; then
    echo "用法: $0 <输出目录> [重试次数]" >&2
    exit 10
fi

BUILD="${WIM_CHRONO_BUILD:-/data/cy/shujuku/.chronotest/demo-build}"
BIN="$BUILD/demo_vehicle_on_crg"
CRG="$(cd "$(dirname "$0")" && pwd)/route_0p1m.crg"

[ -x "$BIN" ] || { echo "!! 找不到可执行文件 $BIN" >&2; exit 10; }
[ -f "$CRG" ] || { echo "!! 找不到路谱 $CRG" >&2; exit 10; }

# ★ 必须「前置」，不能写 ${LD_LIBRARY_PATH:-默认}。
#   这台机器上 LD_LIBRARY_PATH 本来就有一长串 CUDA 路径（已设、非空），
#   `:-` 只在未设或为空时才取默认值 —— 结果就是套用外层的 CUDA 路径、
#   把 vsg 丢了，报 `libvsgImGui.so.0: cannot open shared object file`，
#   退出码 127（**不是段错误，别误读成崩溃**）。
export LD_LIBRARY_PATH="/home/zhanghe/Packages/chrono/lib:/home/zhanghe/Packages/vsg/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
ulimit -c 0

mkdir -p "$OUT_DIR"

# 整趟约 399 s 仿真、--video-dt 0.5 => 约 797 帧。
# 阈值取 780：留一点余量，但足以判定"跑到了终点附近"。
EXPECT_MIN=780

for try in $(seq 1 "$MAX_TRY"); do
    FRAMES="$OUT_DIR/frames"
    rm -rf "$FRAMES"; mkdir -p "$FRAMES"

    echo "=== 第 $try / $MAX_TRY 次录制  $(date '+%H:%M:%S') ==="
    T0=$(date +%s)
    timeout 5400 "$BIN" "$CRG" \
        --speed 15 --offset -1.75 --duration 600 \
        --render-dt 0.5 --video "$FRAMES" --video-dt 0.5 --video-size 1280x720 \
        --pbr 0 --no-shadows \
        > "$OUT_DIR/attempt_$try.log" 2>&1
    RC=$?
    T1=$(date +%s)
    N=$(ls "$FRAMES" 2>/dev/null | wc -l)
    echo "    退出码=$RC  用时=$((T1 - T0))s  帧数=$N  （需要 >= $EXPECT_MIN）"

    if [ "$RC" -ne 139 ] && [ "$N" -ge "$EXPECT_MIN" ]; then
        echo "==> 录制成功：$N 帧"
        echo "$N" > "$OUT_DIR/frame_count.txt"
        exit 0
    fi

    if [ "$RC" -eq 139 ]; then
        echo "    !! 段错误（驱动侧故障），重试"
    else
        echo "    !! 未达标（退出码 $RC / 帧数 $N），重试"
    fi
    # 失败的一趟不留帧，免得把磁盘塞满
    rm -rf "$FRAMES"
done

echo "!! $MAX_TRY 次都没录成，放弃" >&2
exit 20
