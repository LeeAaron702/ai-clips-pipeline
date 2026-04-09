#!/usr/bin/env python3
"""
Check TikTok follower count for @stigscloset and auto-switch pipeline phase at 1K.

Scrapes the public TikTok profile, logs count to SQLite, updates config.json,
and sends a Telegram notification if the 1K milestone is hit.

Usage:
    python3 scripts/check_followers.py
    python3 scripts/check_followers.py --history
"""

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
CONFIG_PATH = PROJECT_ROOT / "config.json"

TIKTOK_USERNAME = "stigscloset"
TIKTOK_PROFILE_URL = f"https://www.tiktok.com/@{TIKTOK_USERNAME}"
TIKTOK_API_URL = f"https://www.tiktok.com/api/user/detail/?uniqueId={TIKTOK_USERNAME}"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

MILESTONE = 1000


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(config: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def ensure_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS follower_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            count INTEGER NOT NULL,
            checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def send_telegram(message: str):
    """Send a Telegram notification via the existing notify script."""
    notify_script = PROJECT_ROOT / "scripts" / "notify_telegram.sh"
    if notify_script.exists():
        subprocess.run(["bash", str(notify_script), "--message", message], capture_output=True)


def scrape_follower_count() -> int | None:
    """
    Try to scrape follower count from TikTok.
    Method 1: Parse __UNIVERSAL_DATA_FOR_REHYDRATION__ from profile HTML.
    Method 2: Fall back to public API endpoint.
    Returns follower count or None if both fail.
    """
    import requests

    headers = {"User-Agent": USER_AGENT}

    # Method 1: HTML scrape
    try:
        resp = requests.get(TIKTOK_PROFILE_URL, headers=headers, timeout=15)
        if resp.status_code == 200:
            # Look for the rehydration data script tag
            match = re.search(
                r'<script\s+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
                resp.text,
                re.DOTALL,
            )
            if match:
                data = json.loads(match.group(1))
                # Navigate: __DEFAULT_SCOPE__ -> webapp.user-detail -> userInfo -> stats -> followerCount
                user_detail = data.get("__DEFAULT_SCOPE__", {}).get("webapp.user-detail", {})
                stats = user_detail.get("userInfo", {}).get("stats", {})
                count = stats.get("followerCount")
                if count is not None:
                    return int(count)
    except Exception as e:
        print(f"WARNING: HTML scrape failed: {e}")

    # Method 2: Public API fallback
    try:
        resp = requests.get(TIKTOK_API_URL, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            stats = data.get("userInfo", {}).get("stats", {})
            count = stats.get("followerCount")
            if count is not None:
                return int(count)
    except Exception as e:
        print(f"WARNING: API fallback failed: {e}")

    return None


def log_count(conn: sqlite3.Connection, count: int):
    conn.execute("INSERT INTO follower_log (count) VALUES (?)", (count,))
    conn.commit()


def check_milestone(count: int, config: dict) -> bool:
    """Check if we hit 1K and need to switch phase. Returns True if switched."""
    if count >= MILESTONE and config.get("sub_1k", False):
        now = datetime.now(timezone.utc).isoformat()
        config["sub_1k"] = False
        config["phase"] = "monetization"
        config["auto_switched_at"] = now

        save_config(config)

        msg = (
            f"🎉 MILESTONE HIT: @{TIKTOK_USERNAME} reached {count:,} followers!\n"
            f"Pipeline auto-switched to monetization phase."
        )
        print(msg)
        send_telegram(msg)
        return True
    return False


def show_history(conn: sqlite3.Connection, limit: int = 20):
    rows = conn.execute(
        "SELECT count, checked_at FROM follower_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    if not rows:
        print("No follower checks recorded yet.")
        return
    print(f"{'Date':>20}  {'Followers':>10}")
    print("-" * 33)
    for row in rows:
        print(f"{row['checked_at']:>20}  {row['count']:>10,}")


def main():
    parser = argparse.ArgumentParser(description="Check TikTok follower count for @stigscloset")
    parser.add_argument("--history", action="store_true", help="Show recent follower checks")
    args = parser.parse_args()

    # Ensure data dir exists
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = get_db()
    ensure_table(conn)

    if args.history:
        show_history(conn)
        conn.close()
        return

    # Scrape current count
    print(f"Checking followers for @{TIKTOK_USERNAME}...")
    count = scrape_follower_count()

    if count is None:
        print("ERROR: Could not retrieve follower count. TikTok may be blocking the request.")
        print("Try again later or check manually.")
        conn.close()
        sys.exit(1)

    # Log to DB
    log_count(conn, count)

    # Update config
    config = load_config()
    now = datetime.now(timezone.utc).isoformat()
    config["follower_count"] = count
    config["last_follower_check"] = now
    save_config(config)

    # Check milestone
    switched = check_milestone(count, config)

    # Summary
    print(f"Follower count: {count:,}")
    print(f"Phase: {config.get('phase', 'unknown')}")
    print(f"Sub-1K: {config.get('sub_1k', 'unknown')}")
    if switched:
        print(">>> PHASE SWITCHED TO MONETIZATION <<<")
    elif count < MILESTONE:
        remaining = MILESTONE - count
        print(f"Remaining to 1K milestone: {remaining:,}")

    conn.close()


if __name__ == "__main__":
    main()
