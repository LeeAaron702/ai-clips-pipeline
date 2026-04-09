#!/usr/bin/env python3
"""
Persistent TikTok post scheduler.
Runs as a long-lived process on hermes. Posts clips at configured times.
Writes status to a log file that's easy to monitor.

Usage:
    python3 scripts/scheduler.py run          # Start the scheduler (foreground)
    python3 scripts/scheduler.py status       # Show current status
    python3 scripts/scheduler.py next         # Show next scheduled post
    python3 scripts/scheduler.py queue        # Show post queue

Run with nohup or launchd for persistence:
    nohup python3 scripts/scheduler.py run > logs/scheduler.log 2>&1 &
"""

import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
CONFIG_PATH = PROJECT_ROOT / "config.json"
STATUS_PATH = PROJECT_ROOT / "data" / "scheduler_status.json"
LOG_DIR = PROJECT_ROOT / "logs"

TZ = ZoneInfo("America/Los_Angeles")

# Posting schedule (PT times)
# A/B test: trending at prime times, captioned (no trending audio) at off-peak
POST_TIMES_TRENDING = ["07:00", "14:00", "20:00"]  # Prime times
POST_TIMES_CAPTIONED = ["10:00", "17:00"]  # Off-peak, no trending audio
POST_TIMES = sorted(POST_TIMES_TRENDING + POST_TIMES_CAPTIONED)
MIN_POST_INTERVAL_MINUTES = 90


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_next_clip():
    """Get the next ready clip to post."""
    db = get_db()
    clip = db.execute("""
        SELECT v.*, s.caption, s.hashtags, s.hook_text, v.trending_track, v.top_hook
        FROM videos v
        JOIN scripts s ON v.script_id = s.id
        WHERE v.status = 'ready'
        ORDER BY v.created_at ASC
        LIMIT 1
    """).fetchone()
    db.close()
    return clip


def get_queue():
    """Get all ready clips."""
    db = get_db()
    clips = db.execute("""
        SELECT v.id, v.source_episode, v.clip_start_sec, v.duration_seconds,
               s.hook_text, v.trending_track, v.top_hook, v.status
        FROM videos v
        JOIN scripts s ON v.script_id = s.id
        WHERE v.status = 'ready'
        ORDER BY v.created_at ASC
    """).fetchall()
    db.close()
    return clips


def get_stats():
    """Get posting statistics."""
    db = get_db()
    ready = db.execute("SELECT COUNT(*) as c FROM videos WHERE status='ready'").fetchone()["c"]
    posted = db.execute("SELECT COUNT(*) as c FROM videos WHERE status='posted'").fetchone()["c"]
    failed = db.execute("SELECT COUNT(*) as c FROM videos WHERE status='failed'").fetchone()["c"]
    last_post = db.execute(
        "SELECT posted_at FROM videos WHERE status='posted' ORDER BY posted_at DESC LIMIT 1"
    ).fetchone()
    db.close()
    return {
        "ready": ready, "posted": posted, "failed": failed,
        "last_post": last_post["posted_at"] if last_post else None,
    }


def post_clip(clip) -> bool:
    """Post a clip to TikTok using upload_tiktok.py."""
    # A/B test: use trending version at prime times, captioned at off-peak
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
    current_time = now_pt.strftime("%H:%M")
    use_trending = any(abs(int(current_time.split(":")[0]) - int(t.split(":")[0])) <= 1 for t in POST_TIMES_TRENDING)
    if use_trending and clip["trending_video_path"] and Path(clip["trending_video_path"]).exists():
        video_path = clip["trending_video_path"]
        print(f"  A/B: Using TRENDING version (prime time)")
    else:
        video_path = clip["video_path"]
        print(f"  A/B: Using CAPTIONED version (off-peak, no trending audio)")
    if not Path(video_path).exists():
        print(f"ERROR: Video not found: {video_path}")
        return False

    caption = clip["caption"] or ""
    if clip["hashtags"] and clip["hashtags"] not in caption:
        caption = f"{caption} {clip['hashtags']}"

    upload_script = PROJECT_ROOT / "scripts" / "upload_tiktok.py"
    env = os.environ.copy()
    env["PATH"] = f"/opt/homebrew/bin:{env.get('PATH', '')}"

    print(f"Posting clip {clip['id']}: {clip['hook_text'][:50]}...")
    result = subprocess.run(
        [sys.executable, str(upload_script),
         "--video", video_path,
         "--caption", caption,
         "--cookies", str(PROJECT_ROOT / "cookies.txt")],
        capture_output=True, text=True, timeout=300,
        cwd=str(PROJECT_ROOT), env=env,
    )

    success = result.returncode == 0
    db = get_db()
    now = datetime.now().isoformat()

    if success:
        db.execute("UPDATE videos SET status='posted', posted_at=? WHERE id=?", (now, clip["id"]))
        db.execute("UPDATE scripts SET status='posted' WHERE id=?", (clip["script_id"],))
        print(f"  POSTED successfully")
    else:
        db.execute("UPDATE videos SET status='failed' WHERE id=?", (clip["id"],))
        print(f"  FAILED: {result.stdout[-200:]}")
    db.commit()
    db.close()

    # Send telegram notification
    notify = PROJECT_ROOT / "scripts" / "notify_telegram.sh"
    msg = f"{'Posted' if success else 'FAILED'}: {clip['hook_text'][:60]}"
    if clip.get("trending_track"):
        msg += f"\nAudio: {clip['trending_track']}"
    subprocess.run(["bash", str(notify), "--message", msg], capture_output=True, env=env)

    return success


