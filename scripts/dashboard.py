#!/usr/bin/env python3
"""
TikTok Pipeline Dashboard — lightweight monitoring UI.
Serves a single auto-refreshing HTML page with pipeline stats, logs, and queue.

Usage:
    python3 scripts/dashboard.py                # default port 8888
    python3 scripts/dashboard.py --port 9000    # custom port

Access: http://hermes:8888 (or http://<tailscale-ip>:8888)
"""

import argparse
import html
import json
import os
import sqlite3
import subprocess
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
STATUS_PATH = PROJECT_ROOT / "data" / "scheduler_status.json"
SCHEDULER_LOG = PROJECT_ROOT / "logs" / "scheduler.log"
PROCESS_LOG = PROJECT_ROOT / "logs" / "process_all.log"
FOLLOWER_STATS = PROJECT_ROOT / "data" / "follower_stats.json"
FOLLOWER_LOG = PROJECT_ROOT / "data" / "follower_log.json"
TZ = ZoneInfo("America/Los_Angeles")


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def tail_file(path, lines=30):
    """Read last N lines of a file."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            buf = min(size, lines * 200)
            f.seek(max(0, size - buf))
            data = f.read().decode("utf-8", errors="replace")
            return "\n".join(data.splitlines()[-lines:])
    except FileNotFoundError:
        return "(no log file)"


def get_disk_usage():
    """Get disk usage stats."""
    stats = {}
    for name, path in [
        ("output/captioned", PROJECT_ROOT / "output" / "captioned"),
        ("output/clips", PROJECT_ROOT / "output" / "clips"),
        ("input/episodes", PROJECT_ROOT / "input" / "episodes"),
        ("data", PROJECT_ROOT / "data"),
    ]:
        try:
            result = subprocess.run(
                ["du", "-sh", str(path)], capture_output=True, text=True, timeout=5
            )
            stats[name] = result.stdout.split("\t")[0].strip() if result.returncode == 0 else "?"
        except Exception:
            stats[name] = "?"
    try:
        result = subprocess.run(["df", "-h", "/"], capture_output=True, text=True, timeout=5)
        lines = result.stdout.strip().splitlines()
        if len(lines) >= 2:
            parts = lines[1].split()
            stats["disk_total"] = parts[1]
            stats["disk_used"] = parts[2]
            stats["disk_free"] = parts[3]
            stats["disk_pct"] = parts[4]
    except Exception:
        stats["disk_free"] = "?"
    return stats


def get_process_pid():
    """Check if processing is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "pipeline_growth.py process"], capture_output=True, text=True, timeout=5
        )
        pids = result.stdout.strip().splitlines()
        return pids[0] if pids else None
    except Exception:
        return None


def get_scheduler_pid():
    """Check scheduler PID."""
    pid_path = PROJECT_ROOT / "data" / "scheduler.pid"
    if not pid_path.exists():
        return None
    pid = pid_path.read_text().strip()
    try:
        os.kill(int(pid), 0)
        return pid
    except (OSError, ValueError):
        return None


