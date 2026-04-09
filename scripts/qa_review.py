#!/usr/bin/env python3
"""
QA Review: Extract 1fps frames from each clip and grade quality.
Uses Claude CLI to analyze visual quality, caption placement, hooks, etc.
Falls back to automated checks if Claude unavailable.

Usage:
    python3 scripts/qa_review.py                    # Review all ready clips
    python3 scripts/qa_review.py --clip 4            # Review specific clip
    python3 scripts/qa_review.py --fix               # Review and auto-fix issues
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pipeline.db"
TRENDING_DIR = PROJECT_ROOT / "output" / "trending"
QA_LOG = PROJECT_ROOT / "logs" / "qa_reviews.jsonl"
CLAUDE_PATH = "/Users/hermes/.local/bin/claude"

REVIEW_PROMPT = """You are a TikTok content quality auditor. Review these frames from a TikTok clip and grade it.

EVALUATE:
1. HOOK (top red pill caption): Is it visible? Is it compelling? Would it make someone stop scrolling?
2. CAPTIONS (bottom text): Are they readable? ALL CAPS? Good size? Not cut off at edges?
3. FRAMING: Is the subject centered? Any awkward crops? Black bars?
4. OVERALL: Rate A/B/C/D and list specific improvements.

Be harsh. If the hook is generic like "YOU NEED TO SEE THIS" that's a C at best.
If captions are cut off or too small, that's a D.
If framing is good and text is readable, that's a B minimum.

