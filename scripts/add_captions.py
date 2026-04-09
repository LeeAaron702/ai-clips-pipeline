#!/usr/bin/env python3
"""
TikTok-style animated captions with word-by-word highlighting.
V3: Improved hook extraction, bigger hook font, subtle zoom, more opaque pill.
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
FONT_SIZE = 72
HIGHLIGHT_FONT_SIZE = 86
HOOK_FONT_SIZE = 112
FONT_COLOR = (255, 255, 255, 255)
HIGHLIGHT_COLOR = (255, 255, 0, 255)
HOOK_COLOR = (255, 255, 255, 255)
STROKE_COLOR = (0, 0, 0, 255)
STROKE_WIDTH = 3
HOOK_STROKE_WIDTH = 5
BG_COLOR = (0, 0, 0, 190)
BG_PADDING_X = 44
BG_PADDING_Y = 28
BG_RADIUS = 28
CAPTION_Y = 1420
MAX_WORDS_PER_GROUP = 3
MIN_WORDS_PER_GROUP = 2
HOOK_DURATION = 2.0
HOOK_Y = 800


def load_font(size: int) -> ImageFont.FreeTypeFont:
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
    cleaned = word.strip().upper()
    return cleaned in {"MUSIC", "[MUSIC]", "(MUSIC)", "♪", "♫", "[APPLAUSE]", "[LAUGHTER]"}


def clean_word(word: str) -> str:
    w = word.strip()
    # Fix whisper hyphenation artifacts: "second -hand" -> "second-hand"
    w = re.sub(r" -", "-", w)
    w = re.sub(r"- ", "-", w)
    return w


def group_words(words: list[dict], max_per_group: int = MAX_WORDS_PER_GROUP) -> list[list[dict]]:
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


def extract_hook_text(words: list[dict], max_words: int = 9) -> str:
    """Extract the most compelling hook phrase from the clip."""
    if not words:
        return ""

    full_text = " ".join(clean_word(w["word"]) for w in words[:40])
    sentences = re.split(r'(?<=[.!?])\s+', full_text)

    # Strategy 1: Find first punchy sentence with ! or ? (min 3 words)
    for sent in sentences[:5]:
        sent = sent.strip()
        if sent.endswith(("!", "?")) and 3 <= len(sent.split()) <= max_words:
            return sent

    # Strategy 2: Combine short exclamation with next sentence for context
    if len(sentences) > 1:
        first = sentences[0].strip()
        second = sentences[1].strip()
        if len(first.split()) < 3:
            combined = first + " " + second
            cw = combined.split()
            if len(cw) <= max_words:
                return combined
            return " ".join(cw[:max_words]) + "..."

    # Strategy 3: First sentence whole if <= max_words
    if sentences:
        first = sentences[0].strip()
        first_words = first.split()
        if len(first_words) <= max_words:
            if not first.endswith((".", "!", "?")):
                first += "..."
            return first
        return " ".join(first_words[:max_words]) + "..."

    hook_words = [clean_word(w["word"]) for w in words[:5]]
    return " ".join(hook_words) + "..."


def render_hook_frame(hook_text: str, font: ImageFont.FreeTypeFont, progress: float) -> Image.Image:
    """Render hook text with fade-in/out and slight scale effect."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    words = hook_text.split()
    # Auto-split into lines: max ~4 words per line, up to 3 lines
    if len(words) <= 4:
        lines = [hook_text]
    elif len(words) <= 8:
        mid = len(words) // 2
        lines = [" ".join(words[:mid]), " ".join(words[mid:])]
    else:
        third = len(words) // 3
        lines = [" ".join(words[:third]), " ".join(words[third:2*third]), " ".join(words[2*third:])]

    # Auto-reduce font if any line is too wide
    temp_img = Image.new("RGBA", (WIDTH, 100), (0, 0, 0, 0))
    temp_draw = ImageDraw.Draw(temp_img)
    max_w = max(temp_draw.textbbox((0,0), line, font=font)[2] for line in lines)
    if max_w > WIDTH - 80:
        smaller = load_font(int(HOOK_FONT_SIZE * 0.75))
        font = smaller

    line_heights = []
    line_widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])

    total_height = sum(line_heights) + 20 * (len(lines) - 1)
    start_y = HOOK_Y - total_height // 2

    # Fade: quick in (0.1s), hold, quick out (0.15s)
    if progress < 0.05:
        alpha = int(255 * (progress / 0.05))
    elif progress > 0.85:
        alpha = int(255 * ((1.0 - progress) / 0.15))
    else:
        alpha = 255

    hook_color = (HOOK_COLOR[0], HOOK_COLOR[1], HOOK_COLOR[2], alpha)
    stroke_color = (0, 0, 0, alpha)

    for i, line in enumerate(lines):
        x = (WIDTH - line_widths[i]) // 2
        y = start_y + i * (line_heights[i] + 20)
        draw.text((x, y), line, fill=hook_color, font=font,
                  stroke_width=HOOK_STROKE_WIDTH, stroke_fill=stroke_color)

    return img