def update_status(state: str, next_post: str = None, extra: dict = None):
    """Write scheduler status to a JSON file for easy monitoring."""
    stats = get_stats()
    status = {
        "state": state,
        "updated": datetime.now(TZ).isoformat(),
        "next_post": next_post,
        "queue_size": stats["ready"],
        "total_posted": stats["posted"],
        "total_failed": stats["failed"],
        "last_post": stats["last_post"],
    }
    if extra:
        status.update(extra)
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATUS_PATH, "w") as f:
        json.dump(status, f, indent=2)


def get_next_post_time() -> datetime:
    """Calculate the next scheduled post time."""
    now = datetime.now(TZ)
    today = now.date()

    for time_str in POST_TIMES:
        h, m = map(int, time_str.split(":"))
        candidate = datetime(today.year, today.month, today.day, h, m, tzinfo=TZ)
        if candidate > now:
            return candidate

    # All times passed today, schedule for tomorrow's first slot
    tomorrow = today + timedelta(days=1)
    h, m = map(int, POST_TIMES[0].split(":"))
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, h, m, tzinfo=TZ)


def run_scheduler():
    """Main scheduler loop. Runs forever, posting at scheduled times."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    print(f"TikTok Post Scheduler started at {datetime.now(TZ).strftime('%Y-%m-%d %H:%M PT')}")
    print(f"Post times (PT): {', '.join(POST_TIMES)}")
    print(f"PID: {os.getpid()}")

    # Write PID file
    pid_path = PROJECT_ROOT / "data" / "scheduler.pid"
    with open(pid_path, "w") as f:
        f.write(str(os.getpid()))

    def handle_shutdown(sig, frame):
        print(f"\nShutting down scheduler (signal {sig})")
        update_status("stopped")
        pid_path.unlink(missing_ok=True)
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    while True:
        next_time = get_next_post_time()
        now = datetime.now(TZ)
        wait_seconds = (next_time - now).total_seconds()

        queue = get_queue()
        if not queue:
            update_status("idle_no_clips", next_post=next_time.isoformat())
            print(f"No clips in queue. Checking again in 30 minutes.")
            time.sleep(1800)
            continue

        update_status("waiting", next_post=next_time.strftime("%Y-%m-%d %H:%M PT"),
                      extra={"next_clip": queue[0]["hook_text"][:60] if queue else None})
        print(f"Next post: {next_time.strftime('%H:%M PT')} ({int(wait_seconds/60)}min away) | Queue: {len(queue)} clips")

        # Sleep until next post time (check every 60s for queue changes)
        while wait_seconds > 0:
            sleep_time = min(60, wait_seconds)
            time.sleep(sleep_time)
            wait_seconds -= sleep_time
            now = datetime.now(TZ)
            wait_seconds = (next_time - now).total_seconds()

        # Time to post
        clip = get_next_clip()
        if clip:
            update_status("posting", extra={"posting_clip": clip["hook_text"][:60]})
            success = post_clip(clip)
            if success:
                update_status("posted", extra={"last_posted": clip["hook_text"][:60]})
            else:
                update_status("post_failed", extra={"failed_clip": clip["hook_text"][:60]})
        else:
            update_status("idle_no_clips")

        # Brief cooldown after posting
        time.sleep(30)


def show_status():
    if STATUS_PATH.exists():
        with open(STATUS_PATH) as f:
            status = json.load(f)
        print(json.dumps(status, indent=2))
    else:
        print("No scheduler status found. Is the scheduler running?")

    # Check if PID is alive
    pid_path = PROJECT_ROOT / "data" / "scheduler.pid"
    if pid_path.exists():
        pid = int(pid_path.read_text().strip())
        try:
            os.kill(pid, 0)
            print(f"\nScheduler process {pid} is RUNNING")
        except OSError:
            print(f"\nScheduler process {pid} is NOT running (stale PID file)")
    else:
        print("\nNo scheduler PID file found")


def show_queue():
    queue = get_queue()
    if not queue:
        print("Queue is empty - no clips ready to post")
        return
    print(f"\nPost Queue ({len(queue)} clips):\n")
    for i, clip in enumerate(queue):
        ep = Path(clip["source_episode"]).stem if clip["source_episode"] else "?"
        track = clip["trending_track"] or "none"
        hook = clip["top_hook"] or clip["hook_text"][:40] or "no hook"
        print(f"  {i+1}. [{clip['id']}] {ep} | {clip['duration_seconds']:.0f}s | {hook}")
        print(f"     Audio: {track}")


def main():
    parser = argparse.ArgumentParser(description="TikTok Post Scheduler")
    parser.add_argument("command", choices=["run", "status", "next", "queue"],
                        help="Command to execute")
    args = parser.parse_args()

    if args.command == "run":
        run_scheduler()
    elif args.command == "status":
        show_status()
    elif args.command == "next":
        next_t = get_next_post_time()
        print(f"Next post: {next_t.strftime('%Y-%m-%d %H:%M PT')}")
        clip = get_next_clip()
        if clip:
            print(f"Clip: {clip['hook_text'][:60]}")
        else:
            print("No clips in queue")
    elif args.command == "queue":
        show_queue()


if __name__ == "__main__":
    main()
