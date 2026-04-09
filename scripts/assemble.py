#!/usr/bin/env python3
"""
TikTok Video Assembly Pipeline
Stitches AI-generated clips + voiceover + text overlays → final TikTok-ready MP4

Usage:
    python3 assemble.py \
        --clips clip1.mp4,clip2.mp4,clip3.mp4 \
        --audio voiceover.wav \
        --hook "This hub replaced my dock" \
        --cta "Link in bio" \
        --output output.mp4
"""

import argparse
import subprocess
import tempfile
import os
import json
from pathlib import Path


def get_duration(filepath):
    """Get video/audio duration in seconds."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", filepath],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def get_resolution(filepath):
    """Get video width and height."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "json", filepath],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    s = data["streams"][0]
    return int(s["width"]), int(s["height"])


def create_text_overlay(text, width, height, fontsize, position, duration_start, duration_end, output_path, opacity=1.0):
    """Create a transparent video with text overlay using Pillow + FFmpeg."""
    from PIL import Image, ImageDraw, ImageFont
    import math

    fps = 30
    total_frames = int((duration_end - duration_start) * fps)
    if total_frames <= 0:
        return None

    # Create frames
    frame_dir = tempfile.mkdtemp()

    try:
        font_path = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
        font = ImageFont.truetype(font_path, fontsize)
    except:
        font = ImageFont.load_default()

    for i in range(min(total_frames, 300)):  # Cap at 10 seconds
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Get text size
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]

        # Position
        if position == "center":
            x = (width - tw) // 2
            y = (height - th) // 2
        elif position == "bottom":
            x = (width - tw) // 2
            y = height - th - 80
        elif position == "top":
            x = (width - tw) // 2
            y = 80
        else:
            x = (width - tw) // 2
            y = (height - th) // 2

        # Draw shadow
        alpha = int(255 * opacity)
        draw.text((x + 2, y + 2), text, fill=(0, 0, 0, alpha), font=font)
        # Draw text
        draw.text((x, y), text, fill=(255, 255, 255, alpha), font=font)

        img.save(os.path.join(frame_dir, f"frame_{i:05d}.png"))

    # Convert frames to video
    subprocess.run([
        "ffmpeg", "-y", "-framerate", str(fps),
        "-i", os.path.join(frame_dir, "frame_%05d.png"),
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuva420p",
        output_path
    ], capture_output=True)

    # Cleanup
    for f in os.listdir(frame_dir):
        os.remove(os.path.join(frame_dir, f))
    os.rmdir(frame_dir)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="TikTok Video Assembly")
    parser.add_argument("--clips", required=True, help="Comma-separated list of clip paths")
    parser.add_argument("--audio", help="Voiceover audio file")
    parser.add_argument("--music", help="Background music file")
    parser.add_argument("--hook", help="Hook text for first 3 seconds")
    parser.add_argument("--cta", help="CTA text for last 3 seconds")
    parser.add_argument("--compliance", default="Contains affiliate links | #ad", help="Compliance text")
    parser.add_argument("--output", required=True, help="Output file path")
    args = parser.parse_args()

    clips = args.clips.split(",")
    workdir = tempfile.mkdtemp()

    print("=== TikTok Video Assembly ===")

    # Step 1: Normalize all clips to 1080x1920 30fps
    print("[1/5] Normalizing clips to 1080x1920...")
    normalized = []
    for i, clip in enumerate(clips):
        norm = os.path.join(workdir, f"norm_{i}.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", clip.strip(),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,fps=30",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an",
            norm
        ], capture_output=True)
        normalized.append(norm)

    # Step 2: Concatenate clips
    print("[2/5] Concatenating clips...")
    concat_file = os.path.join(workdir, "concat.txt")
    with open(concat_file, "w") as f:
        for n in normalized:
            f.write(f"file '{n}'\n")

    concat_out = os.path.join(workdir, "concat.mp4")
    subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        concat_out
    ], capture_output=True)

    duration = get_duration(concat_out)
    print(f"   Duration: {duration:.1f}s")

    # Step 3: Burn text overlays using Pillow
    print("[3/5] Adding text overlays...")

    # We'll use a simpler approach: create individual text frame images
    # and overlay them at specific timestamps using FFmpeg's overlay filter
    # Since drawtext isn't available, we'll create text as image overlays

    # Create hook text image
    from PIL import Image, ImageDraw, ImageFont

    def make_text_image(text, width, height, fontsize, y_pos, output_path, opacity=255):
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", fontsize)
        except:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = (width - tw) // 2
        # Shadow
        draw.text((x + 2, y_pos + 2), text, fill=(0, 0, 0, opacity), font=font)
        # Text
        draw.text((x, y_pos), text, fill=(255, 255, 255, opacity), font=font)
        img.save(output_path)

    overlay_out = concat_out  # Start with concat, add overlays

    if args.hook:
        hook_img = os.path.join(workdir, "hook.png")
        make_text_image(args.hook, 1080, 1920, 64, 880, hook_img)

        hooked = os.path.join(workdir, "hooked.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", overlay_out, "-i", hook_img,
            "-filter_complex", "[0:v][1:v]overlay=0:0:enable='between(t,0,3)'",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an",
            hooked
        ], capture_output=True)
        overlay_out = hooked

    if args.compliance:
        comp_img = os.path.join(workdir, "compliance.png")
        make_text_image(args.compliance, 1080, 1920, 28, 1820, comp_img, opacity=180)

        comped = os.path.join(workdir, "comped.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", overlay_out, "-i", comp_img,
            "-filter_complex", "[0:v][1:v]overlay=0:0:enable='between(t,0,5)'",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an",
            comped
        ], capture_output=True)
        overlay_out = comped

    if args.cta:
        cta_img = os.path.join(workdir, "cta.png")
        make_text_image(args.cta, 1080, 1920, 52, 920, cta_img)

        cta_start = max(0, duration - 3)
        ctad = os.path.join(workdir, "ctad.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", overlay_out, "-i", cta_img,
            "-filter_complex", f"[0:v][1:v]overlay=0:0:enable='gte(t,{cta_start})'",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an",
            ctad
        ], capture_output=True)
        overlay_out = ctad

    # Step 4: Mix audio
    print("[4/5] Mixing audio...")
    if args.audio and args.music:
        audio_out = os.path.join(workdir, "final_audio.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", overlay_out, "-i", args.audio, "-i", args.music,
            "-filter_complex", "[1:a]apad[vo];[2:a]volume=0.15,apad[bg];[vo][bg]amix=inputs=2:duration=first[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            audio_out
        ], capture_output=True)
        overlay_out = audio_out
    elif args.audio:
        audio_out = os.path.join(workdir, "final_audio.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", overlay_out, "-i", args.audio,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            audio_out
        ], capture_output=True)
        overlay_out = audio_out
    elif args.music:
        audio_out = os.path.join(workdir, "final_audio.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-i", overlay_out, "-i", args.music,
            "-filter_complex", "[1:a]volume=0.30[bg]",
            "-map", "0:v", "-map", "[bg]",
            "-c:v", "copy", "-c:a", "aac", "-shortest",
            audio_out
        ], capture_output=True)
        overlay_out = audio_out

    # Step 5: Final encode
    print("[5/5] Final encode...")
    subprocess.run([
        "ffmpeg", "-y", "-i", overlay_out,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        args.output
    ], capture_output=True)

    # Report
    if os.path.exists(args.output):
        final_dur = get_duration(args.output)
        size = os.path.getsize(args.output) / (1024 * 1024)
        w, h = get_resolution(args.output)
        print(f"\n=== Done ===")
        print(f"Output: {args.output}")
        print(f"Duration: {final_dur:.1f}s")
        print(f"Resolution: {w}x{h}")
        print(f"Size: {size:.1f}MB")
    else:
        print("ERROR: Output file not created")

    # Cleanup
    import shutil
    shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    main()
