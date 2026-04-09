#!/usr/bin/env python3
"""
Daily analytics report — queries pipeline DB and sends summary via Telegram.

Usage:
    python3 scripts/daily_report.py
    python3 scripts/daily_report.py report
    python3 scripts/daily_report.py report --stdout-only
"""

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
CONFIG_PATH = PROJECT_ROOT / "config.json"
EPISODES_DIR = PROJECT_ROOT / "input" / "episodes"


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_video_counts(conn: sqlite3.Connection) -> dict:
    """Count clips by status."""
    cur = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM videos GROUP BY status"
    )
    counts = {"ready": 0, "posted": 0, "failed": 0}
    for row in cur:
        counts[row["status"]] = row["cnt"]
    return counts


def get_posted_today(conn: sqlite3.Connection) -> int:
    """Clips posted in the last 24 hours."""
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    cur = conn.execute(
        "SELECT COUNT(*) as cnt FROM videos WHERE status = 'posted' AND posted_at >= ?",
        (cutoff,),
    )
    return cur.fetchone()["cnt"]


def get_episode_stats(conn: sqlite3.Connection) -> dict:
    """Episode counts and total clips extracted."""
    cur = conn.execute(
        "SELECT COUNT(*) as total, COALESCE(SUM(clips_extracted), 0) as clips FROM episodes"
    )
    row = cur.fetchone()
    return {"total": row["total"], "clips_extracted": row["clips"]}


def get_new_episodes_waiting(conn: sqlite3.Connection) -> int:
    """Count episode files in input dir not yet in the DB."""
    if not EPISODES_DIR.exists():
        return 0
    known = set()
    cur = conn.execute("SELECT filename FROM episodes")
    for row in cur:
        known.add(row["filename"])
    waiting = 0
    for f in EPISODES_DIR.iterdir():
        if f.suffix.lower() in (".mp4", ".mkv", ".avi", ".mov", ".webm"):
            if f.name not in known:
                waiting += 1
    return waiting


def get_follower_trend(conn: sqlite3.Connection, limit: int = 5) -> list[int]:
    """Last N follower counts from follower_log."""
    cur = conn.execute(
        "SELECT count FROM follower_log ORDER BY checked_at DESC LIMIT ?",
        (limit,),
    )
    rows = [row["count"] for row in cur]
    rows.reverse()  # chronological order
    return rows


def get_budget_spent(conn: sqlite3.Connection, month: str) -> float:
    """Total spent this month from budget_log."""
    cur = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) as total FROM budget_log WHERE month = ?",
        (month,),
    )
    return cur.fetchone()["total"]


def build_report() -> str:
    """Build the HTML-formatted report string."""
    config = load_config()
    conn = get_db()

    today = datetime.now().strftime("%Y-%m-%d")

    # Video stats
    counts = get_video_counts(conn)
    posted_today = get_posted_today(conn)

    # Episode stats
    ep_stats = get_episode_stats(conn)
    new_waiting = get_new_episodes_waiting(conn)

    # Follower info
    follower_count = config.get("follower_count", "?")
    last_check = config.get("last_follower_check")
    if last_check:
        # Trim to minute precision
        last_check = last_check[:16].replace("T", " ")
    else:
        last_check = "never"

    trend = get_follower_trend(conn)
    trend_str = " → ".join(str(c) for c in trend) if trend else "no data"

    # Budget
    budget = config.get("budget", {})
    current_month = budget.get("current_month", datetime.now().strftime("%Y-%m"))
    monthly_limit = budget.get("monthly_limit_usd", 0)
    spent_config = budget.get("spent_usd", 0)
    spent_log = get_budget_spent(conn, current_month)
    spent = max(spent_config, spent_log)  # use whichever is higher

    # Phase label
    phase = config.get("phase", "unknown").upper()

    # Format month for display
    try:
        month_display = datetime.strptime(current_month, "%Y-%m").strftime("%b %Y")
    except ValueError:
        month_display = current_month

    conn.close()

    report = (
        f"<b>📊 Daily Pipeline Report</b>\n"
        f"<b>Date:</b> {today}\n"
        f"\n"
        f"<b>Pipeline Status</b>\n"
        f"• Phase: {phase} ({follower_count} followers)\n"
        f"• Clips ready: {counts['ready']}\n"
        f"• Clips posted: {counts['posted']}\n"
        f"• Posted today: {posted_today}\n"
        f"• Failed: {counts['failed']}\n"
        f"\n"
        f"<b>Content</b>\n"
        f"• Episodes processed: {ep_stats['total']}\n"
        f"• New episodes waiting: {new_waiting}\n"
        f"• Total clips extracted: {ep_stats['clips_extracted']}\n"
        f"\n"
        f"<b>Followers</b>\n"
        f"• Current: {follower_count}\n"
        f"• Last check: {last_check}\n"
        f"• Trend: {trend_str}\n"
        f"\n"
        f"<b>Budget ({month_display})</b>\n"
        f"• Spent: ${spent:.2f} / ${monthly_limit:.2f}"
    )
    return report


def send_telegram(message: str):
    """Send message via the notify_telegram.sh script."""
    result = subprocess.run(
        [
            "bash",
            str(PROJECT_ROOT / "scripts" / "notify_telegram.sh"),
            "--message",
            message,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Telegram send failed: {result.stderr}", file=sys.stderr)
    else:
        print(result.stdout.strip())


def main():
    parser = argparse.ArgumentParser(description="Daily pipeline analytics report")
    parser.add_argument(
        "command", nargs="?", default="report", choices=["report"],
        help="Command to run (default: report)",
    )
    parser.add_argument(
        "--stdout-only", action="store_true",
        help="Print report to stdout without sending via Telegram",
    )
    args = parser.parse_args()

    report = build_report()
    print(report)

    if not args.stdout_only:
        send_telegram(report)


if __name__ == "__main__":
    main()
