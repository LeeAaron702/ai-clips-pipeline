#!/usr/bin/env python3
"""
Reprocess a single clip through caption + trending audio pipeline.
Uses existing raw cuts from output/clips/.

Usage:
    python3 scripts/reprocess_clip.py 4
    python3 scripts/reprocess_clip.py 2 --no-hook --no-zoom
"""

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from add_captions import add_captions, get_words_for_clip
from add_trending_audio import add_trending_audio
from generate_top_hook import generate_hook_from_transcript

CLIPS_DIR = PROJECT_ROOT / "output" / "clips"
CAPTIONED_DIR = PROJECT_ROOT / "output" / "captioned"
TRENDING_DIR = PROJECT_ROOT / "output" / "trending"
TRANSCRIPT_DIR = PROJECT_ROOT / "data" / "transcripts"

EPISODE = "S10E04 - Botswana Special"


def reprocess(clip_num: int, show_hook: bool = True, apply_zoom: bool = True):
    clip_name = f"{EPISODE}_clip_{clip_num:03d}.mp4"
    raw_clip = CLIPS_DIR / clip_name
    captioned_out = CAPTIONED_DIR / clip_name
    trending_out = TRENDING_DIR / clip_name

    if not raw_clip.exists():
        print(f"ERROR: Raw clip not found: {raw_clip}")
        sys.exit(1)

    transcript_path = TRANSCRIPT_DIR / f"{EPISODE}.json"
    if not transcript_path.exists():
        print(f"ERROR: Transcript not found: {transcript_path}")
        sys.exit(1)

    with open(transcript_path) as f:
        transcript = json.load(f)

    import sqlite3
    db = sqlite3.connect(str(PROJECT_ROOT / "data" / "pipeline.db"))
    db.row_factory = sqlite3.Row
    row = db.execute(
        "SELECT clip_start_sec, clip_end_sec FROM videos WHERE source_episode = ? AND video_path LIKE ?",
        (f"{EPISODE}.mkv", f"%clip_{clip_num:03d}%")
    ).fetchone()
    db.close()

    if not row:
        print(f"ERROR: Clip {clip_num:03d} not found in database")
        sys.exit(1)

    start_sec = row["clip_start_sec"]
    end_sec = row["clip_end_sec"]
    duration = end_sec - start_sec

    print(f"Reprocessing clip {clip_num:03d} ({start_sec:.1f}s - {end_sec:.1f}s, {duration:.1f}s)")

    words = get_words_for_clip(transcript, start_sec, end_sec)
    print(f"Found {len(words)} words")

    top_hook = generate_hook_from_transcript(words, EPISODE, clip_num)

    print("\n[1/2] Adding captions + zoom...")
    caption_result = add_captions(str(raw_clip), words, str(captioned_out), top_hook=top_hook,
                                   clip_duration=duration, show_hook=show_hook,
                                   apply_zoom=apply_zoom)
    if not caption_result:
        print("ERROR: Captioning failed")
        sys.exit(1)

    print("\n[2/2] Adding trending audio...")
    trending_result = add_trending_audio(str(captioned_out), str(trending_out))
    if not trending_result:
        print("WARNING: Trending audio failed, captioned version still available")

    print(f"\nDone! Output:")
    print(f"  Captioned: {captioned_out}")
    print(f"  Trending:  {trending_out}")
    return str(trending_out) if trending_result else str(captioned_out)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reprocess a single clip")
    parser.add_argument("clip_num", type=int, help="Clip number (e.g. 4 for clip_004)")
    parser.add_argument("--no-hook", action="store_true", help="Disable hook text overlay")
    parser.add_argument("--no-zoom", action="store_true", help="Disable zoom effect")
    args = parser.parse_args()
    reprocess(args.clip_num, show_hook=not args.no_hook, apply_zoom=not args.no_zoom)
