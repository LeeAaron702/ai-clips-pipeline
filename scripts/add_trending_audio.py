#!/usr/bin/env python3
"""
Mix trending audio underneath a TikTok clip at low volume.
Produces a second output file with trending audio to hack TikTok's algorithm.

The trending audio is played quietly (10-15% volume) under the original audio
so TikTok's content fingerprinting associates the video with a trending sound.

Usage:
    python3 scripts/add_trending_audio.py --video clip.mp4 --output clip_trending.mp4
    python3 scripts/add_trending_audio.py --video clip.mp4 --audio assets/trending_audio/track.mp3 --output clip_trending.mp4
"""

import argparse
import os
import random
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRENDING_DIR = PROJECT_ROOT / "assets" / "trending_audio"

# Volume for trending audio (0.0 - 1.0)
TRENDING_VOLUME = 0.12  # 12% - audible but doesn't compete with dialogue


def get_random_trending_audio():
    """Pick a random trending audio file from the assets folder."""
    if not TRENDING_DIR.exists():
        return None, None
    
    audio_exts = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
    tracks = [f for f in TRENDING_DIR.iterdir() if f.suffix.lower() in audio_exts]
    
    if not tracks:
        return None, None
    
    pick = random.choice(tracks)
    return str(pick), pick.stem


def add_trending_audio(video_path: str, output_path: str, audio_path: str = None, volume: float = TRENDING_VOLUME) -> str:
    """
    Mix trending audio under the video's original audio.
    The trending track is looped if shorter than the video, and faded out at the end.
    Returns output path on success.
    """
    video_path = Path(video_path).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    track_name = None
    if not audio_path:
        audio_path, track_name = get_random_trending_audio()
    else:
        track_name = Path(audio_path).stem
    
    if not audio_path or not Path(audio_path).exists():
        print("WARNING: No trending audio available. Skipping.")
        return None, None

    print(f"  Trending track: {track_name}")

    # Get video duration
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True
    )
    duration = float(result.stdout.strip())

    # Mix: loop trending audio, set volume, fade out last 2 seconds, combine with original
    fade_start = max(0, duration - 2)
    filter_complex = (
        f"[1:a]aloop=loop=-1:size=2e+09,atrim=0:{duration},"
        f"volume={volume},afade=t=out:st={fade_start}:d=2[trending];"
        f"[0:a][trending]amix=inputs=2:duration=first:dropout_transition=2[aout]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(audio_path),
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",  # Don't re-encode video
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        str(output_path),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"ERROR mixing trending audio: {result.stderr[-300:]}")
        return None

    if output_path.exists():
        size_mb = output_path.stat().st_size / (1024 * 1024)
        print(f"  Trending audio version: {output_path.name} ({size_mb:.1f}MB)")
        return str(output_path), track_name

    return None, None


def main():
    parser = argparse.ArgumentParser(description="Add trending audio to TikTok clips")
    parser.add_argument("--video", required=True, help="Input video")
    parser.add_argument("--audio", help="Specific trending audio file (random if omitted)")
    parser.add_argument("--output", required=True, help="Output video path")
    parser.add_argument("--volume", type=float, default=TRENDING_VOLUME, help="Trending audio volume (0.0-1.0)")
    args = parser.parse_args()

    add_trending_audio(args.video, args.output, args.audio, args.volume)


if __name__ == "__main__":
    main()