def render_caption_frame(
    group: list[dict],
    active_word_idx: int,
    font: ImageFont.FreeTypeFont,
    highlight_font: ImageFont.FreeTypeFont,
) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    word_metrics = []
    total_width = 0
    max_height = 0
    spacing = 16

    for i, w in enumerate(group):
        text = clean_word(w["word"])
        is_active = (i == active_word_idx)
        f = highlight_font if is_active else font
        bbox = draw.textbbox((0, 0), text, font=f)
        w_width = bbox[2] - bbox[0]
        w_height = bbox[3] - bbox[1]
        word_metrics.append({
            "text": text, "width": w_width, "height": w_height,
            "font": f, "is_active": is_active,
        })
        total_width += w_width
        max_height = max(max_height, w_height)

    total_width += spacing * (len(group) - 1)
    if total_width + 2 * BG_PADDING_X > WIDTH:
        total_width = WIDTH - 2 * BG_PADDING_X

    start_x = (WIDTH - total_width) // 2

    pill_x1 = start_x - BG_PADDING_X
    pill_y1 = CAPTION_Y - BG_PADDING_Y
    pill_x2 = start_x + total_width + BG_PADDING_X
    pill_y2 = CAPTION_Y + max_height + BG_PADDING_Y
    draw.rounded_rectangle((pill_x1, pill_y1, pill_x2, pill_y2), radius=BG_RADIUS, fill=BG_COLOR)

    x = start_x
    for wm in word_metrics:
        y = CAPTION_Y + (max_height - wm["height"]) // 2
        color = HIGHLIGHT_COLOR if wm["is_active"] else FONT_COLOR
        draw.text((x, y), wm["text"], fill=color, font=wm["font"],
                  stroke_width=STROKE_WIDTH, stroke_fill=STROKE_COLOR)
        x += wm["width"] + spacing

    return img


def get_words_for_clip(transcript: dict, clip_start: float, clip_end: float) -> list[dict]:
    words = []
    for segment in transcript["segments"]:
        if segment["end"] < clip_start or segment["start"] > clip_end:
            continue
        for w in segment.get("words", []):
            if w["start"] >= clip_start and w["end"] <= clip_end:
                if is_music_tag(w["word"]):
                    continue
                words.append({
                    "word": clean_word(w["word"]),
                    "start": round(w["start"] - clip_start, 3),
                    "end": round(w["end"] - clip_start, 3),
                })
    return words


def apply_zoom_effect(input_path: str, output_path: str, zoom_pct: float = 3.0) -> str:
    """Apply a subtle slow zoom (Ken Burns) effect to keep visual interest."""
    # Zoom from 100% to 100+zoom_pct% over the clip duration
    # Using ffmpeg zoompan filter
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", input_path],
        capture_output=True, text=True
    )
    duration = float(result.stdout.strip())
    total_frames = int(duration * 25)  # source fps

    # zoompan: zoom from 1.0 to 1.0+zoom_pct/100 linearly
    zoom_end = 1.0 + zoom_pct / 100.0
    zoom_expr = f"'min({zoom_end},1+({zoom_pct/100}/{total_frames})*on)'"
    
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", f"zoompan=z={zoom_expr}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=25",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"WARNING: Zoom effect failed: {result.stderr[-300:]}")
        return None
    return output_path


def add_captions(
    video_path: str,
    words: list[dict],
    output_path: str,
    clip_duration: float = None,
    show_hook: bool = True,
    apply_zoom: bool = True,
) -> str:
    """Add TikTok-style animated captions with hook overlay to a video."""
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

    # Optional zoom effect on source video first
    zoom_input = str(video_path)
    zoom_tmp = None
    if apply_zoom:
        import tempfile as tf
        zoom_tmp = tf.NamedTemporaryFile(suffix='.mp4', delete=False, prefix='zoom_').name
        print("Applying subtle zoom effect...")
        zoom_result = apply_zoom_effect(str(video_path), zoom_tmp)
        if zoom_result:
            zoom_input = zoom_tmp
        else:
            print("Zoom failed, continuing without it")

    total_frames = int(clip_duration * FPS)

    font = load_font(FONT_SIZE)
    highlight_font = load_font(HIGHLIGHT_FONT_SIZE)
    hook_font = load_font(HOOK_FONT_SIZE)

    hook_text = extract_hook_text(words) if show_hook else ""
    hook_frames = int(HOOK_DURATION * FPS) if hook_text else 0

    groups = group_words(words)
    group_timeline = []
    for group in groups:
        group_start = group[0]["start"]
        group_end = group[-1]["end"]
        group_timeline.append({"words": group, "start": group_start, "end": group_end})

    frame_dir = tempfile.mkdtemp(prefix="captions_")
    print(f"Rendering {total_frames} caption frames (hook: {hook_text!r})...")

    last_frame_img = None
    last_frame_key = None

    for frame_idx in range(total_frames):
        t = frame_idx / FPS

        if frame_idx < hook_frames and hook_text:
            progress = frame_idx / hook_frames
            frame_key = f"hook_{int(progress * 20)}"
            if frame_key != last_frame_key:
                img = render_hook_frame(hook_text, hook_font, progress)
                last_frame_img = img
                last_frame_key = frame_key
            last_frame_img.save(os.path.join(frame_dir, f"frame_{frame_idx:06d}.png"))
            continue

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
        "-i", zoom_input,
        "-i", caption_video,
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ], capture_output=True, text=True)

    shutil.rmtree(frame_dir, ignore_errors=True)
    if zoom_tmp and os.path.exists(zoom_tmp):
        os.unlink(zoom_tmp)

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
    parser.add_argument("--no-hook", action="store_true", help="Disable hook text overlay")
    parser.add_argument("--no-zoom", action="store_true", help="Disable zoom effect")
    args = parser.parse_args()

    with open(args.transcript) as f:
        transcript = json.load(f)

    words = get_words_for_clip(transcript, args.start, args.end)
    print(f"Found {len(words)} words for clip ({args.start:.1f}s - {args.end:.1f}s)")

    add_captions(args.video, words, args.output, clip_duration=args.end - args.start,
                 show_hook=not args.no_hook, apply_zoom=not args.no_zoom)


if __name__ == "__main__":
    main()
