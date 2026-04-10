#!/usr/bin/env python3
"""
Phase 1 Growth Pipeline Orchestrator.
Processes Top Gear episodes into captioned TikTok clips and posts them.
Processes episodes into captioned TikTok clips with SFX.

Usage:
    python3 scripts/pipeline_growth.py process input/episodes/S01E01.mp4
    python3 scripts/pipeline_growth.py post
    python3 scripts/pipeline_growth.py status
"""

import argparse
import json
import os
import random
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
CONFIG_PATH = PROJECT_ROOT / "config.json"
EPISODES_DIR = PROJECT_ROOT / "input" / "episodes"
CLIPS_DIR = PROJECT_ROOT / "output" / "clips"
CAPTIONED_DIR = PROJECT_ROOT / "output" / "captioned"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from transcribe import transcribe_episode
from clip_selector import select_clips_heuristic
from cut_clips import cut_clip
from add_captions import add_captions, get_words_for_clip
from generate_top_hook import generate_hook_from_transcript
from add_effects import find_zoom_moments, add_sfx
from generate_post_caption import generate_post_caption
from auto_review import run_post_pipeline_review


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(config: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    # Ensure trending columns exist
    try:
        conn.execute("SELECT trending_video_path FROM videos LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE videos ADD COLUMN trending_video_path TEXT")
        conn.commit()
    return conn


def send_telegram(message: str):
    notify_script = PROJECT_ROOT / "scripts" / "notify_telegram.sh"
    if notify_script.exists():
        subprocess.run(["bash", str(notify_script), "--message", message], capture_output=True)


def process_episode(episode_path: str, max_clips: int = 12) -> list[dict]:
    """Full pipeline: transcribe -> select -> cut (face-tracked) -> caption -> SFX -> DB."""
    episode_path = Path(episode_path).resolve()
    if not episode_path.exists():
        print(f"ERROR: Episode not found: {episode_path}")
        return []

    episode_name = episode_path.stem
    print(f"\n{'='*60}")
    print(f"PROCESSING: {episode_name}")
    print(f"{'='*60}\n")

    db = get_db()
    existing = db.execute("SELECT * FROM episodes WHERE filename = ?", (episode_path.name,)).fetchone()
    if existing and existing["clips_extracted"] > 0:
        print(f"Already processed: {existing['clips_extracted']} clips extracted, {existing['clips_posted']} posted")
        db.close()
        return []

    # Step 1: Transcribe
    print("[1/5] Transcribing episode...")
    transcript = transcribe_episode(str(episode_path))
    transcript_path = PROJECT_ROOT / "data" / "transcripts" / f"{episode_name}.json"

    # Step 2: Select best moments (shorter clips for growth, filters MUSIC)
    print(f"\n[2/5] Selecting top {max_clips} moments...")
    clips = select_clips_heuristic(transcript, max_clips, episode_name=episode_name)
    if not clips:
        print("WARNING: No suitable clips found.")
        db.close()
        return []
    print(f"Selected {len(clips)} clips")

    # Step 3-5: Cut, caption, SFX
    print(f"\n[3/5] Cutting {len(clips)} clips with face tracking...")
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    CAPTIONED_DIR.mkdir(parents=True, exist_ok=True)
    
    results = []
    for i, clip in enumerate(clips):
        clip_filename = f"{episode_name}_{clip['name']}.mp4"
        clip_path = CLIPS_DIR / clip_filename
        captioned_path = CAPTIONED_DIR / clip_filename

        print(f"\n--- Clip {i+1}/{len(clips)}: {clip['start_sec']:.1f}s - {clip['end_sec']:.1f}s ---")

        # Cut with face tracking
        cut_result = cut_clip(str(episode_path), clip["start_sec"], clip["end_sec"], str(clip_path))
        if not cut_result:
            print(f"  SKIP: Failed to cut clip {i+1}")
            continue

        # Add captions
        print(f"  [4/5] Adding captions...")
        words = get_words_for_clip(transcript, clip["start_sec"], clip["end_sec"])
        # Generate persistent top hook
        clip_num = i + 1
        top_hook = generate_hook_from_transcript(words, episode_name, clip_num)
        # Generate AI post caption + hashtags
        post_caption = generate_post_caption(words, episode_name, top_hook, clip_num=clip_num)

        caption_result = add_captions(str(clip_path), words, str(captioned_path), top_hook=top_hook)

        if not caption_result:
            print(f"  WARNING: Caption failed, using uncaptioned clip")
            captioned_path = clip_path

        # Add SFX (whoosh + bass hits)
        print(f"  [5/5] Adding sound effects...")
        zoom_moments = find_zoom_moments(words, max_zooms=4)
        if zoom_moments:
            moments_str = ", ".join(f"{m:.1f}s" for m in zoom_moments)
            print(f"    Zoom/bass moments: {moments_str}")
        import tempfile as _tf
        sfx_tmp = _tf.NamedTemporaryFile(suffix=".mp4", delete=False, prefix="sfx_").name
        add_sfx(str(captioned_path), sfx_tmp, zoom_moments)
        # Replace captioned with SFX version
        if os.path.exists(sfx_tmp) and os.path.getsize(sfx_tmp) > 0:
            import shutil
            shutil.move(sfx_tmp, str(captioned_path))
        elif os.path.exists(sfx_tmp):
            os.unlink(sfx_tmp)

        # Use clip-specific hashtags
        hashtags = ""  # hashtags now included in post_caption

        # Store in DB
        db.execute("""
            INSERT INTO scripts (hook_text, narration, caption, hashtags, status)
            VALUES (?, ?, ?, ?, 'ready')
        """, (
            clip.get("text_preview", "")[:100],
            clip.get("text_preview", ""),
            post_caption,
            hashtags,
        ))
        script_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]

        db.execute("""
            INSERT INTO videos (script_id, video_path, trending_video_path, duration_seconds, status, content_type, source_episode, clip_start_sec, clip_end_sec, cost_usd, trending_track, top_hook)
            VALUES (?, ?, NULL, ?, 'ready', 'clip', ?, ?, ?, 0.0, NULL, ?)
        """, (
            script_id,
            str(captioned_path),
            clip["duration"],
            episode_path.name,
            clip["start_sec"],
            clip["end_sec"],
            top_hook,
        ))

        # Garbage collection: delete raw cuts after captioned version is built
        import os as _os
        if captioned_path.exists() and clip_path.exists() and str(clip_path) != str(captioned_path):
            _os.unlink(str(clip_path))

        db.commit()
        results.append({
            "path": str(captioned_path),
            "top_hook": top_hook,
            "duration": clip["duration"],
            "start": clip["start_sec"],
            "end": clip["end_sec"],
        })

    db.execute("""
        INSERT OR REPLACE INTO episodes (filename, title, duration_seconds, transcript_path, clips_extracted, processed_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        episode_path.name,
        episode_name,
        transcript["duration"],
        str(transcript_path),
        len(results),
        datetime.now().isoformat(),
    ))
    db.commit()
    db.close()

    print(f"\n{'='*60}")
    print(f"DONE: {len(results)} clips ready for posting")
    print(f"{'='*60}\n")

    send_telegram(f"Episode processed: {episode_name}\n{len(results)} clips ready for posting")

    # Run auto-review on new clips
    if results:
        try:
            print("\n[AUTO-REVIEW] Reviewing new clips...")
            run_post_pipeline_review()
        except Exception as e:
            print(f"[AUTO-REVIEW] Review failed (non-fatal): {e}")

    return results


def post_next_clip() -> bool:
    """Post the next ready clip to TikTok."""
    config = load_config()
    db = get_db()

    clip = db.execute("""
        SELECT v.*, s.caption, s.hashtags, s.hook_text
        FROM videos v
        JOIN scripts s ON v.script_id = s.id
        WHERE v.status = 'ready'
        ORDER BY v.created_at ASC
        LIMIT 1
    """).fetchone()

    if not clip:
        print("No clips ready for posting.")
        unprocessed = check_for_new_episodes()
        if unprocessed:
            print(f"Found {len(unprocessed)} new episodes to process.")
            for ep in unprocessed[:1]:
                process_episode(ep)
            return post_next_clip()
        send_telegram("No clips available. Drop a new episode into input/episodes/")
        db.close()
        return False

    video_path = clip["video_path"]

    if not Path(video_path).exists():
        print(f"ERROR: Video file missing: {video_path}")
        db.execute("UPDATE videos SET status = 'failed' WHERE id = ?", (clip["id"],))
        db.commit()
        db.close()
        return False

    caption = clip["caption"] or ""
    if clip["hashtags"] and clip["hashtags"] not in caption:
        caption = f"{caption} {clip['hashtags']}"

    cookies = config["tiktok"]["cookies_path"]
    post_script = PROJECT_ROOT / "scripts" / "post.sh"

    print(f"Posting: {Path(video_path).name}")
    print(f"Caption: {caption[:80]}...")

    result = subprocess.run(
        ["bash", str(post_script), "--video", video_path, "--caption", caption, "--cookies", cookies],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT),
    )

    if result.returncode == 0:
        db.execute("UPDATE videos SET status = 'posted', posted_at = ? WHERE id = ?",
                    (datetime.now().isoformat(), clip["id"]))
        db.execute("UPDATE scripts SET status = 'posted' WHERE id = ?", (clip["script_id"],))
        if clip["source_episode"]:
            db.execute("UPDATE episodes SET clips_posted = clips_posted + 1 WHERE filename = ?",
                       (clip["source_episode"],))
        db.commit()
        send_telegram(f"Posted to TikTok!\n{clip['hook_text'] or Path(video_path).name}")
        print("SUCCESS: Posted to TikTok")
        db.close()
        return True
    else:
        print(f"FAILED: {result.stdout}\n{result.stderr}")
        send_telegram(f"FAILED to post: {Path(video_path).name}\n{result.stderr[:200]}")
        db.close()
        return False


def check_for_new_episodes() -> list[str]:
    if not EPISODES_DIR.exists():
        return []
    db = get_db()
    processed = {row["filename"] for row in db.execute("SELECT filename FROM episodes").fetchall()}
    db.close()
    video_exts = {".mp4", ".mkv", ".avi", ".mov", ".ts", ".webm"}
    return sorted(str(f) for f in EPISODES_DIR.iterdir()
                  if f.suffix.lower() in video_exts and f.name not in processed)


def show_status():
    config = load_config()
    db = get_db()

    ready = db.execute("SELECT COUNT(*) as c FROM videos WHERE status = 'ready'").fetchone()["c"]
    posted = db.execute("SELECT COUNT(*) as c FROM videos WHERE status = 'posted'").fetchone()["c"]
    failed = db.execute("SELECT COUNT(*) as c FROM videos WHERE status = 'failed'").fetchone()["c"]
    episodes = db.execute("SELECT COUNT(*) as c FROM episodes").fetchone()["c"]
    new_episodes = len(check_for_new_episodes())

    print(f"\n=== TikTok Pipeline Status ===")
    print(f"Phase: {'GROWTH' if config['sub_1k'] else 'MONETIZATION'}")
    print(f"Followers: {config['follower_count']}")
    print(f"Episodes processed: {episodes}")
    print(f"New episodes waiting: {new_episodes}")
    print(f"Clips ready: {ready}")
    print(f"Clips posted: {posted}")
    print(f"Clips failed: {failed}")
    print(f"Budget: ${config['budget']['spent_usd']:.2f} / ${config['budget']['monthly_limit_usd']:.2f}")
    print()

    recent = db.execute("""
        SELECT v.*, s.hook_text FROM videos v
        JOIN scripts s ON v.script_id = s.id
        WHERE v.status = 'posted'
        ORDER BY v.posted_at DESC LIMIT 5
    """).fetchall()

    if recent:
        print("Recent posts:")
        for r in recent:
            print(f"  {r['posted_at'][:16]} | {r['hook_text'][:60] if r['hook_text'] else Path(r['video_path']).name}")
    db.close()


def main():
    parser = argparse.ArgumentParser(description="TikTok Growth Pipeline")
    subparsers = parser.add_subparsers(dest="command")

    proc = subparsers.add_parser("process", help="Process an episode into clips")
    proc.add_argument("episode", help="Path to episode file")
    proc.add_argument("--max-clips", type=int, default=12, help="Max clips to extract")

    post_cmd = subparsers.add_parser("post", help="Post the next ready clip")

    subparsers.add_parser("process-new", help="Process all new episodes")
    subparsers.add_parser("status", help="Show pipeline status")

    args = parser.parse_args()

    if args.command == "process":
        process_episode(args.episode, args.max_clips)
    elif args.command == "post":
        post_next_clip()
    elif args.command == "process-new":
        new_eps = check_for_new_episodes()
        if not new_eps:
            print("No new episodes found in input/episodes/")
        for ep in new_eps:
            process_episode(ep)
    elif args.command == "status":
        show_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
