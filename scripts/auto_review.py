#!/usr/bin/env python3
"""
Auto-Review & Self-Improvement System for TikTok Clip Pipeline.

Reviews output quality using Claude CLI (with automated fallback),
logs structured reviews, auto-fixes what it can, and pushes changes.

Usage:
    python3 scripts/auto_review.py                  # Full review cycle
    python3 scripts/auto_review.py --dry-run         # Review without changes
    python3 scripts/auto_review.py --clip 4          # Review specific clip
    python3 scripts/auto_review.py --no-push         # Skip git push
    python3 scripts/auto_review.py --recheck         # Re-review after fixes
"""

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
TRENDING_DIR = PROJECT_ROOT / "output" / "trending"
CAPTIONED_DIR = PROJECT_ROOT / "output" / "captioned"
CLIPS_DIR = PROJECT_ROOT / "output" / "clips"
LOGS_DIR = PROJECT_ROOT / "logs"
QA_LOG = LOGS_DIR / "qa_reviews.jsonl"
LATEST_REVIEW = LOGS_DIR / "latest_review.md"
CHANGELOG = LOGS_DIR / "changelog.md"
CLAUDE_PATH = "/Users/hermes/.local/bin/claude"
FFPROBE = "ffprobe"
FFMPEG = "ffmpeg"

# Generic hooks that should be regenerated
GENERIC_HOOKS = {
    "YOU NEED TO SEE THIS",
    "YOU NEED TO SEE THIS...",
    "WAIT FOR THIS...",
    "WAIT FOR THIS",
    "NOBODY EXPECTED THIS...",
    "NOBODY EXPECTED THIS",
    "THIS IS INSANE",
    "THIS IS INSANE...",
    "YOU WON'T BELIEVE THIS",
    "YOU WON'T BELIEVE THIS...",
}

# Review prompt for Claude CLI
REVIEW_PROMPT = """You are a TikTok content quality auditor. Review these frames extracted from a 9:16 TikTok clip.

EVALUATE each category on A/B/C/D scale:

1. HOOK TEXT (persistent text near top of frame):
   - Is it visible and readable?
   - Is it specific and compelling? (e.g. "CLARKSON MIGHT NOT SURVIVE THIS" is good, "YOU NEED TO SEE THIS" is bad)
   - Would it make someone stop scrolling?

2. CAPTIONS (bottom animated text):
   - Readable at mobile size? ALL CAPS?
   - Good contrast against video? Not cut off at edges?
   - Font size appropriate?

3. FRAMING:
   - Subject properly visible? Faces in frame?
   - No awkward crops or black bars?
   - 9:16 aspect ratio used well?

4. OVERALL QUALITY:
   - Would this perform on TikTok? First 2 seconds compelling?

Be harsh but specific. Generic praise is useless.

Respond ONLY with this exact JSON (no other text):
{"grade": "B", "hook_grade": "B", "caption_grade": "A", "framing_grade": "B", "issues": ["hook is generic - says YOU NEED TO SEE THIS", "subject partially out of frame at 15s mark"], "suggestions": ["regenerate hook with specific reference to the action", "widen crop during wide shots"], "auto_fixable": ["generic_hook"]}

Valid auto_fixable values: "generic_hook", "caption_overflow", "duration_short", "duration_long"
Non-fixable issues: just list them in issues array, they'll be flagged for human review.
"""


