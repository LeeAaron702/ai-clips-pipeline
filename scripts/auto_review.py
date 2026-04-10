#!/usr/bin/env python3
"""
Auto-Review & Self-Improvement System for TikTok Clip Pipeline.

Reviews output quality using Claude API with visual frame analysis,
logs structured reviews, auto-fixes what it can, and pushes changes.

Usage:
    python3 scripts/auto_review.py                  # Full review cycle
    python3 scripts/auto_review.py --dry-run         # Review without changes
    python3 scripts/auto_review.py --clip 4          # Review specific clip
    python3 scripts/auto_review.py --no-push         # Skip git push
    python3 scripts/auto_review.py --recheck         # Re-review after fixes
"""

import argparse
import base64
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
CAPTIONED_DIR = PROJECT_ROOT / "output" / "captioned"
CLIPS_DIR = PROJECT_ROOT / "output" / "clips"
LOGS_DIR = PROJECT_ROOT / "logs"
QA_LOG = LOGS_DIR / "qa_reviews.jsonl"
LATEST_REVIEW = LOGS_DIR / "latest_review.md"
CHANGELOG = LOGS_DIR / "changelog.md"
CLAUDE_PATH = "/Users/hermes/.local/bin/claude"
FFPROBE = "/opt/homebrew/bin/ffprobe"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
CREDS_PATH = Path.home() / ".claude" / ".credentials.json"

# Delay between API calls to avoid rate limits (seconds)
API_DELAY = 8

# Generic hooks that should be regenerated
GENERIC_HOOKS = {
    "YOU NEED TO SEE THIS", "YOU NEED TO SEE THIS...",
    "WAIT FOR THIS...", "WAIT FOR THIS",
    "NOBODY EXPECTED THIS...", "NOBODY EXPECTED THIS",
    "THIS IS INSANE", "THIS IS INSANE...",
    "YOU WONT BELIEVE THIS", "YOU WONT BELIEVE THIS...",
    "THIS GOES HORRIBLY WRONG...", "THINGS ARE ABOUT TO GO WRONG",
}

REVIEW_PROMPT = """You are a TikTok content quality auditor. You are looking at 6 frames extracted from a 9:16 TikTok clip.

EVALUATE each category on A/B/C/D scale:

1. HOOK TEXT (persistent text near top of frame):
   - Is it visible and readable?
   - Is it specific and compelling? (e.g. "CLARKSON MIGHT NOT SURVIVE THIS" = good, "YOU NEED TO SEE THIS" = bad)
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

Be harsh and specific. Generic praise is useless. Point out exact problems.

Respond ONLY with this exact JSON format (no other text before or after):
{"grade": "B", "hook_grade": "B", "caption_grade": "A", "framing_grade": "B", "issues": ["hook is generic", "subject partially out of frame"], "suggestions": ["regenerate hook", "widen crop during wide shots"], "auto_fixable": ["generic_hook"]}

Valid auto_fixable values: "generic_hook", "caption_overflow", "duration_short", "duration_long"
"""


def ensure_dirs():
    LOGS_DIR.mkdir(parents=True, exist_ok=True)


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def get_api_token():
    """Load OAuth access token for API calls."""
    if CREDS_PATH.exists():
        with open(CREDS_PATH) as f:
            creds = json.load(f)
        return creds.get("claudeAiOauth", {}).get("accessToken")
    return None


def get_video_info(video_path):
    """Get video metadata via ffprobe."""
    try:
        result = subprocess.run(
            [FFPROBE, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", video_path],
            capture_output=True, text=True, timeout=10
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
        return info
    except Exception:
        return {}


def extract_review_frames(video_path, count=6):
    """Extract evenly-spaced frames, downscaled for API efficiency."""
    tmpdir = tempfile.mkdtemp(prefix="qa_frames_")
    # Extract at 1fps first
    subprocess.run(
        [FFMPEG, "-y", "-i", video_path, "-vf", "fps=1,scale=540:-1", "-q:v", "5",
         os.path.join(tmpdir, "frame_%04d.jpg")],
        capture_output=True, timeout=30
    )
    frames = sorted(Path(tmpdir).glob("*.jpg"))
    if not frames:
        shutil.rmtree(tmpdir, ignore_errors=True)
        return [], None

    # Pick evenly spaced
    if len(frames) <= count:
        picks = [str(f) for f in frames]
    else:
        step = len(frames) / count
        picks = [str(frames[int(i * step)]) for i in range(count)]

    return picks, tmpdir


def review_with_api(frames, clip_info, token, retries=2):
    """Send frames to Anthropic API for visual review. Returns review dict or None."""
    content = []
    for frame_path in frames:
        try:
            with open(frame_path, "rb") as img:
                b64 = base64.b64encode(img.read()).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}
            })
        except Exception:
            continue

    if not content:
        return None

    # Build context
    hook = clip_info.get("top_hook", "")
    duration = clip_info.get("duration", 0)
    context = f"\nClip context: Hook text is \"{hook}\", duration is {duration:.1f}s.\n"
    content.append({"type": "text", "text": REVIEW_PROMPT + context})

    payload = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 500,
        "messages": [{"role": "user", "content": content}]
    }

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(payload).encode(),
                headers={
                    "x-api-key": token,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                }
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read())
                text = result["content"][0]["text"]
                # Extract JSON
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    review = json.loads(text[start:end])
                    review["review_type"] = "claude_api"
                    return review
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                wait = (attempt + 1) * 15
                print(f"    Rate limited, waiting {wait}s (attempt {attempt + 1}/{retries + 1})")
                time.sleep(wait)
                continue
            err_body = ""
            try:
                err_body = e.read().decode()[:200]
            except Exception:
                pass
            print(f"    API error {e.code}: {err_body}")
            return None
        except json.JSONDecodeError:
            print(f"    Could not parse review JSON from API response")
            return None
        except Exception as e:
            print(f"    API error: {e}")
            return None

    return None


