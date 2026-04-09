#!/usr/bin/env python3
"""
Fetch TikTok follower count for @stigscloset.
Writes to data/follower_stats.json. Called by scheduler after each post.
Also logs history to data/follower_log.json for growth tracking.
"""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATS_PATH = PROJECT_ROOT / "data" / "follower_stats.json"
LOG_PATH = PROJECT_ROOT / "data" / "follower_log.json"
TZ = ZoneInfo("America/Los_Angeles")

TIKTOK_USER = "stigscloset"


def fetch_follower_count() -> dict | None:
    """Scrape follower count from TikTok public profile."""
    try:
        result = subprocess.run(
            ["curl", "-s", "-A",
             "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
             f"https://www.tiktok.com/@{TIKTOK_USER}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None

        html = result.stdout

        # Extract stats from page JSON
        followers = None
        following = None
        likes = None

        m = re.search(r'"followerCount":(\d+)', html)
        if m:
            followers = int(m.group(1))
        m = re.search(r'"followingCount":(\d+)', html)
        if m:
            following = int(m.group(1))
        m = re.search(r'"heartCount":(\d+)', html)
        if m:
            likes = int(m.group(1))
        m = re.search(r'"videoCount":(\d+)', html)
        videos = int(m.group(1)) if m else None

        if followers is not None:
            return {
                "followers": followers,
                "following": following,
                "likes": likes,
                "videos": videos,
                "fetched_at": datetime.now(TZ).isoformat(),
                "username": TIKTOK_USER,
            }
    except Exception as e:
        print(f"Error fetching followers: {e}")
    return None


def update_stats():
    """Fetch and save follower stats."""
    stats = fetch_follower_count()
    if not stats:
        print("Failed to fetch follower count")
        return None

    # Save current stats
    STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATS_PATH, "w") as f:
        json.dump(stats, f, indent=2)

    # Append to log for growth tracking
    log = []
    if LOG_PATH.exists():
        try:
            with open(LOG_PATH) as f:
                log = json.load(f)
        except (json.JSONDecodeError, ValueError):
            log = []

    # Only log if count changed or last log > 1hr ago
    should_log = True
    if log:
        last = log[-1]
        if last.get("followers") == stats["followers"]:
            # Same count — only log if >1hr since last
            from datetime import datetime as dt
            try:
                last_time = dt.fromisoformat(last["fetched_at"])
                now_time = dt.fromisoformat(stats["fetched_at"])
                if (now_time - last_time).total_seconds() < 3600:
                    should_log = False
            except Exception:
                pass

    if should_log:
        log.append(stats)
        # Keep last 1000 entries
        if len(log) > 1000:
            log = log[-1000:]
        with open(LOG_PATH, "w") as f:
            json.dump(log, f)

    print(f"@{TIKTOK_USER}: {stats['followers']} followers, {stats['likes']} likes, {stats['videos']} videos")
    return stats


if __name__ == "__main__":
    update_stats()