def build_dashboard():
    """Build the full HTML dashboard."""
    now = datetime.now(TZ)
    db = get_db()

    # --- Stats ---
    ready = db.execute("SELECT COUNT(*) as c FROM videos WHERE status='ready'").fetchone()["c"]
    posted = db.execute("SELECT COUNT(*) as c FROM videos WHERE status='posted'").fetchone()["c"]
    failed = db.execute("SELECT COUNT(*) as c FROM videos WHERE status='failed'").fetchone()["c"]
    total_clips = ready + posted + failed

    today_str = now.strftime("%Y-%m-%d")
    posted_today = db.execute(
        "SELECT COUNT(*) as c FROM videos WHERE status='posted' AND posted_at LIKE ?",
        (f"{today_str}%",)
    ).fetchone()["c"]

    # --- Episodes ---
    episodes = db.execute(
        "SELECT filename, title, clips_extracted, clips_posted, processed_at FROM episodes ORDER BY processed_at DESC"
    ).fetchall()

    # Count remaining
    episodes_dir = PROJECT_ROOT / "input" / "episodes"
    total_episodes = 0
    if episodes_dir.exists():
        total_episodes = len([f for f in episodes_dir.iterdir() if f.suffix.lower() in (".mp4", ".mkv", ".avi", ".mov")])

    # --- Queue ---
    queue = db.execute("""
        SELECT v.id, v.source_episode, v.duration_seconds, v.top_hook, s.hook_text, v.created_at
        FROM videos v JOIN scripts s ON v.script_id = s.id
        WHERE v.status = 'ready' ORDER BY v.created_at ASC LIMIT 15
    """).fetchall()

    # --- Recent posts ---
    recent = db.execute("""
        SELECT v.id, v.source_episode, v.duration_seconds, v.top_hook, s.hook_text, v.posted_at
        FROM videos v JOIN scripts s ON v.script_id = s.id
        WHERE v.status = 'posted' ORDER BY v.posted_at DESC LIMIT 15
    """).fetchall()

    # --- AI vs Heuristic hooks ---
    ai_hooks = db.execute(
        "SELECT COUNT(*) as c FROM videos WHERE top_hook IS NOT NULL AND top_hook != ''"
    ).fetchone()["c"]

    # Check for common heuristic patterns
    heuristic_patterns = [
        "YOU NEED TO SEE THIS", "THIS GOES HORRIBLY WRONG", "NOBODY EXPECTED THIS",
        "THE % SITUATION GETS WORSE", "THINGS ARE ABOUT TO GO WRONG",
    ]
    heuristic_count = 0
    for pat in heuristic_patterns:
        heuristic_count += db.execute(
            "SELECT COUNT(*) as c FROM videos WHERE top_hook LIKE ?", (f"%{pat}%",)
        ).fetchone()["c"]

    db.close()

    # --- Scheduler status ---
    sched = {}
    if STATUS_PATH.exists():
        with open(STATUS_PATH) as f:
            sched = json.load(f)

    # --- PIDs ---
    proc_pid = get_process_pid()
    sched_pid = get_scheduler_pid()

    # --- Disk ---
    disk = get_disk_usage()

    # --- Logs ---
    sched_log = html.escape(tail_file(SCHEDULER_LOG, 25))
    proc_log = html.escape(tail_file(PROCESS_LOG, 35))

    # --- TikTok Account ---
    tiktok_stats = {}
    if FOLLOWER_STATS.exists():
        with open(FOLLOWER_STATS) as f:
            tiktok_stats = json.load(f)

    # --- Build HTML ---
    def stat_card(label, value, color="#4fc3f7"):
        return f"""<div class="card"><div class="card-value" style="color:{color}">{value}</div><div class="card-label">{label}</div></div>"""

    def status_dot(running):
        color = "#4caf50" if running else "#f44336"
        label = "RUNNING" if running else "STOPPED"
        return f'<span class="dot" style="background:{color}"></span> {label}'

    episode_rows = ""
    for ep in episodes:
        name = html.escape(ep["title"] or ep["filename"])
        episode_rows += f"""<tr>
            <td>{name}</td>
            <td>{ep['clips_extracted']}</td>
            <td>{ep['clips_posted']}</td>
            <td>{ep['processed_at'][:16] if ep['processed_at'] else '-'}</td>
        </tr>"""

    queue_rows = ""
    for q in queue:
        ep = Path(q["source_episode"]).stem if q["source_episode"] else "?"
        hook = html.escape(q["top_hook"] or q["hook_text"] or "-")[:50]
        queue_rows += f"""<tr>
            <td>{q['id']}</td>
            <td>{ep[:30]}</td>
            <td>{hook}</td>
            <td>{q['duration_seconds']:.0f}s</td>
        </tr>"""

    recent_rows = ""
    for r in recent:
        ep = Path(r["source_episode"]).stem if r["source_episode"] else "?"
        hook = html.escape(r["top_hook"] or r["hook_text"] or "-")[:50]
        posted_at = r["posted_at"][:16] if r["posted_at"] else "-"
        recent_rows += f"""<tr>
            <td>{r['id']}</td>
            <td>{ep[:30]}</td>
            <td>{hook}</td>
            <td>{posted_at}</td>
        </tr>"""

    disk_rows = ""
    for name, size in disk.items():
        if not name.startswith("disk_"):
            disk_rows += f"<tr><td>{name}</td><td>{size}</td></tr>"

    next_post = sched.get("next_post", "?")
    sched_state = sched.get("state", "unknown")

    # TikTok template vars
    tt_user = tiktok_stats.get("username", "stigscloset")
    tt_time = tiktok_stats.get("fetched_at", "?")[:16] if tiktok_stats.get("fetched_at") else "?"
    tt_followers = tiktok_stats.get("followers", "?")
    tt_likes = tiktok_stats.get("likes", "?")
    tt_followers_card = stat_card("Followers", f"{tt_followers:,}" if isinstance(tt_followers, int) else "?", "#fe2c55")
    tt_likes_card = stat_card("Likes", f"{tt_likes:,}" if isinstance(tt_likes, int) else "?", "#fe2c55")
    tt_videos_card = stat_card("Videos", tiktok_stats.get("videos", "?"), "#fe2c55")
    tt_following_card = stat_card("Following", tiktok_stats.get("following", "?"), "#8b949e")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30">