def review_automated(video_path, clip_info):
    """Automated ffprobe checks (fallback only)."""
    issues = []
    suggestions = []
    auto_fixable = []

    info = get_video_info(video_path)
    w, h = info.get("width", 0), info.get("height", 0)
    if w and h and (w != 1080 or h != 1920):
        issues.append(f"Resolution {w}x{h}, expected 1080x1920")

    duration = info.get("duration", 0)
    if 0 < duration < 10:
        issues.append(f"Too short: {duration:.1f}s")
        auto_fixable.append("duration_short")
    elif duration > 60:
        issues.append(f"Too long: {duration:.1f}s")
        auto_fixable.append("duration_long")

    hook = clip_info.get("top_hook", "").strip().upper().rstrip(".")
    if hook in GENERIC_HOOKS or not hook:
        issues.append(f"Generic hook: \"{clip_info.get('top_hook', '')}\"")
        auto_fixable.append("generic_hook")
        suggestions.append("Regenerate hook with AI")

    grade = "B" if not issues else ("C" if len(issues) <= 2 else "D")

    return {
        "grade": grade, "hook_grade": "?" , "caption_grade": "?",
        "framing_grade": "?",
        "issues": issues or ["Automated checks passed"],
        "suggestions": suggestions or ["Visual review needed"],
        "auto_fixable": auto_fixable, "review_type": "automated",
    }


def review_single_clip(clip_id, token=None):
    """Review a single clip. Returns review dict."""
    db = get_db()
    row = db.execute("""
        SELECT v.id, v.video_path, v.top_hook, v.duration_seconds,
               v.source_episode, v.clip_start_sec, v.clip_end_sec,
               s.hook_text, s.hashtags
        FROM videos v JOIN scripts s ON v.script_id = s.id
        WHERE v.id = ?
    """, (clip_id,)).fetchone()
    db.close()

    if not row:
        return {"grade": "F", "issues": ["Clip not found"], "review_type": "error", "clip_id": clip_id}

    video_path = row["video_path"]
    clip_info = {
        "clip_id": row["id"],
        "top_hook": row["top_hook"] or row["hook_text"] or "",
        "duration": row["duration_seconds"] or 0,
        "source_episode": row["source_episode"],
    }

    if not video_path or not Path(video_path).exists():
        return {"grade": "F", "issues": [f"Video not found: {video_path}"],
                "review_type": "error", "clip_id": clip_id}

    # Extract frames
    frames, tmpdir = extract_review_frames(video_path)
    if not frames:
        return {"grade": "F", "issues": ["Failed to extract frames"],
                "review_type": "error", "clip_id": clip_id}

    print(f"    {len(frames)} frames -> ", end="", flush=True)

    # Try API visual review
    review = None
    if token:
        review = review_with_api(frames, clip_info, token)
        if review:
            print(f"Grade: {review.get('grade', '?')} (visual)")
        else:
            print("API failed, using automated")

    if not review:
        review = review_automated(video_path, clip_info)
        print(f"Grade: {review.get('grade', '?')} (automated)")

    # Add metadata
    review["clip_id"] = clip_id
    review["timestamp"] = datetime.now().isoformat()
    review["video_path"] = video_path
    review["top_hook"] = clip_info.get("top_hook", "")

    # Cleanup
    if tmpdir:
        shutil.rmtree(tmpdir, ignore_errors=True)

    return review


