#!/usr/bin/env python3
"""
Post specific clips to TikTok. Handles cookie auth and DB updates.

Usage:
    python3 scripts/batch_post.py 4 2 6
    python3 scripts/batch_post.py 4 --headless
"""

import argparse
import json
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
CONFIG_PATH = PROJECT_ROOT / "config.json"

EPISODE = "S10E04 - Botswana Special"

CAPTIONS = {
    4: "If you run out of water, you will die. If your car breaks down you cant be rescued, you will die. #topgear #fyp #foryou #jeremyclarkson #cars #adventure #africa #botswana",
    2: "What the hell have you done man?! The Opel Kadett vs the Mercedes in Botswana #topgear #fyp #foryou #jeremyclarkson #funny #comedy #botswana #cars",
    6: "Two cows three cows... YES! We crossed the Makgadikgadi! #topgear #fyp #foryou #adventure #africa #botswana #roadtrip #cars",
    10: "Ordinary second-hand road cars. Proper off-roading. This is what Top Gear is about. #topgear #fyp #foryou #offroad #adventure #africa #botswana #cars",
}


def get_video_path(clip_num: int) -> str:
    return str(PROJECT_ROOT / "output" / "trending" / f"{EPISODE}_clip_{clip_num:03d}.mp4")


def update_db(clip_num: int, success: bool):
    db = sqlite3.connect(str(DB_PATH))
    if success:
        db.execute(
            "UPDATE videos SET status = 'posted', posted_at = ? WHERE source_episode = ? AND video_path LIKE ?",
            (datetime.now().isoformat(), f"{EPISODE}.mkv", f"%clip_{clip_num:03d}%")
        )
        db.execute(
            "UPDATE scripts SET status = 'posted' WHERE id IN (SELECT script_id FROM videos WHERE source_episode = ? AND video_path LIKE ?)",
            (f"{EPISODE}.mkv", f"%clip_{clip_num:03d}%")
        )
        db.execute(
            "UPDATE episodes SET clips_posted = clips_posted + 1 WHERE filename = ?",
            (f"{EPISODE}.mkv",)
        )
    else:
        db.execute(
            "UPDATE videos SET status = 'failed' WHERE source_episode = ? AND video_path LIKE ?",
            (f"{EPISODE}.mkv", f"%clip_{clip_num:03d}%")
        )
    db.commit()
    db.close()


def send_telegram(message: str):
    script = PROJECT_ROOT / "scripts" / "notify_telegram.sh"
    if script.exists():
        subprocess.run(["bash", str(script), "--message", message], capture_output=True)


def post_clip(clip_num: int, headless: bool = False, delay: int = 0) -> bool:
    from tiktok_uploader.upload import upload_video

    video = get_video_path(clip_num)
    desc = CAPTIONS.get(clip_num, f"Top Gear Botswana #topgear #fyp #foryou")

    if not Path(video).exists():
        print(f"ERROR: Video not found: {video}")
        return False

    if delay > 0:
        print(f"Waiting {delay}s between posts (anti-detection)...")
        time.sleep(delay)

    print(f"\nPosting clip {clip_num:03d}...")
    print(f"  Video: {Path(video).name}")
    print(f"  Caption: {desc[:80]}...")
    print(f"  Headless: {headless}")

    try:
        signal.alarm(180)
        result = upload_video(
            filename=video,
            description=desc,
            cookies="cookies.txt",
            headless=headless,
        )
        signal.alarm(0)
        print(f"  Result: {result}")
        update_db(clip_num, True)
        send_telegram(f"Posted clip {clip_num:03d} to TikTok!\n{desc[:80]}...")
        return True
    except Exception as e:
        signal.alarm(0)
        print(f"  FAILED: {type(e).__name__}: {e}")
        update_db(clip_num, False)
        send_telegram(f"FAILED to post clip {clip_num:03d}: {e}")
        return False


def main():
    signal.signal(signal.SIGALRM, lambda s, f: (_ for _ in ()).throw(TimeoutError("Upload timed out")))

    parser = argparse.ArgumentParser(description="Post clips to TikTok")
    parser.add_argument("clips", nargs="+", type=int, help="Clip numbers to post")
    parser.add_argument("--headless", action="store_true", help="Use headless browser")
    parser.add_argument("--delay", type=int, default=60, help="Seconds between posts")
    args = parser.parse_args()

    results = []
    for i, clip_num in enumerate(args.clips):
        delay = args.delay if i > 0 else 0
        success = post_clip(clip_num, headless=args.headless, delay=delay)
        results.append((clip_num, success))

    print(f"\n{'='*40}")
    print("Posting Results:")
    for clip_num, success in results:
        status = "POSTED" if success else "FAILED"
        print(f"  Clip {clip_num:03d}: {status}")


if __name__ == "__main__":
    main()
