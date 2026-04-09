#!/usr/bin/env python3
"""
TikTok-style animated captions with word-by-word highlighting.
Uses Pillow for rendering + FFmpeg for overlay.

Features:
- Word-by-word highlight (current word in yellow, larger)
- Semi-transparent background pill behind text
- Center-bottom positioned (avoids TikTok UI)
- Grouped into 2-5 word phrases for readability
- Filters out [MUSIC] tags from display
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Caption styling
WIDTH = 1080
HEIGHT = 1920
FPS = 30
FONT_SIZE = 56
HIGHLIGHT_FONT_SIZE = 66
FONT_COLOR = (255, 255, 255, 255)
HIGHLIGHT_COLOR = (255, 255, 0, 255)
SHADOW_COLOR = (0, 0, 0, 200)
BG_COLOR = (0, 0, 0, 160)
BG_PADDING_X = 36
BG_PADDING_Y = 20
BG_RADIUS = 24
CAPTION_Y = 1560                          # Lower position, safe from TikTok UI
MAX_WORDS_PER_GROUP = 5
MIN_WORDS_PER_GROUP = 2


def load_font(size: int) -> ImageFont.FreeTypeFont:
    """Load a clean sans-serif font."""
    font_paths = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/SFCompact-Bold.otf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                continue
    return ImageFont.load_default()


def is_music_tag(word: str) -> bool:
    """Check if a word is a music/sound tag from whisper."""
    cleaned = word.strip().upper()
    return cleaned in {"MUSIC", "[MUSIC]", "(MUSIC)", "♪", "♫", "[APPLAUSE]", "[LAUGHTER]"}


def clean_word(word: str) -> str:
    """Clean up whisper artifacts from words."""
    # Remove leading/trailing whitespace
    word = word.strip()
    return word


def group_words(words: list[dict], max_per_group: int = MAX_WORDS_PER_GROUP) -> list[list[dict]]:
    """Group words into display phrases of 2-5 words."""
    if not words:
        return []

    groups = []
    current_group = []

    for word in words:
        current_group.append(word)
        should_break = False
        text = word["word"]

        if len(current_group) >= max_per_group:
            should_break = True
        elif len(current_group) >= MIN_WORDS_PER_GROUP:
            if text.endswith((".", ",", "!", "?", "...", ";", ":")):
                should_break = True
            word_idx = words.index(word)
            if word_idx < len(words) - 1:
                gap = words[word_idx + 1]["start"] - word["end"]
                if gap > 0.3:
                    should_break = True

        if should_break:
            groups.append(current_group)
            current_group = []

    if current_group:
        groups.append(current_group)

    return groups


def render_caption_frame(
    group: list[dict],
    active_word_idx: int,
    font: ImageFont.FreeTypeFont,
    highlight_font: ImageFont.FreeTypeFont,
) -> Image.Image:
    """Render a single caption frame as a transparent PNG."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    word_metrics = []
    total_width = 0
    max_height = 0
    spacing = 14

    for i, w in enumerate(group):
        text = clean_word(w["word"])
        is_active = (i == active_word_idx)
        f = highlight_font if is_active else font

        bbox = draw.textbbox((0, 0), text, font=f)
        w_width = bbox[2] - bbox[0]
        w_height = bbox[3] - bbox[1]

        word_metrics.append({
            "text": text,
            "width": w_width,
            "height": w_height,
            "font": f,
            "is_active": is_active,
        })
        total_width += w_width
        max_height = max(max_height, w_height)

    total_width += spacing * (len(group) - 1)

    # Ensure text fits within screen width
    if total_width + 2 * BG_PADDING_X > WIDTH:
        total_width = WIDTH - 2 * BG_PADDING_X

    start_x = (WIDTH - total_width) // 2

    # Draw background pill
    pill_x1 = start_x - BG_PADDING_X
    pill_y1 = CAPTION_Y - BG_PADDING_Y
    pill_x2 = start_x + total_width + BG_PADDING_X
    pill_y2 = CAPTION_Y + max_height + BG_PADDING_Y
    draw.rounded_rectangle((pill_x1, pill_y1, pill_x2, pill_y2), radius=BG_RADIUS, fill=BG_COLOR)

    # Draw words
    x = start_x
    for wm in word_metrics:
        y = CAPTION_Y + (max_height - wm["height"]) // 2
        color = HIGHLIGHT_COLOR if wm["is_active"] else FONT_COLOR

        # Shadow
        draw.text((x + 2, y + 2), wm["text"], fill=SHADOW_COLOR, font=wm["font"])
        # Text
        draw.text((x, y), wm["text"], fill=color, font=wm["font"])

        x += wm["width"] + spacing

    return img