def fix_generic_hook(clip_id, current_hook, dry_run=False):
    """Regenerate a generic hook — try AI first, then heuristic."""
    db = get_db()
    row = db.execute(
        "SELECT source_episode, clip_start_sec, clip_end_sec FROM videos WHERE id = ?",
        (clip_id,)
    ).fetchone()
    db.close()

    if not row or not row["source_episode"]:
        return None

    episode_name = row["source_episode"].replace(".mkv", "").replace(".mp4", "")
    transcript_path = PROJECT_ROOT / "data" / "transcripts" / f"{episode_name}.json"
    if not transcript_path.exists():
        return None

    with open(transcript_path) as f:
        transcript = json.load(f)

    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
    from add_captions import get_words_for_clip
    from generate_top_hook import generate_hook_from_transcript

    words = get_words_for_clip(transcript, row["clip_start_sec"], row["clip_end_sec"])
    if not words:
        return None

    clip_num = clip_id % 100
    new_hook = generate_hook_from_transcript(words, episode_name, clip_num)

    if new_hook.strip().upper().rstrip(".") in GENERIC_HOOKS:
        return None

    if dry_run:
        print(f"      [DRY RUN] Hook: \"{current_hook}\" -> \"{new_hook}\"")
        return new_hook

    db = get_db()
    db.execute("UPDATE videos SET top_hook = ? WHERE id = ?", (new_hook, clip_id))
    db.execute("UPDATE scripts SET hook_text = ? WHERE id = (SELECT script_id FROM videos WHERE id = ?)",
               (new_hook, clip_id))
    db.commit()
    db.close()
    print(f"      Hook: \"{current_hook}\" -> \"{new_hook}\"")
    return new_hook


def apply_fixes(review, dry_run=False):
    """Apply auto-fixes. Returns list of changes."""
    changes = []
    clip_id = review.get("clip_id")
    fixable = review.get("auto_fixable", [])
    if not clip_id or not fixable:
        return changes

    if "generic_hook" in fixable:
        new = fix_generic_hook(clip_id, review.get("top_hook", ""), dry_run)
        if new:
            changes.append(f"Hook regenerated: \"{new}\"")

    if "duration_short" in fixable:
        changes.append("Flagged: clip too short for manual review")
    if "duration_long" in fixable:
        changes.append("Flagged: clip too long for manual review")

    return changes


def log_review(review):
    ensure_dirs()
    with open(QA_LOG, "a") as f:
        f.write(json.dumps(review) + "\n")


