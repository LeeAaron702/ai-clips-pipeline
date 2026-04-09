#!/usr/bin/env python3
"""
TikTok-style animated captions V4.
- Word pop-in bounce animation
- Montserrat ExtraBold, ALL CAPS
- No background pill, heavy drop shadow
- Large text (100/130/160px)
- 2-line layout (4-6 words per group)
- Keyword color highlighting
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

from PIL import Image, ImageDraw, ImageFont, ImageFilter

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# === STYLE CONSTANTS ===
WIDTH = 1080
HEIGHT = 1920
FPS = 30

# Font sizes
FONT_SIZE = 100
HIGHLIGHT_FONT_SIZE = 130
HOOK_FONT_SIZE = 150

# Colors
FONT_COLOR = (255, 255, 255, 255)
HIGHLIGHT_COLOR = (31, 135, 255, 255)      # Top Gear blue for keywords
ACTIVE_COLOR = (31, 135, 255, 255)          # Top Gear blue pop for current word
HOOK_COLOR = (255, 255, 255, 255)
STROKE_COLOR = (0, 0, 0, 255)
SHADOW_COLOR = (0, 0, 0, 180)

# Shadow (replaces background pill)
STROKE_WIDTH = 2
SHADOW_OFFSETS = [(0, 5), (1, 5), (-1, 5), (2, 4), (-2, 4), (0, 6), (3, 3), (-3, 3)]

# Layout
CAPTION_Y = 1280                  # Higher to fit 2 lines above TikTok UI
LINE_SPACING = 20
WORD_SPACING = 24
MAX_WORDS_PER_GROUP = 5
MIN_WORDS_PER_GROUP = 3

# Animation
BOUNCE_FRAMES = 7                 # ~230ms at 30fps
HOOK_DURATION = 2.0
HOOK_Y = 750

# Persistent top hook (stays entire clip)
TOP_HOOK_Y = 200                  # Near top, below TikTok header
TOP_HOOK_FONT_SIZE = 64
TOP_HOOK_COLOR = (255, 255, 255, 230)

# Emphasis words that get permanent highlight color
EMPHASIS_WORDS = {
    "die", "died", "dead", "death", "kill", "killed", "crash", "crashed",
    "fire", "explode", "destroyed", "smash", "fastest", "insane",
    "incredible", "amazing", "terrible", "horrible", "impossible",
    "brilliant", "genius", "perfect", "beautiful", "ruined", "disaster",
    "nightmare", "ridiculous", "magnificent", "spectacular", "hilarious",
    "never", "always", "worst", "best", "ever", "hell", "god", "bloody",
    "water", "broke", "broken", "fail", "failed", "won", "win", "lost",
    "love", "hate", "yes", "no",
}

# === FONT LOADING ===
_font_cache = {}

def load_font(size: int) -> ImageFont.FreeTypeFont:
    if size in _font_cache:
        return _font_cache[size]
    font_paths = [
        str(PROJECT_ROOT / "assets" / "fonts" / "Montserrat-ExtraBold.ttf"),
        str(PROJECT_ROOT / "assets" / "fonts" / "Montserrat-Black.ttf"),
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                f = ImageFont.truetype(fp, size)
                _font_cache[size] = f
                return f
            except Exception:
                continue
    return ImageFont.load_default()


# === ANIMATION ===
def bounce_scale(frame_offset: int) -> float:
    """Ease-out-back bounce: 0 -> overshoot 115% -> settle 100%."""
    if frame_offset >= BOUNCE_FRAMES:
        return 1.0
    if frame_offset < 0:
        return 0.0
    t = frame_offset / BOUNCE_FRAMES  # 0 to 1
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * pow(t - 1, 3) + c1 * pow(t - 1, 2)


# === TEXT HELPERS ===
def is_music_tag(word: str) -> bool:
    cleaned = word.strip().upper()
    return cleaned in {"MUSIC", "[MUSIC]", "(MUSIC)", "♪", "♫", "[APPLAUSE]", "[LAUGHTER]"}


def clean_word(word: str) -> str:
    w = word.strip()
    w = re.sub(r" -", "-", w)
    w = re.sub(r"- ", "-", w)
    return w


def is_emphasis(word: str) -> bool:
    return word.strip(".,!?;:\"'").lower() in EMPHASIS_WORDS


def group_words(words: list[dict]) -> list[list[dict]]:
    if not words:
        return []
    groups = []
    current = []
    for word in words:
        current.append(word)
        should_break = False
        text = word["word"]
        if len(current) >= MAX_WORDS_PER_GROUP:
            should_break = True
        elif len(current) >= MIN_WORDS_PER_GROUP:
            if text.endswith((".", ",", "!", "?", "...", ";", ":")):
                should_break = True
            idx = words.index(word)
            if idx < len(words) - 1:
                gap = words[idx + 1]["start"] - word["end"]
                if gap > 0.3:
                    should_break = True
        if should_break:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def split_into_lines(words: list) -> list[list]:
    """Split word list into 2 lines for display."""
    if len(words) <= 3:
        return [words]
    mid = (len(words) + 1) // 2
    return [words[:mid], words[mid:]]


# === HOOK ===
def extract_hook_text(words: list[dict], max_words: int = 9) -> str:
    if not words:
        return ""
    full_text = " ".join(clean_word(w["word"]) for w in words[:40])
    sentences = re.split(r'(?<=[.!?])\s+', full_text)
    for sent in sentences[:5]:
        sent = sent.strip()
        if sent.endswith(("!", "?")) and 3 <= len(sent.split()) <= max_words:
            return sent.upper()
    if len(sentences) > 1:
        first = sentences[0].strip()
        second = sentences[1].strip()
        if len(first.split()) < 3:
            combined = first + " " + second
            cw = combined.split()
            if len(cw) <= max_words:
                return combined.upper()
            return (" ".join(cw[:max_words]) + "...").upper()
    if sentences:
        first = sentences[0].strip()
        fw = first.split()
        if len(fw) <= max_words:
            if not first.endswith((".", "!", "?")):
                first += "..."
            return first.upper()
        return (" ".join(fw[:max_words]) + "...").upper()
    return (" ".join(clean_word(w["word"]) for w in words[:5]) + "...").upper()


def render_hook_frame(hook_text: str, font: ImageFont.FreeTypeFont, progress: float) -> Image.Image:
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    words = hook_text.split()
    if len(words) <= 4:
        lines = [hook_text]
    elif len(words) <= 8:
        mid = len(words) // 2
        lines = [" ".join(words[:mid]), " ".join(words[mid:])]
    else:
        third = len(words) // 3
        lines = [" ".join(words[:third]), " ".join(words[third:2*third]), " ".join(words[2*third:])]

    # Auto-reduce font for wide lines
    line_widths = []
    line_heights = []
    active_font = font
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=active_font)
        line_widths.append(bbox[2] - bbox[0])
        line_heights.append(bbox[3] - bbox[1])
    if max(line_widths) > WIDTH - 80:
        ratio = (WIDTH - 80) / max(line_widths)
        active_font = load_font(max(60, int(HOOK_FONT_SIZE * ratio)))
        line_widths = []
        line_heights = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=active_font)
            line_widths.append(bbox[2] - bbox[0])
            line_heights.append(bbox[3] - bbox[1])

    total_h = sum(line_heights) + 20 * (len(lines) - 1)
    start_y = HOOK_Y - total_h // 2

    # Fade + scale animation
    if progress < 0.08:
        alpha = int(255 * (progress / 0.08))
        scale = 0.5 + 0.5 * (progress / 0.08)
    elif progress > 0.85:
        alpha = int(255 * ((1.0 - progress) / 0.15))
        scale = 1.0
    else:
        alpha = 255
        scale = 1.0

    hook_fill = (HOOK_COLOR[0], HOOK_COLOR[1], HOOK_COLOR[2], alpha)
    shadow_fill = (0, 0, 0, alpha)

    for i, line in enumerate(lines):
        x = (WIDTH - line_widths[i]) // 2
        y = start_y + i * (line_heights[i] + 20)
        # Shadow
        for dx, dy in SHADOW_OFFSETS:
            draw.text((x + dx, y + dy), line, fill=shadow_fill, font=active_font)
        # Text with stroke
        draw.text((x, y), line, fill=hook_fill, font=active_font,
                  stroke_width=3, stroke_fill=shadow_fill)

    return img


# === TOP HOOK (persistent) ===
def render_top_hook(img: Image.Image, text: str, font: ImageFont.FreeTypeFont):
    """Render persistent top hook caption with drop shadow (no background)."""
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]

    # Center horizontally
    x = (WIDTH - tw) // 2
    y = TOP_HOOK_Y

    # Drop shadow for contrast (same as bottom captions)
    for dx, dy in SHADOW_OFFSETS:
        draw.text((x + dx, y + dy), text, fill=SHADOW_COLOR, font=font)

    # Text with stroke
    draw.text((x, y), text, fill=TOP_HOOK_COLOR, font=font,
              stroke_width=3, stroke_fill=(0, 0, 0, 255))


# === CAPTION RENDERING ===
def draw_word_shadow(draw, x, y, text, font):
    """Draw drop shadow behind text (no background pill)."""
    for dx, dy in SHADOW_OFFSETS:
        draw.text((x + dx, y + dy), text, fill=SHADOW_COLOR, font=font)


def render_scaled_word(img: Image.Image, text: str, cx: int, cy: int,
                       font: ImageFont.FreeTypeFont, color: tuple,
                       scale: float):
    """Render a single word at a given scale, centered on (cx, cy)."""
    # Get full-size metrics
    temp_draw = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bbox = temp_draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]

    pad = 30
    temp = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    td = ImageDraw.Draw(temp)

    # Shadow
    for dx, dy in SHADOW_OFFSETS:
        td.text((pad + dx, pad + dy), text, fill=SHADOW_COLOR, font=font)
    # Stroke + fill
    td.text((pad, pad), text, fill=color, font=font,
            stroke_width=STROKE_WIDTH, stroke_fill=STROKE_COLOR)

    # Scale
    new_w = max(1, int(temp.width * scale))
    new_h = max(1, int(temp.height * scale))
    temp = temp.resize((new_w, new_h), Image.LANCZOS)

    # Paste centered on (cx, cy)
    paste_x = cx - new_w // 2
    paste_y = cy - new_h // 2
    # Clamp to image bounds
    paste_x = max(0, min(WIDTH - new_w, paste_x))
    paste_y = max(0, min(HEIGHT - new_h, paste_y))
    img.alpha_composite(temp, (paste_x, paste_y))

    return w  # Return full-size width for layout


def render_caption_frame(
    group: list[dict],
    current_time: float,
    font: ImageFont.FreeTypeFont,
    highlight_font: ImageFont.FreeTypeFont,
) -> Image.Image:
    """Render caption frame with word-by-word bounce animation."""
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Build word metrics
    word_data = []
    for i, w in enumerate(group):
        text = clean_word(w["word"]).upper()
        is_emph = is_emphasis(text)
        f = highlight_font if is_emph else font
        color = HIGHLIGHT_COLOR if is_emph else FONT_COLOR

        bbox = draw.textbbox((0, 0), text, font=f)
        w_width = bbox[2] - bbox[0]
        w_height = bbox[3] - bbox[1]

        # Is this word currently being spoken?
        is_active = w["start"] <= current_time <= w["end"] + 0.1

        # Calculate bounce
        time_since = current_time - w["start"]
        frames_since = int(time_since * FPS)
        scale = bounce_scale(frames_since) if time_since >= 0 else 0.0

        # Active word gets pop color if not already emphasized
        if is_active and not is_emph:
            color = ACTIVE_COLOR

        word_data.append({
            "text": text, "width": w_width, "height": w_height,
            "font": f, "color": color, "scale": scale,
            "is_active": is_active, "visible": time_since >= 0,
        })

    # Only show visible words
    visible = [wd for wd in word_data if wd["visible"]]
    if not visible:
        return img

    # Split into lines
    lines = split_into_lines(visible)

    # Get max height across all words for consistent line height
    max_h = max(wd["height"] for wd in visible)

    # Render each line
    for line_idx, line_words in enumerate(lines):
        # Calculate total width at current scales
        total_w = sum(int(wd["width"] * max(wd["scale"], 0.01)) for wd in line_words)
        total_w += WORD_SPACING * (len(line_words) - 1)

        # Cap line width to screen with margins
        margin = 40
        if total_w > WIDTH - margin * 2:
            # Scale down all words proportionally
            scale_factor = (WIDTH - margin * 2) / total_w
            for wd in line_words:
                wd["width"] = int(wd["width"] * scale_factor)
                wd["height"] = int(wd["height"] * scale_factor)
                wd["font"] = load_font(int(FONT_SIZE * scale_factor)) if not is_emphasis(wd["text"]) else load_font(int(HIGHLIGHT_FONT_SIZE * scale_factor))
            total_w = sum(wd["width"] for wd in line_words) + WORD_SPACING * (len(line_words) - 1)
            max_h = max(wd["height"] for wd in line_words)

        line_y = CAPTION_Y + line_idx * (max_h + LINE_SPACING)
        start_x = max(margin, (WIDTH - total_w) // 2)

        x = start_x
        for wd in line_words:
            effective_w = int(wd["width"] * max(wd["scale"], 0.01))
            cx = x + effective_w // 2
            cy = line_y + max_h // 2

            if wd["scale"] < 0.98:
                # Bouncing: render scaled
                render_scaled_word(img, wd["text"], cx, cy, wd["font"], wd["color"], wd["scale"])
            else:
                # Full size: render directly (fast path)
                dx = x
                dy = line_y + (max_h - wd["height"]) // 2
                draw_word_shadow(draw, dx, dy, wd["text"], wd["font"])
                draw.text((dx, dy), wd["text"], fill=wd["color"], font=wd["font"],
                          stroke_width=STROKE_WIDTH, stroke_fill=STROKE_COLOR)

            x += effective_w + WORD_SPACING

    return img


# === TRANSCRIPT HELPERS ===
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


# === ZOOM ===
def apply_zoom_effect(input_path: str, output_path: str, zoom_pct: float = 3.0) -> str:
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", input_path],
        capture_output=True, text=True
    )
    duration = float(result.stdout.strip())
    total_frames = int(duration * 25)
    zoom_end = 1.0 + zoom_pct / 100.0
    zoom_expr = f"'min({zoom_end},1+({zoom_pct/100}/{total_frames})*on)'"
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", f"zoompan=z={zoom_expr}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1080x1920:fps=25",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy", "-movflags", "+faststart", output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return output_path if result.returncode == 0 else None


# === MAIN PIPELINE ===
def add_captions(
    video_path: str,
    words: list[dict],
    output_path: str,
    clip_duration: float = None,
    show_hook: bool = True,
    apply_zoom: bool = True,
    top_hook: str = "",
) -> str:
    video_path = Path(video_path).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not words:
        shutil.copy2(str(video_path), str(output_path))
        return str(output_path)

    if clip_duration is None:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True
        )
        clip_duration = float(result.stdout.strip())

    # Optional zoom
    zoom_input = str(video_path)
    zoom_tmp = None
    if apply_zoom:
        import tempfile as tf
        zoom_tmp = tf.NamedTemporaryFile(suffix='.mp4', delete=False, prefix='zoom_').name
        print("Applying zoom...")
        if not apply_zoom_effect(str(video_path), zoom_tmp):
            zoom_tmp = None
            zoom_input = str(video_path)
        else:
            zoom_input = zoom_tmp

    total_frames = int(clip_duration * FPS)
    font = load_font(FONT_SIZE)
    highlight_font = load_font(HIGHLIGHT_FONT_SIZE)
    hook_font = load_font(HOOK_FONT_SIZE)

    top_hook_font = load_font(TOP_HOOK_FONT_SIZE) if top_hook else None
    if top_hook:
        print(f"Top hook: {top_hook!r}")
    hook_text = extract_hook_text(words) if show_hook else ""
    hook_frames = int(HOOK_DURATION * FPS) if hook_text else 0

    groups = group_words(words)
    group_timeline = []
    for group in groups:
        group_timeline.append({
            "words": group,
            "start": group[0]["start"],
            "end": group[-1]["end"],
        })

    frame_dir = tempfile.mkdtemp(prefix="captions_")
    print(f"Rendering {total_frames} frames (hook: {hook_text!r})...")
    print(f"Font: Montserrat ExtraBold {FONT_SIZE}/{HIGHLIGHT_FONT_SIZE}px, ALL CAPS, no BG pill")

    last_img = None
    last_key = None

    for frame_idx in range(total_frames):
        t = frame_idx / FPS

        # Hook phase
        if frame_idx < hook_frames and hook_text:
            progress = frame_idx / hook_frames
            key = f"hook_{int(progress * 20)}"
            if key != last_key:
                last_img = render_hook_frame(hook_text, hook_font, progress)
                last_key = key
            if top_hook and top_hook_font:
                render_top_hook(last_img, top_hook, top_hook_font)
            last_img.save(os.path.join(frame_dir, f"frame_{frame_idx:06d}.png"))
            continue

        # Find active group
        active_group = None
        for gt in group_timeline:
            if gt["start"] - 0.1 <= t <= gt["end"] + 0.15:
                active_group = gt
                break

        if active_group is None:
            key = "empty"
            if key != last_key:
                last_img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
                if top_hook and top_hook_font:
                    render_top_hook(last_img, top_hook, top_hook_font)
                last_key = key
            last_img.save(os.path.join(frame_dir, f"frame_{frame_idx:06d}.png"))
            continue

        # Build cache key from word visibility states
        word_states = []
        for w in active_group["words"]:
            ts = t - w["start"]
            fs = int(ts * FPS) if ts >= 0 else -1
            word_states.append(min(fs, BOUNCE_FRAMES + 1))
        key = f"{id(active_group)}_{tuple(word_states)}"

        if key != last_key:
            last_img = render_caption_frame(active_group["words"], t, font, highlight_font)
            if top_hook and top_hook_font:
                render_top_hook(last_img, top_hook, top_hook_font)
            last_key = key

        last_img.save(os.path.join(frame_dir, f"frame_{frame_idx:06d}.png"))

    # Composite
    print("Compositing...")
    caption_video = os.path.join(frame_dir, "captions.mov")
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(FPS),
        "-i", os.path.join(frame_dir, "frame_%06d.png"),
        "-c:v", "png", "-pix_fmt", "rgba", caption_video,
    ], capture_output=True, text=True)

    result = subprocess.run([
        "ffmpeg", "-y",
        "-i", zoom_input, "-i", caption_video,
        "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy", "-movflags", "+faststart",
        str(output_path),
    ], capture_output=True, text=True)

    shutil.rmtree(frame_dir, ignore_errors=True)
    if zoom_tmp and os.path.exists(zoom_tmp):
        os.unlink(zoom_tmp)

    if result.returncode != 0:
        print(f"ERROR: {result.stderr[-500:]}")
        return None
    if output_path.exists():
        print(f"Done: {output_path.name} ({output_path.stat().st_size/1024/1024:.1f}MB)")
        return str(output_path)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", required=True)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--no-hook", action="store_true")
    parser.add_argument("--no-zoom", action="store_true")
    args = parser.parse_args()
    with open(args.transcript) as f:
        transcript = json.load(f)
    words = get_words_for_clip(transcript, args.start, args.end)
    print(f"Found {len(words)} words")
    add_captions(args.video, words, args.output, args.end - args.start,
                 show_hook=not args.no_hook, apply_zoom=not args.no_zoom)

if __name__ == "__main__":
    main()