def get_words_for_clip(transcript: dict, clip_start: float, clip_end: float) -> list[dict]:
    """Extract words that fall within the clip's time range, filtering MUSIC tags."""
    words = []
    for segment in transcript["segments"]:
        if segment["end"] < clip_start or segment["start"] > clip_end:
            continue
        for w in segment.get("words", []):
            if w["start"] >= clip_start and w["end"] <= clip_end:
                # Filter out MUSIC tags
                if is_music_tag(w["word"]):
                    continue
                words.append({
                    "word": clean_word(w["word"]),
                    "start": round(w["start"] - clip_start, 3),
                    "end": round(w["end"] - clip_start, 3),
                })
    return words


def add_captions(
    video_path: str,
    words: list[dict],
    output_path: str,
    clip_duration: float = None,
) -> str:
    """Add TikTok-style animated captions to a video."""
    video_path = Path(video_path).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not words:
        print("WARNING: No words to caption. Copying video as-is.")
        shutil.copy2(str(video_path), str(output_path))
        return str(output_path)

    if clip_duration is None:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True
        )
        clip_duration = float(result.stdout.strip())

    total_frames = int(clip_duration * FPS)

    font = load_font(FONT_SIZE)
    highlight_font = load_font(HIGHLIGHT_FONT_SIZE)

    groups = group_words(words)

    group_timeline = []
    for group in groups:
        group_start = group[0]["start"]
        group_end = group[-1]["end"]
        group_timeline.append({
            "words": group,
            "start": group_start,
            "end": group_end,
        })

    frame_dir = tempfile.mkdtemp(prefix="captions_")
    print(f"Rendering {total_frames} caption frames...")

    last_frame_img = None
    last_frame_key = None

    for frame_idx in range(total_frames):
        t = frame_idx / FPS

        active_group = None
        for gt in group_timeline:
            if gt["start"] <= t <= gt["end"] + 0.1:
                active_group = gt
                break

        if active_group is None:
            frame_key = "empty"
            if last_frame_key != frame_key:
                img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
                last_frame_img = img
                last_frame_key = frame_key
            last_frame_img.save(os.path.join(frame_dir, f"frame_{frame_idx:06d}.png"))
            continue

        active_word_idx = 0
        for i, w in enumerate(active_group["words"]):
            if w["start"] <= t <= w["end"] + 0.05:
                active_word_idx = i
                break
            elif t > w["end"]:
                active_word_idx = i

        frame_key = f"{id(active_group)}_{active_word_idx}"

        if frame_key != last_frame_key:
            img = render_caption_frame(active_group["words"], active_word_idx, font, highlight_font)
            last_frame_img = img
            last_frame_key = frame_key

        last_frame_img.save(os.path.join(frame_dir, f"frame_{frame_idx:06d}.png"))

    print("Compositing captions onto video...")
    caption_video = os.path.join(frame_dir, "captions.mov")

    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", os.path.join(frame_dir, "frame_%06d.png"),
        "-c:v", "png", "-pix_fmt", "rgba",
        caption_video,
    ], capture_output=True, text=True)

    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", caption_video,
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ], capture_output=True, text=True)

    shutil.rmtree(frame_dir, ignore_errors=True)

    if result.returncode != 0:
        print(f"ERROR compositing: {result.stderr[-500:]}")
        return None

    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"Captioned: {output_path.name} ({size_mb:.1f}MB)")
        return str(output_path)

    print("ERROR: Output not created")
    return None


def main():
    parser = argparse.ArgumentParser(description="Add TikTok-style captions to video clips")
    parser.add_argument("--video", required=True, help="Input video clip")
    parser.add_argument("--transcript", required=True, help="Transcript JSON file")
    parser.add_argument("--start", type=float, required=True, help="Clip start time in episode (seconds)")
    parser.add_argument("--end", type=float, required=True, help="Clip end time in episode (seconds)")
    parser.add_argument("--output", required=True, help="Output video path")
    args = parser.parse_args()

    with open(args.transcript) as f:
        transcript = json.load(f)

    words = get_words_for_clip(transcript, args.start, args.end)
    print(f"Found {len(words)} words for clip ({args.start:.1f}s - {args.end:.1f}s)")

    add_captions(args.video, words, args.output, clip_duration=args.end - args.start)


if __name__ == "__main__":
    main()