def write_latest_review(results, all_changes):
    ensure_dirs()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    grades = [r["grade"] for r in results]
    visual = sum(1 for r in results if r.get("review_type") == "claude_api")
    auto = sum(1 for r in results if r.get("review_type") == "automated")

    lines = [
        f"# QA Review Summary",
        f"**Date:** {now}",
        f"**Clips reviewed:** {len(results)} ({visual} visual, {auto} automated)",
        "",
        "## Grade Distribution",
    ]

    for g in ["A", "B", "C", "D", "F"]:
        count = grades.count(g)
        if count:
            lines.append(f"- **{g}:** {count} {'#' * count}")

    if grades:
        from collections import Counter
        lines.append(f"\n**Overall: {Counter(grades).most_common(1)[0][0]}**")

    lines.append("\n## Clip Details")
    for r in results:
        cid = r.get("clip_id", "?")
        grade = r.get("grade", "?")
        rtype = r.get("review_type", "?")
        hook = r.get("top_hook", "")[:60]
        lines.append(f"\n### Clip {cid} — {grade} ({rtype})")
        if hook:
            lines.append(f"Hook: \"{hook}\"")
        if rtype == "claude_api":
            for key in ["hook_grade", "caption_grade", "framing_grade"]:
                if r.get(key):
                    lines.append(f"- {key.replace('_', ' ').title()}: {r[key]}")
        for issue in r.get("issues", [])[:5]:
            lines.append(f"- {issue}")
        for sug in r.get("suggestions", [])[:3]:
            lines.append(f"- *Suggestion: {sug}*")

    if all_changes:
        lines.append("\n## Changes Applied")
        for ch in all_changes:
            for change in ch.get("changes", []):
                lines.append(f"- Clip {ch.get('clip_id', '?')}: {change}")

    lines.append(f"\n---\n*Generated by auto_review.py at {now}*")
    with open(LATEST_REVIEW, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n  Review written to {LATEST_REVIEW}")


def write_changelog(results, all_changes):
    ensure_dirs()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    grades = [r["grade"] for r in results]
    visual = sum(1 for r in results if r.get("review_type") == "claude_api")

    lines = [f"\n## [{now}] Auto-Review Cycle ({visual} visual reviews)"]
    for ch in all_changes:
        for change in ch.get("changes", []):
            lines.append(f"- Clip {ch.get('clip_id', '?')}: {change}")

    grade_counts = [f"{grades.count(g)}{g}" for g in ["A", "B", "C", "D", "F"] if grades.count(g)]
    lines.append(f"- Grades: {' / '.join(grade_counts)}")

    existing = CHANGELOG.read_text() if CHANGELOG.exists() else ""
    with open(CHANGELOG, "w") as f:
        f.write(existing + "\n".join(lines) + "\n")


def git_push_changes(all_changes, dry_run=False, no_push=False):
    if dry_run or no_push:
        return False

    subprocess.run(["git", "add", "logs/"], capture_output=True, cwd=str(PROJECT_ROOT))
    summary = f"reviewed {sum(1 for ch in all_changes if ch.get('changes'))} clips with fixes" if any(ch.get("changes") for ch in all_changes) else "reviewed clips, no fixes needed"
    subprocess.run(["git", "commit", "-m", f"Auto-review: {summary}"],
                   capture_output=True, cwd=str(PROJECT_ROOT))
    result = subprocess.run(["git", "push", "origin", "main"],
                           capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    if result.returncode == 0:
        print(f"  Pushed to origin/main")
    return result.returncode == 0


# === MAIN ===

def review_all_clips(use_claude=True, dry_run=False, no_push=False,
                     clip_id=None, recheck=False):
    """Full review cycle."""
    ensure_dirs()

    token = get_api_token() if use_claude else None
    if token:
        print(f"  API token loaded — visual reviews enabled")
    else:
        print(f"  No API token — automated checks only")

    db = get_db()
    if clip_id:
        clips = db.execute(
            "SELECT v.id FROM videos v WHERE v.id = ?", (clip_id,)
        ).fetchall()
    else:
        # Only review clips with actual video files (skip deleted trending)
        clips = db.execute("""
            SELECT v.id FROM videos v
            WHERE v.status IN ('ready', 'posted')
            AND v.video_path IS NOT NULL
            ORDER BY v.id
        """).fetchall()
    db.close()

    if not clips:
        print("No clips to review.")
        return []

    print(f"\n{'='*60}")
    print(f"AUTO-REVIEW: {len(clips)} clips | {'DRY RUN' if dry_run else 'LIVE'} | {'Visual' if token else 'Automated'}")
    print(f"{'='*60}\n")

    results = []
    all_changes = []
    visual_count = 0

    for i, clip in enumerate(clips):
        cid = clip["id"]

        # Get hook for display
        db = get_db()
        info = db.execute(
            "SELECT v.top_hook, s.hook_text FROM videos v JOIN scripts s ON v.script_id = s.id WHERE v.id = ?",
            (cid,)
        ).fetchone()
        db.close()
        hook = (info["top_hook"] or info["hook_text"] or "")[:50] if info else ""

        print(f"  [{i+1}/{len(clips)}] Clip {cid}: \"{hook}\"")

        review = review_single_clip(cid, token=token)
        results.append(review)
        log_review(review)

        if review.get("review_type") == "claude_api":
            visual_count += 1

        # Apply fixes
        fixable = review.get("auto_fixable", [])
        if fixable:
            changes = apply_fixes(review, dry_run)
            if changes:
                all_changes.append({"clip_id": cid, "changes": changes})
        else:
            all_changes.append({"clip_id": cid, "changes": []})

        # Rate limit delay between visual reviews
        if token and i < len(clips) - 1:
            time.sleep(API_DELAY)

    # Write reports
    write_latest_review(results, all_changes)
    write_changelog(results, all_changes)
    git_push_changes(all_changes, dry_run, no_push)

    # Summary
    grades = [r.get("grade", "?") for r in results]
    print(f"\n{'='*60}")
    print(f"REVIEW COMPLETE: {len(results)} clips ({visual_count} visual)")
    for g in ["A", "B", "C", "D", "F"]:
        c = grades.count(g)
        if c:
            print(f"  {g}: {c}")
    print(f"{'='*60}\n")
    return results


def run_post_pipeline_review(dry_run=False, no_push=False):
    """Entry point from pipeline_growth.py."""
    return review_all_clips(use_claude=True, dry_run=dry_run, no_push=no_push, recheck=True)


def main():
    parser = argparse.ArgumentParser(description="Auto-review TikTok clips")
    parser.add_argument("--clip", type=int, help="Review specific clip ID")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--recheck", action="store_true")
    parser.add_argument("--no-claude", action="store_true")
    args = parser.parse_args()

    review_all_clips(
        use_claude=not args.no_claude, dry_run=args.dry_run,
        no_push=args.no_push, clip_id=args.clip, recheck=args.recheck,
    )


if __name__ == "__main__":
    main()
