#!/bin/bash
# TikTok Video Assembly Pipeline
# Usage: ./assemble.sh --clips clip1.mp4,clip2.mp4,clip3.mp4 --audio voiceover.mp3 --hook "Stop scrolling" --cta "Link in bio" --music assets/music/track1.mp3 --output output.mp4
# All clips get stitched, hook text overlaid, voiceover synced, captions burned, compliance added

set -e

# Parse arguments
CLIPS=""
AUDIO=""
HOOK_TEXT=""
CTA_TEXT=""
MUSIC=""
OUTPUT=""
FONT="/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_REGULAR="/System/Library/Fonts/Supplemental/Arial.ttf"
COMPLIANCE_TEXT="Contains affiliate links | #ad"

while [[ $# -gt 0 ]]; do
    case $1 in
        --clips) CLIPS="$2"; shift 2 ;;
        --audio) AUDIO="$2"; shift 2 ;;
        --hook) HOOK_TEXT="$2"; shift 2 ;;
        --cta) CTA_TEXT="$2"; shift 2 ;;
        --music) MUSIC="$2"; shift 2 ;;
        --output) OUTPUT="$2"; shift 2 ;;
        --font) FONT="$2"; shift 2 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [ -z "$CLIPS" ] || [ -z "$OUTPUT" ]; then
    echo "Usage: $0 --clips clip1.mp4,clip2.mp4 --audio voiceover.mp3 --hook 'Hook text' --cta 'CTA text' --music track.mp3 --output output.mp4"
    exit 1
fi

WORKDIR=$(mktemp -d)
trap "rm -rf $WORKDIR" EXIT

echo "=== TikTok Video Assembly ==="

# Step 1: Normalize all clips to 1080x1920, 30fps
echo "[1/6] Normalizing clips..."
IFS=',' read -ra CLIP_ARRAY <<< "$CLIPS"
CONCAT_LIST="$WORKDIR/concat.txt"
> "$CONCAT_LIST"

for i in "${!CLIP_ARRAY[@]}"; do
    CLIP="${CLIP_ARRAY[$i]}"
    NORM="$WORKDIR/norm_${i}.mp4"
    ffmpeg -y -i "$CLIP" \
        -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,fps=30" \
        -c:v libx264 -preset fast -crf 23 \
        -an \
        "$NORM" 2>/dev/null
    echo "file '$NORM'" >> "$CONCAT_LIST"
done

# Step 2: Concatenate clips with crossfade
echo "[2/6] Concatenating clips..."
CONCAT_OUT="$WORKDIR/concat.mp4"
if [ ${#CLIP_ARRAY[@]} -eq 1 ]; then
    cp "$WORKDIR/norm_0.mp4" "$CONCAT_OUT"
else
    ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" \
        -c:v libx264 -preset fast -crf 23 \
        "$CONCAT_OUT" 2>/dev/null
fi

# Get video duration
DURATION=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$CONCAT_OUT" | cut -d. -f1)
echo "   Video duration: ${DURATION}s"

# Step 3: Add text overlays (hook + compliance + CTA)
echo "[3/6] Adding text overlays..."
OVERLAY_OUT="$WORKDIR/overlay.mp4"

# Build filter chain
FILTER=""

# Escape font path for FFmpeg (handle spaces)
FONT_ESC=$(echo "$FONT" | sed "s/'/\\\\'/g" | sed 's/ /\\ /g')

# Hook text (first 3 seconds, center, large white text with shadow)
if [ -n "$HOOK_TEXT" ]; then
    FILTER="drawtext=text='${HOOK_TEXT}':fontsize=56:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2:enable='between(t,0,3)'"
fi

# Compliance text (first 5 seconds, bottom, small)
if [ -n "$FILTER" ]; then FILTER="$FILTER,"; fi
FILTER="${FILTER}drawtext=text='${COMPLIANCE_TEXT}':fontsize=24:fontcolor=white@0.7:x=(w-text_w)/2:y=h-60:enable='between(t,0,5)'"

# CTA text (last 3 seconds, center)
if [ -n "$CTA_TEXT" ]; then
    CTA_START=$((DURATION - 3))
    if [ $CTA_START -lt 0 ]; then CTA_START=0; fi
    FILTER="${FILTER},drawtext=text='${CTA_TEXT}':fontsize=48:fontcolor=white:borderw=3:bordercolor=black:x=(w-text_w)/2:y=(h-text_h)/2+60:enable='gte(t,$CTA_START)'"
fi

ffmpeg -y -i "$CONCAT_OUT" \
    -vf "$FILTER" \
    -c:v libx264 -preset fast -crf 23 \
    -c:a copy \
    "$OVERLAY_OUT" 2>/dev/null

# Step 4: Mix audio (voiceover + background music)
echo "[4/6] Mixing audio..."
AUDIO_OUT="$WORKDIR/final_audio.mp4"

if [ -n "$AUDIO" ] && [ -n "$MUSIC" ]; then
    # Mix voiceover (full volume) + music (15% volume)
    ffmpeg -y -i "$OVERLAY_OUT" -i "$AUDIO" -i "$MUSIC" \
        -filter_complex "[1:a]apad[vo];[2:a]volume=0.15,apad[bg];[vo][bg]amix=inputs=2:duration=first[aout]" \
        -map 0:v -map "[aout]" \
        -c:v copy -c:a aac -shortest \
        "$AUDIO_OUT" 2>/dev/null
elif [ -n "$AUDIO" ]; then
    # Voiceover only
    ffmpeg -y -i "$OVERLAY_OUT" -i "$AUDIO" \
        -map 0:v -map 1:a \
        -c:v copy -c:a aac -shortest \
        "$AUDIO_OUT" 2>/dev/null
elif [ -n "$MUSIC" ]; then
    # Music only (30% volume)
    ffmpeg -y -i "$OVERLAY_OUT" -i "$MUSIC" \
        -filter_complex "[1:a]volume=0.30[bg]" \
        -map 0:v -map "[bg]" \
        -c:v copy -c:a aac -shortest \
        "$AUDIO_OUT" 2>/dev/null
else
    cp "$OVERLAY_OUT" "$AUDIO_OUT"
fi

# Step 5: Final encode with TikTok-optimized settings
echo "[5/6] Final encode..."
FINAL_OUT="$WORKDIR/final.mp4"
ffmpeg -y -i "$AUDIO_OUT" \
    -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
    -c:a aac -b:a 128k -ar 44100 \
    -movflags +faststart \
    "$FINAL_OUT" 2>/dev/null

# Step 6: Copy to output
echo "[6/6] Saving to $OUTPUT"
cp "$FINAL_OUT" "$OUTPUT"

# Report
FILESIZE=$(ls -lh "$OUTPUT" | awk '{print $5}')
FINAL_DUR=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$OUTPUT")
echo ""
echo "=== Done ==="
echo "Output: $OUTPUT"
echo "Duration: ${FINAL_DUR}s"
echo "Size: $FILESIZE"
echo "Resolution: 1080x1920 (9:16)"