<title>TikTok Pipeline Dashboard</title>
<style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, 'SF Mono', monospace; padding: 20px; }}
    h1 {{ color: #58a6ff; margin-bottom: 4px; font-size: 1.4em; }}
    h2 {{ color: #8b949e; font-size: 1em; margin: 24px 0 10px; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid #21262d; padding-bottom: 6px; }}
    .subtitle {{ color: #8b949e; font-size: 0.85em; margin-bottom: 20px; }}
    .status-bar {{ display: flex; gap: 24px; align-items: center; padding: 12px 16px; background: #161b22; border-radius: 8px; margin-bottom: 20px; flex-wrap: wrap; }}
    .status-item {{ display: flex; align-items: center; gap: 8px; font-size: 0.9em; }}
    .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .card {{ background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 16px; text-align: center; }}
    .card-value {{ font-size: 1.8em; font-weight: bold; }}
    .card-label {{ font-size: 0.75em; color: #8b949e; margin-top: 4px; text-transform: uppercase; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
    th {{ text-align: left; color: #8b949e; font-weight: normal; padding: 6px 10px; border-bottom: 1px solid #21262d; }}
    td {{ padding: 6px 10px; border-bottom: 1px solid #21262d22; }}
    tr:hover td {{ background: #161b22; }}
    .log {{ background: #0d1117; border: 1px solid #21262d; border-radius: 6px; padding: 12px; font-size: 0.75em; line-height: 1.5; overflow-x: auto; white-space: pre-wrap; word-break: break-all; max-height: 400px; overflow-y: auto; color: #8b949e; }}
    .grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .disk-bar {{ background: #21262d; border-radius: 4px; height: 8px; margin-top: 6px; }}
    .disk-fill {{ background: #f78166; border-radius: 4px; height: 8px; }}
    @media (max-width: 768px) {{ .grid-2 {{ grid-template-columns: 1fr; }} .cards {{ grid-template-columns: repeat(2, 1fr); }} }}
</style>
</head>
<body>
<h1>@stigscloset Pipeline Dashboard</h1>
<div class="subtitle">{now.strftime('%B %d, %Y %I:%M %p PT')} &middot; auto-refreshes every 30s</div>

<div style="background:#161b22;border:1px solid #21262d;border-radius:8px;padding:16px;margin-bottom:20px;">
<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
    <strong style="color:#fe2c55;font-size:1.1em;">@{tt_user}</strong>
    <span style="color:#8b949e;font-size:0.8em;">updated {tt_time}</span>
</div>
<div class="cards" style="margin-bottom:0;">
    {tt_followers_card}
    {tt_likes_card}
    {tt_videos_card}
    {tt_following_card}
</div>
</div>

<div class="status-bar">
    <div class="status-item"><strong>Processor:</strong> {status_dot(proc_pid is not None)}{f' (PID {proc_pid})' if proc_pid else ''}</div>
    <div class="status-item"><strong>Scheduler:</strong> {status_dot(sched_pid is not None)}{f' (PID {sched_pid})' if sched_pid else ''}</div>
    <div class="status-item"><strong>State:</strong> {sched_state}</div>
    <div class="status-item"><strong>Next Post:</strong> {next_post}</div>
</div>

<div class="cards">
    {stat_card("Queue", ready, "#4fc3f7")}
    {stat_card("Posted", posted, "#4caf50")}
    {stat_card("Failed", failed, "#f44336" if failed > 0 else "#8b949e")}
    {stat_card("Today", posted_today, "#ffb74d")}
    {stat_card("Total Clips", total_clips, "#ce93d8")}
    {stat_card("Episodes", f"{len(episodes)}/{total_episodes}", "#81c784")}
    {stat_card("AI Hooks", f"{ai_hooks - heuristic_count}", "#4fc3f7")}
    {stat_card("Heuristic", heuristic_count, "#8b949e")}
</div>

<div class="grid-2">
<div>
<h2>Post Queue (next {len(queue)})</h2>
<table>
<tr><th>#</th><th>Episode</th><th>Hook</th><th>Dur</th></tr>
{queue_rows}
</table>
</div>
<div>
<h2>Recent Posts</h2>
<table>
<tr><th>#</th><th>Episode</th><th>Hook</th><th>Posted</th></tr>
{recent_rows}
</table>
</div>
</div>

<h2>Episodes</h2>
<table>
<tr><th>Episode</th><th>Clips</th><th>Posted</th><th>Processed</th></tr>
{episode_rows}
</table>

<h2>Storage</h2>
<div class="cards" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
    {stat_card("Disk Free", disk.get('disk_free', '?'), "#81c784")}
    {stat_card("Disk Used", f"{disk.get('disk_used', '?')} ({disk.get('disk_pct', '?')})", "#f78166")}
</div>
<table style="max-width: 400px;">
<tr><th>Directory</th><th>Size</th></tr>
{disk_rows}
</table>

<div class="grid-2">
<div>
<h2>Processing Log</h2>
<div class="log">{proc_log}</div>
</div>
<div>
<h2>Scheduler Log</h2>
<div class="log">{sched_log}</div>
</div>
</div>

<div style="text-align:center; color:#8b949e; font-size:0.7em; margin-top:30px; padding-top:12px; border-top:1px solid #21262d;">
    ai-clips-pipeline &middot; hermes &middot; refreshed {now.strftime('%H:%M:%S PT')}
</div>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/dashboard":
            content = build_dashboard().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/api/status":
            db = get_db()
            ready = db.execute("SELECT COUNT(*) as c FROM videos WHERE status='ready'").fetchone()["c"]
            posted = db.execute("SELECT COUNT(*) as c FROM videos WHERE status='posted'").fetchone()["c"]
            failed = db.execute("SELECT COUNT(*) as c FROM videos WHERE status='failed'").fetchone()["c"]
            db.close()
            sched = {}
            if STATUS_PATH.exists():
                with open(STATUS_PATH) as f:
                    sched = json.load(f)
            data = json.dumps({
                "ready": ready, "posted": posted, "failed": failed,
                "scheduler": sched,
                "processor_running": get_process_pid() is not None,
                "scheduler_running": get_scheduler_pid() is not None,
            })
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data.encode())
        else:
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()

    def log_message(self, fmt, *args):
        pass  # suppress request logging


def main():
    parser = argparse.ArgumentParser(description="TikTok Pipeline Dashboard")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), DashboardHandler)
    print(f"Dashboard running at http://{args.host}:{args.port}")
    print(f"Access from LAN: http://hermes:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down dashboard")
        server.server_close()


if __name__ == "__main__":
    main()