Respond in this exact JSON format:
{"grade": "B", "hook_grade": "B", "caption_grade": "A", "framing_grade": "B", "issues": ["hook is generic", "framing cuts off subject at 15s"], "suggestions": ["use specific hook referencing the action", "widen crop during car shots"]}
"""


def extract_frames(video_path: str, fps: int = 1) -> list[str]:
    """Extract frames at 1fps for review."""
    tmpdir = tempfile.mkdtemp(prefix="qa_")
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps={fps}", "-q:v", "5",
        os.path.join(tmpdir, "frame_%04d.jpg"),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return []
    frames = sorted(Path(tmpdir).glob("*.jpg"))
    return [str(f) for f in frames]


def review_with_claude(video_path: str, frames: list[str]) -> dict:
    """Send frames to Claude for review."""
    # Pick 6 evenly spaced frames for review
    if len(frames) > 6:
        step = len(frames) // 6
        review_frames = [frames[i * step] for i in range(6)]
    else:
        review_frames = frames

    # Build the prompt with frame references
    frame_args = []
    for f in review_frames:
        frame_args.extend(["--file", f])

    try:
        result = subprocess.run(
            [CLAUDE_PATH, "-p", REVIEW_PROMPT, *frame_args, "--model", "haiku", "--output-format", "text"],
            capture_output=True, text=True, timeout=60,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            # Try to parse JSON from response
            text = result.stdout.strip()
            # Find JSON in response
            json_match = text[text.find("{"):text.rfind("}") + 1]
            if json_match:
                return json.loads(json_match)
    except Exception as e:
        print(f"  Claude review failed: {e}")

    return None


def review_automated(video_path: str, frames: list[str]) -> dict:
    """Automated quality checks without AI."""
    issues = []
    suggestions = []

    # Check video properties
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", video_path],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        data = json.loads(result.stdout)
        vs = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
        if vs:
            w, h = int(vs["width"]), int(vs["height"])
            if w != 1080 or h != 1920:
                issues.append(f"Resolution {w}x{h}, expected 1080x1920")

            duration = float(data["format"]["duration"])
            if duration < 10:
                issues.append(f"Too short: {duration:.1f}s")
            elif duration > 60:
                issues.append(f"Too long: {duration:.1f}s")

            bitrate = int(data["format"].get("bit_rate", 0))
            if bitrate < 2000000:
                issues.append(f"Low bitrate: {bitrate/1000:.0f}kbps")

    # Check file size
    size_mb = os.path.getsize(video_path) / (1024 * 1024)
    if size_mb < 5:
        issues.append(f"Small file: {size_mb:.1f}MB - may indicate quality issues")

    # Grade based on issues
    if not issues:
        grade = "B"  # Can't be A without AI review of content
    elif len(issues) == 1:
        grade = "B"
    elif len(issues) <= 3:
        grade = "C"
    else:
        grade = "D"

    return {
        "grade": grade,
        "hook_grade": "?",
        "caption_grade": "?",
        "framing_grade": "?",
        "issues": issues if issues else ["automated check passed - needs visual review"],
        "suggestions": suggestions if suggestions else ["run with Claude for visual review"],
        "review_type": "automated",
    }


def review_clip(clip_num: int = None, video_path: str = None) -> dict:
    """Review a single clip."""
    if not video_path and clip_num:
        # Find video in DB
        db = sqlite3.connect(str(DB_PATH))
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT trending_video_path, video_path, top_hook FROM videos WHERE id = ?",
            (clip_num,)
        ).fetchone()
        db.close()
        if row:
            video_path = row["trending_video_path"] or row["video_path"]

    if not video_path or not Path(video_path).exists():
        return {"grade": "F", "issues": ["video not found"]}

    print(f"  Extracting frames...")
    frames = extract_frames(video_path)
    if not frames:
        return {"grade": "F", "issues": ["failed to extract frames"]}

    print(f"  {len(frames)} frames extracted, reviewing...")

    # Try Claude first
    review = review_with_claude(video_path, frames)
    if review:
        review["review_type"] = "claude"
    else:
        review = review_automated(video_path, frames)

    # Cleanup frames
    import shutil
    frame_dir = str(Path(frames[0]).parent)
    shutil.rmtree(frame_dir, ignore_errors=True)

    return review


def review_all():
    """Review all ready clips."""
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    clips = db.execute("""
        SELECT v.id, v.trending_video_path, v.video_path, v.top_hook,
               v.duration_seconds, v.trending_track, s.hook_text
        FROM videos v JOIN scripts s ON v.script_id = s.id
        WHERE v.status = 'ready'
        ORDER BY v.id
    """).fetchall()
    db.close()

    QA_LOG.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*50}")
    print(f"QA REVIEW: {len(clips)} clips")
    print(f"{'='*50}\n")

    results = []
    for clip in clips:
        video = clip["trending_video_path"] or clip["video_path"]
        hook = clip["top_hook"] or clip["hook_text"][:40]
        print(f"Clip {clip['id']}: {hook}")
        print(f"  Duration: {clip['duration_seconds']:.1f}s | Audio: {clip['trending_track'] or 'none'}")

        review = review_clip(video_path=video)
        review["clip_id"] = clip["id"]
        review["top_hook"] = hook
        review["trending_track"] = clip["trending_track"]
        results.append(review)

        grade = review["grade"]
        print(f"  Grade: {grade} | Issues: {', '.join(review.get('issues', []))}")
        print()

        # Log to file
        with open(QA_LOG, "a") as f:
            f.write(json.dumps(review) + "\n")

    # Summary
    grades = [r["grade"] for r in results]
    print(f"\n{'='*50}")
    print(f"SUMMARY: {len(results)} clips reviewed")
    for g in ["A", "B", "C", "D", "F"]:
        count = grades.count(g)
        if count:
            print(f"  {g}: {count}")
    print(f"{'='*50}")

    return results


def main():
    parser = argparse.ArgumentParser(description="QA Review pipeline output")
    parser.add_argument("--clip", type=int, help="Review specific clip ID")
    parser.add_argument("--fix", action="store_true", help="Auto-fix issues found")
    args = parser.parse_args()

    if args.clip:
        review = review_clip(clip_num=args.clip)
        print(json.dumps(review, indent=2))
    else:
        review_all()


if __name__ == "__main__":
    main()
