#!/usr/bin/env bash
# =============================================================================
# 把 05_vehicle_on_crg --video 抓出来的 PNG 序列合成一个 mp4。
#
#   用法: ./make_video.sh <帧目录> [输出.mp4] [fps]
#         默认输出 <帧目录>/../vehicle_on_crg.mp4，默认 20 fps
#
# -----------------------------------------------------------------------------
# 为什么需要一个脚本，而不是一句 ffmpeg
# -----------------------------------------------------------------------------
# 因为「帧率」这件事在这里是**有物理含义的**，不是随便填的：
#
#   仿真抓帧间隔 --video-dt 0.5 表示"每 0.5 秒仿真时间一张图"。
#   如果按 2 fps 播放，看到的就是**实时**（1 秒仿真 = 1 秒视频）。
#   按 20 fps 播放，就是**10 倍速**——5.8 km 的路 40 秒看完。
#
#   所以 fps = (1 / 抓帧间隔) × 倍速。把倍速写进脚本，比每次手算可靠。
#
# 另一个坑：ffmpeg 的 -framerate 和 -r 不是一回事。
#   输入用 -framerate（告诉它序列本身的速率），
#   输出用 -r（告诉它成片速率）。只写 -r 会把帧丢重或补重。
# =============================================================================
set -uo pipefail

FRAME_DIR="${1:-}"
OUT="${2:-}"
FPS="${3:-20}"

if [[ -z "$FRAME_DIR" || ! -d "$FRAME_DIR" ]]; then
    echo "用法: $0 <帧目录> [输出.mp4] [fps]" >&2
    echo "  帧目录不存在: ${FRAME_DIR:-<空>}" >&2
    exit 10
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "!! 找不到 ffmpeg" >&2
    exit 10
fi

# 帧文件名是 frame_00000.png 起、连续编号。
# ★ 这里必须**核对连续性**，不能只看张数：中途丢一帧的话 ffmpeg 会
#   静默把它当成序列结束，成片少一大截，而且不报错。
FIRST=$(ls "$FRAME_DIR"/frame_*.png 2>/dev/null | head -1)
if [[ -z "$FIRST" ]]; then
    echo "!! $FRAME_DIR 里没有 frame_*.png" >&2
    exit 10
fi
N=$(ls "$FRAME_DIR"/frame_*.png | wc -l)
LAST_IDX=$(basename "$(ls "$FRAME_DIR"/frame_*.png | tail -1)" .png | sed 's/frame_//')
LAST_IDX=$((10#$LAST_IDX))
if [[ "$LAST_IDX" -ne $((N - 1)) ]]; then
    echo "!! 帧号不连续：共 $N 张，但最后一帧编号是 $LAST_IDX（应为 $((N - 1))）" >&2
    echo "   缺帧会让 ffmpeg 静默截断成片，先补齐再合成。" >&2
    exit 20
fi

if [[ -z "$OUT" ]]; then
    OUT="$(dirname "$(readlink -f "$FRAME_DIR")")/vehicle_on_crg.mp4"
fi
mkdir -p "$(dirname "$OUT")"

echo "==> 帧目录   : $FRAME_DIR"
echo "==> 张数     : $N"
echo "==> 播放帧率 : $FPS fps"
echo "==> 成片时长 : $(awk -v n="$N" -v f="$FPS" 'BEGIN{printf "%.1f", n/f}') s"
echo "==> 输出     : $OUT"

ffmpeg -hide_banner -loglevel error -stats \
    -framerate "$FPS" \
    -i "$FRAME_DIR/frame_%05d.png" \
    -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
    -movflags +faststart \
    -y "$OUT"
RC=$?
if [[ $RC -ne 0 ]]; then
    echo "!! ffmpeg 失败，退出码 $RC" >&2
    exit 30
fi

# ★ 不信 ffmpeg 的"成功"，回头问一遍产物本身。
if [[ ! -s "$OUT" ]]; then
    echo "!! 输出文件不存在或是空的" >&2
    exit 30
fi
ffprobe -hide_banner -loglevel error \
    -show_entries format=duration,size:stream=width,height,nb_frames,r_frame_rate \
    -of default=noprint_wrappers=1 "$OUT"
echo "==> 完成: $OUT ($(du -h "$OUT" | cut -f1))"