def ensure_dirs():
    """Create logs directory if it doesn't exist."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_video_info(video_path: str) -> dict:
    """Get video metadata via ffprobe."""
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", video_path],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return {}
        data = json.loads(result.stdout)
        vs = next((s for s in data.get("streams", []) if s["codec_type"] == "video"), None)
        fmt = data.get("format", {})
        info = {
            "duration": float(fmt.get("duration", 0)),
            "size_bytes": int(fmt.get("size", 0)),
            "bitrate": int(fmt.get("bit_rate", 0)),
        }
        if vs:
            info["width"] = int(vs.get("width", 0))
            info["height"] = int(vs.get("height", 0))
            info["codec"] = vs.get("codec_name", "")
        return info
    except Exception:
        return {}


def extract_frames(video_path: str) -> list[str]:
    """Extract 1fps frames, return paths."""
    tmpdir = tempfile.mkdtemp(prefix="qa_frames_")
    cmd = [
        FFMPEG, "-y", "-i", video_path,
        "-vf", "fps=1", "-q:v", "5",
        os.path.join(tmpdir, "frame_%04d.jpg"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    frames = sorted(Path(tmpdir).glob("*.jpg"))
    return [str(f) for f in frames]


def pick_review_frames(frames: list[str], count: int = 6) -> list[str]:
    """Pick evenly-spaced frames for review."""
    if len(frames) <= count:
        return frames
    step = len(frames) / count
    return [frames[int(i * step)] for i in range(count)]


def claude_available() -> bool:
    """Check if Claude CLI is available and logged in."""
    try:
        result = subprocess.run(
            [CLAUDE_PATH, "-p", "say ok", "--model", "haiku", "--output-format", "text"],
            capture_output=True, text=True, timeout=15,
            cwd=str(PROJECT_ROOT),
        )
        return result.returncode == 0 and len(result.stdout.strip()) > 0
    except Exception:
        return False


def review_with_claude(video_path: str, frames: list[str], clip_info: dict = None) -> dict | None:
    """Send frames to Claude CLI for visual review."""
    review_frames = pick_review_frames(frames, 6)

    # Build command
    frame_args = []
    for f in review_frames:
        frame_args.extend(["--file", f])

    # Add clip context to prompt
    context = ""
    if clip_info:
        hook = clip_info.get("top_hook", "")
        duration = clip_info.get("duration", 0)
        context = f"\n\nClip context: Hook text is \"{hook}\", duration is {duration:.1f}s.\n"

    prompt = REVIEW_PROMPT + context

    try:
        result = subprocess.run(
            [CLAUDE_PATH, "-p", prompt, *frame_args, "--model", "haiku", "--output-format", "text"],
            capture_output=True, text=True, timeout=120,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0 and result.stdout.strip():
            text = result.stdout.strip()
            # Extract JSON from response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                review = json.loads(text[start:end])
                review["review_type"] = "claude"
                return review
    except json.JSONDecodeError:
        pass
    except Exception as e:
        print(f"    Claude review error: {e}")

    return None


def review_automated(video_path: str, frames: list[str], clip_info: dict = None) -> dict:
    """Automated quality checks (fallback when Claude unavailable)."""
    issues = []
    suggestions = []
    auto_fixable = []

    info = get_video_info(video_path)

    # Resolution check
    w = info.get("width", 0)
    h = info.get("height", 0)
    if w and h:
        if w != 1080 or h != 1920:
            issues.append(f"Resolution {w}x{h}, expected 1080x1920")
    else:
        issues.append("Could not read video resolution")

    # Duration check
    duration = info.get("duration", 0)
    if duration > 0:
        if duration < 10:
            issues.append(f"Too short: {duration:.1f}s (min 10s)")
            auto_fixable.append("duration_short")
        elif duration > 60:
            issues.append(f"Too long: {duration:.1f}s (max 60s)")
            auto_fixable.append("duration_long")
    else:
        issues.append("Could not read video duration")

    # Bitrate check
    bitrate = info.get("bitrate", 0)
    if bitrate > 0 and bitrate < 2_000_000:
        issues.append(f"Low bitrate: {bitrate / 1000:.0f}kbps (want >2000kbps)")
        suggestions.append("Re-encode at higher bitrate or check source quality")

    # File size check
    size_mb = info.get("size_bytes", 0) / (1024 * 1024)
    if size_mb > 0 and size_mb < 3:
        issues.append(f"Small file: {size_mb:.1f}MB (may indicate quality issues)")

    # Hook check (from DB)
    hook = clip_info.get("top_hook", "") if clip_info else ""
    hook_upper = hook.strip().upper().rstrip(".")
    if hook_upper in GENERIC_HOOKS or not hook.strip():
        issues.append(f"Generic hook: \"{hook}\"")
        auto_fixable.append("generic_hook")
        suggestions.append("Regenerate hook with episode-specific content")

    # Grade
    fixable_count = len(auto_fixable)
    real_issues = len(issues) - fixable_count  # Non-fixable issues
    if not issues:
        grade = "B"  # Can't give A without visual review
    elif real_issues == 0 and fixable_count <= 1:
        grade = "B"
    elif len(issues) <= 2:
        grade = "C"
    else:
        grade = "D"

    return {
        "grade": grade,
        "hook_grade": "D" if "generic_hook" in auto_fixable else "?",
        "caption_grade": "?",
        "framing_grade": "?",
        "issues": issues if issues else ["Automated checks passed - needs visual review"],
        "suggestions": suggestions if suggestions else ["Run with Claude for visual review"],
        "auto_fixable": auto_fixable,
        "review_type": "automated",
    }


def review_single_clip(clip_id: int = None, video_path: str = None,
                        use_claude: bool = True) -> dict:
    """Review a single clip. Returns review dict."""
    clip_info = {}

    if clip_id and not video_path:
        db = get_db()
        row = db.execute("""
            SELECT v.id, v.trending_video_path, v.video_path, v.top_hook,
                   v.duration_seconds, v.trending_track, v.source_episode,
                   v.clip_start_sec, v.clip_end_sec,
                   s.hook_text, s.hashtags
            FROM videos v JOIN scripts s ON v.script_id = s.id
            WHERE v.id = ?
        """, (clip_id,)).fetchone()
        db.close()

        if not row:
            return {"grade": "F", "issues": ["Clip not found in database"],
                    "review_type": "error", "clip_id": clip_id}

        video_path = row["trending_video_path"] or row["video_path"]
        clip_info = {
            "clip_id": row["id"],
            "top_hook": row["top_hook"] or row["hook_text"] or "",
            "duration": row["duration_seconds"] or 0,
            "trending_track": row["trending_track"],
            "source_episode": row["source_episode"],
            "start_sec": row["clip_start_sec"],
            "end_sec": row["clip_end_sec"],
            "hashtags": row["hashtags"],
        }

    if not video_path or not Path(video_path).exists():
        return {"grade": "F", "issues": [f"Video file not found: {video_path}"],
                "review_type": "error", "clip_id": clip_id}

    print(f"    Extracting frames...")
    frames = extract_frames(video_path)
    if not frames:
        return {"grade": "F", "issues": ["Failed to extract frames"],
                "review_type": "error", "clip_id": clip_id}

    print(f"    {len(frames)} frames extracted")

    # Try Claude first
    review = None
    if use_claude:
        print(f"    Sending to Claude for review...")
        review = review_with_claude(video_path, frames, clip_info)

    if not review:
        if use_claude:
            print(f"    Claude unavailable, falling back to automated checks")
        review = review_automated(video_path, frames, clip_info)

    # Add metadata
    review["clip_id"] = clip_info.get("clip_id", clip_id)
    review["timestamp"] = datetime.now().isoformat()
    review["video_path"] = video_path
    if "top_hook" not in review:
        review["top_hook"] = clip_info.get("top_hook", "")

    # Cleanup frames
    if frames:
        frame_dir = str(Path(frames[0]).parent)
        shutil.rmtree(frame_dir, ignore_errors=True)

    return review


# === AUTO-FIX FUNCTIONS ===

def fix_generic_hook(clip_id: int, current_hook: str, dry_run: bool = False) -> str | None:
    """Regenerate a generic hook with better heuristics."""
    db = get_db()
    row = db.execute("""
        SELECT v.source_episode, v.clip_start_sec, v.clip_end_sec
        FROM videos v WHERE v.id = ?
    """, (clip_id,)).fetchone()
    db.close()

    if not row or not row["source_episode"]:
        return None

    episode_name = row["source_episode"].replace(".mkv", "").replace(".mp4", "")
    transcript_path = PROJECT_ROOT / "data" / "transcripts" / f"{episode_name}.json"

    if not transcript_path.exists():
        print(f"      Transcript not found: {transcript_path}")
        return None

    with open(transcript_path) as f:
        transcript = json.load(f)

    # Get words for this clip segment
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from add_captions import get_words_for_clip
    from generate_top_hook import generate_hook_heuristic

    words = get_words_for_clip(transcript, row["clip_start_sec"], row["clip_end_sec"])
    if not words:
        return None

    # Generate new hook - retry up to 3 times to avoid getting same generic one
    for attempt in range(3):
        new_hook = generate_hook_heuristic(words, episode_name)
        if new_hook.strip().upper().rstrip(".") not in GENERIC_HOOKS:
            break

    if new_hook.strip().upper().rstrip(".") in GENERIC_HOOKS:
        print(f"      Could not generate non-generic hook after 3 attempts")
        return None

    if dry_run:
        print(f"      [DRY RUN] Would regenerate hook: \"{current_hook}\" -> \"{new_hook}\"")
        return new_hook

    # Update DB
    db = get_db()
    db.execute("UPDATE videos SET top_hook = ? WHERE id = ?", (new_hook, clip_id))
    db.execute("UPDATE scripts SET hook_text = ? WHERE id = (SELECT script_id FROM videos WHERE id = ?)",
               (new_hook, clip_id))
    db.commit()
    db.close()

    print(f"      Hook updated: \"{current_hook}\" -> \"{new_hook}\"")
    return new_hook


def reprocess_clip_after_fix(clip_id: int, dry_run: bool = False) -> bool:
    """Re-run a clip through the caption + trending audio pipeline."""
    if dry_run:
        print(f"      [DRY RUN] Would reprocess clip {clip_id:03d}")
        return True

    reprocess_script = PROJECT_ROOT / "scripts" / "reprocess_clip.py"
    if not reprocess_script.exists():
        print(f"      reprocess_clip.py not found")
        return False

    try:
        result = subprocess.run(
            [sys.executable, str(reprocess_script), str(clip_id)],
            capture_output=True, text=True, timeout=300,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            print(f"      Reprocessed clip {clip_id:03d} successfully")
            return True
        else:
            print(f"      Reprocess failed: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"      Reprocess error: {e}")
        return False


def apply_fixes(review: dict, dry_run: bool = False) -> list[str]:
    """Apply auto-fixes for identified issues. Returns list of changes made."""
    changes = []
    clip_id = review.get("clip_id")
    auto_fixable = review.get("auto_fixable", [])

    if not clip_id or not auto_fixable:
        return changes

    needs_reprocess = False

    if "generic_hook" in auto_fixable:
        current_hook = review.get("top_hook", "")
        new_hook = fix_generic_hook(clip_id, current_hook, dry_run=dry_run)
        if new_hook:
            changes.append(f"Regenerated hook \"{new_hook}\" (was \"{current_hook}\")")
            needs_reprocess = True

    if "caption_overflow" in auto_fixable:
        # Flag for reprocess with smaller font - would need add_captions.py changes
        changes.append("Flagged caption overflow for reprocess")
        needs_reprocess = True

    if "duration_short" in auto_fixable:
        changes.append("Flagged for manual review - clip too short")

    if "duration_long" in auto_fixable:
        changes.append("Flagged for manual review - clip too long")

    # Reprocess if we changed something that affects the video
    if needs_reprocess and changes:
        reprocessed = reprocess_clip_after_fix(clip_id, dry_run=dry_run)
        if reprocessed:
            changes.append(f"Reprocessed clip {clip_id:03d}")

    return changes


# === LOGGING ===

def log_review(review: dict):
    """Append review to JSONL log."""
    ensure_dirs()
    with open(QA_LOG, "a") as f:
        f.write(json.dumps(review) + "\n")


def write_latest_review(results: list[dict], changes: list[dict]):
    """Write human-readable review summary."""
    ensure_dirs()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    grades = [r["grade"] for r in results]

    lines = [
        f"# QA Review Summary",
        f"**Date:** {now}",
        f"**Clips reviewed:** {len(results)}",
        f"**Review type:** {results[0].get('review_type', 'unknown') if results else 'n/a'}",
        "",
        "## Grade Distribution",
    ]

    for g in ["A", "B", "C", "D", "F"]:
        count = grades.count(g)
        if count:
            bar = "#" * count
            lines.append(f"- **{g}:** {count} {bar}")

    # Overall grade (most common)
    if grades:
        from collections import Counter
        most_common = Counter(grades).most_common(1)[0][0]
        lines.append(f"\n**Overall: {most_common}**")

    lines.append("\n## Clip Details")
    for r in results:
        cid = r.get("clip_id", "?")
        grade = r.get("grade", "?")
        hook = r.get("top_hook", "")[:60]
        issues = r.get("issues", [])
        lines.append(f"\n### Clip {cid} - Grade: {grade}")
        if hook:
            lines.append(f"Hook: \"{hook}\"")
        if issues:
            for issue in issues:
                lines.append(f"- {issue}")
        sug = r.get("suggestions", [])
        if sug:
            lines.append("**Suggestions:**")
            for s in sug:
                lines.append(f"- {s}")

    if changes:
        lines.append("\n## Changes Applied")
        for ch in changes:
            clip_id = ch.get("clip_id", "?")
            for change in ch.get("changes", []):
                lines.append(f"- Clip {clip_id:03d}: {change}" if isinstance(clip_id, int) else f"- {change}")

    lines.append(f"\n---\n*Generated by auto_review.py at {now}*")

    with open(LATEST_REVIEW, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"\n  Review summary written to {LATEST_REVIEW}")


def write_changelog(results: list[dict], all_changes: list[dict]):
    """Append to changelog.md."""
    ensure_dirs()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    grades = [r["grade"] for r in results]

    lines = [f"\n## [{now}] Auto-Review Cycle"]

    has_entries = False

    # Log changes
    for ch in all_changes:
        clip_id = ch.get("clip_id", "?")
        for change in ch.get("changes", []):
            cid_str = f"{clip_id:03d}" if isinstance(clip_id, int) else str(clip_id)
            lines.append(f"- Clip {cid_str}: {change}")
            has_entries = True

    # Log flagged items (issues that need human review)
    human_flags = []
    for r in results:
        auto_fixable = set(r.get("auto_fixable", []))
        issues = r.get("issues", [])
        non_fixable = [i for i in issues
                       if not any(af in i.lower() for af in ["generic hook", "automated check"])]
        # Filter to truly unfixable issues
        for issue in non_fixable:
            lower = issue.lower()
            if any(kw in lower for kw in ["frame", "crop", "face", "framing", "audio",
                                           "content", "moment", "subject"]):
                human_flags.append((r.get("clip_id", "?"), issue))
                has_entries = True

    for clip_id, issue in human_flags:
        cid_str = f"{clip_id:03d}" if isinstance(clip_id, int) else str(clip_id)
        lines.append(f"- Clip {cid_str}: Flagged for manual review - {issue}")

    # Grade summary
    grade_counts = []
    for g in ["A", "B", "C", "D", "F"]:
        count = grades.count(g)
        if count:
            grade_counts.append(f"{count} clips {g}")
    if grade_counts:
        lines.append(f"- Overall grade: {', '.join(grade_counts)}")
        has_entries = True

    if not has_entries:
        lines.append(f"- All {len(results)} clips passed review (no changes needed)")

    # Append to changelog
    existing = ""
    if CHANGELOG.exists():
        existing = CHANGELOG.read_text()

    with open(CHANGELOG, "w") as f:
        f.write(existing + "\n".join(lines) + "\n")

    print(f"  Changelog updated: {CHANGELOG}")


# === GIT OPERATIONS ===

def git_push_changes(all_changes: list[dict], dry_run: bool = False, no_push: bool = False) -> bool:
    """Commit and push if there are code/script changes (not just logs)."""
    if dry_run or no_push:
        if dry_run:
            print("\n  [DRY RUN] Skipping git push")
        if no_push:
            print("\n  --no-push flag set, skipping git push")
        return False

    # Check for changes (exclude logs/ from the "should we push" decision)
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT)
    )
    # Also check untracked
    result2 = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT)
    )

    changed_files = result.stdout.strip().split("\n") if result.stdout.strip() else []
    status_lines = result2.stdout.strip().split("\n") if result2.stdout.strip() else []

    # Filter: only push if there are script/code changes (not just logs)
    code_changes = [f for f in changed_files if not f.startswith("logs/")]
    new_code_files = [l for l in status_lines
                      if l.strip() and not l.strip().startswith("logs/")
                      and (l.startswith("?? scripts/") or l.startswith("M  scripts/")
                           or l.startswith("A  scripts/"))]

    if not code_changes and not new_code_files:
        print("\n  No code changes to push (only log files updated)")
        # Still add and commit logs
        subprocess.run(["git", "add", "logs/"], capture_output=True, cwd=str(PROJECT_ROOT))
        summary = _build_commit_summary(all_changes)
        subprocess.run(
            ["git", "commit", "-m", f"Auto-review logs: {summary}"],
            capture_output=True, cwd=str(PROJECT_ROOT)
        )
        return False

    # Stage everything
    subprocess.run(["git", "add", "-A"], capture_output=True, cwd=str(PROJECT_ROOT))

    summary = _build_commit_summary(all_changes)
    commit_msg = f"Auto-review: {summary}"

    result = subprocess.run(
        ["git", "commit", "-m", commit_msg],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT)
    )

    if result.returncode != 0:
        print(f"\n  Git commit failed: {result.stderr[:200]}")
        return False

    print(f"\n  Committed: {commit_msg}")

    # Push
    result = subprocess.run(
        ["git", "push", "origin", "main"],
        capture_output=True, text=True, cwd=str(PROJECT_ROOT)
    )

    if result.returncode == 0:
        print(f"  Pushed to origin/main")
        return True
    else:
        print(f"  Push failed: {result.stderr[:200]}")
        return False


def _build_commit_summary(all_changes: list[dict]) -> str:
    """Build a short commit message from changes."""
    if not all_changes or not any(ch.get("changes") for ch in all_changes):
        return "reviewed clips, no fixes needed"

    parts = []
    hook_fixes = 0
    reprocessed = 0
    flagged = 0

    for ch in all_changes:
        for change in ch.get("changes", []):
            lower = change.lower()
            if "hook" in lower and "regenerated" in lower:
                hook_fixes += 1
            elif "reprocessed" in lower:
                reprocessed += 1
            elif "flagged" in lower:
                flagged += 1

    if hook_fixes:
        parts.append(f"{hook_fixes} hooks regenerated")
    if reprocessed:
        parts.append(f"{reprocessed} clips reprocessed")
    if flagged:
        parts.append(f"{flagged} flagged for review")

    return ", ".join(parts) if parts else "reviewed clips"


# === MAIN REVIEW CYCLE ===

def review_all_clips(use_claude: bool = True, dry_run: bool = False,
                     no_push: bool = False, clip_id: int = None,
                     recheck: bool = False) -> list[dict]:
    """Full review cycle: review -> fix -> re-review -> log -> push."""
    ensure_dirs()

    # Check Claude availability once
    claude_ok = False
    if use_claude:
        print("Checking Claude CLI availability...")
        claude_ok = claude_available()
        if claude_ok:
            print("  Claude CLI is available - using AI review")
        else:
            print("  Claude CLI unavailable - using automated checks")

    # Get clips to review
    db = get_db()
    if clip_id:
        clips = db.execute("""
            SELECT v.id, v.trending_video_path, v.video_path, v.top_hook,
                   v.duration_seconds, v.trending_track, v.source_episode,
                   v.clip_start_sec, v.clip_end_sec,
                   s.hook_text, s.hashtags
            FROM videos v JOIN scripts s ON v.script_id = s.id
            WHERE v.id = ?
        """, (clip_id,)).fetchall()
    else:
        clips = db.execute("""
            SELECT v.id, v.trending_video_path, v.video_path, v.top_hook,
                   v.duration_seconds, v.trending_track, v.source_episode,
                   v.clip_start_sec, v.clip_end_sec,
                   s.hook_text, s.hashtags
            FROM videos v JOIN scripts s ON v.script_id = s.id
            WHERE v.status IN ('ready', 'posted')
            ORDER BY v.id
        """).fetchall()
    db.close()

    if not clips:
        print("\nNo clips to review.")
        return []

    print(f"\n{'='*60}")
    print(f"AUTO-REVIEW: {len(clips)} clip(s) | {'DRY RUN' if dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    results = []
    all_changes = []

    for clip in clips:
        video = clip["trending_video_path"] or clip["video_path"]
        hook = clip["top_hook"] or clip["hook_text"] or ""
        cid = clip["id"]

        print(f"Clip {cid:03d}: \"{hook[:50]}\"")
        if clip["duration_seconds"]:
            print(f"  Duration: {clip['duration_seconds']:.1f}s | Audio: {clip['trending_track'] or 'none'}")

        # Review
        review = review_single_clip(clip_id=cid, use_claude=claude_ok)
        results.append(review)
        log_review(review)

        grade = review.get("grade", "?")
        issues = review.get("issues", [])
        print(f"  Grade: {grade}")
        for issue in issues[:5]:
            print(f"    - {issue}")

        # Apply fixes if not dry run and there are fixable issues
        auto_fixable = review.get("auto_fixable", [])
        if auto_fixable:
            print(f"  Auto-fixable: {', '.join(auto_fixable)}")
            changes = apply_fixes(review, dry_run=dry_run)
            if changes:
                all_changes.append({"clip_id": cid, "changes": changes})
                print(f"  Changes: {len(changes)}")

                # Re-review if we made changes and --recheck flag
                if recheck and not dry_run:
                    print(f"  Re-reviewing after fixes...")
                    re_review = review_single_clip(clip_id=cid, use_claude=claude_ok)
                    old_grade = grade
                    new_grade = re_review.get("grade", "?")
                    print(f"  Re-review grade: {old_grade} -> {new_grade}")
                    log_review(re_review)
                    # Use the re-review result
                    results[-1] = re_review
        else:
            all_changes.append({"clip_id": cid, "changes": []})

        print()

    # Write summaries
    write_latest_review(results, all_changes)
    write_changelog(results, all_changes)

    # Git push
    git_push_changes(all_changes, dry_run=dry_run, no_push=no_push)

    # Print final summary
    grades = [r.get("grade", "?") for r in results]
    print(f"\n{'='*60}")
    print(f"REVIEW COMPLETE: {len(results)} clips")
    for g in ["A", "B", "C", "D", "F"]:
        count = grades.count(g)
        if count:
            print(f"  {g}: {count}")
    total_fixes = sum(len(ch.get("changes", [])) for ch in all_changes)
    if total_fixes:
        print(f"  Fixes applied: {total_fixes}")
    print(f"{'='*60}\n")

    return results


def run_post_pipeline_review(dry_run: bool = False, no_push: bool = False):
    """Entry point called from pipeline_growth.py after processing an episode."""
    print("\n[POST-PIPELINE] Running auto-review...")
    results = review_all_clips(
        use_claude=True,
        dry_run=dry_run,
        no_push=no_push,
        recheck=True,  # Always re-review after fixes in pipeline mode
    )
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Auto-review & self-improvement for TikTok clip pipeline"
    )
    parser.add_argument("--clip", type=int, help="Review a specific clip ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="Review without making changes")
    parser.add_argument("--no-push", action="store_true",
                        help="Skip git push after changes")
    parser.add_argument("--recheck", action="store_true",
                        help="Re-review clips after applying fixes")
    parser.add_argument("--no-claude", action="store_true",
                        help="Skip Claude CLI, use automated checks only")
    args = parser.parse_args()

    results = review_all_clips(
        use_claude=not args.no_claude,
        dry_run=args.dry_run,
        no_push=args.no_push,
        clip_id=args.clip,
        recheck=args.recheck,
    )

    if not results:
        sys.exit(0)

    # Exit code based on worst grade
    grades = [r.get("grade", "F") for r in results]
    if "F" in grades:
        sys.exit(2)
    elif "D" in grades:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
