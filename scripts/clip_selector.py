#!/usr/bin/env python3
"""
Analyze transcript and select the best viral moments for TikTok clips.

Usage:
    python3 scripts/clip_selector.py data/transcripts/S01E01.json
    python3 scripts/clip_selector.py data/transcripts/S01E01.json --max-clips 15
"""

import argparse
import json
import random
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXCITEMENT_WORDS = {
    "wow", "incredible", "amazing", "unbelievable", "insane", "crash", "crashed",
    "explode", "explosion", "fire", "destroyed", "smash", "smashed", "fastest",
    "record", "brilliant", "genius", "terrible", "horrible", "rubbish", "disaster",
    "impossible", "ridiculous", "magnificent", "spectacular", "terrifying", "hilarious",
    "laugh", "laughing", "bloody", "blimey", "crikey", "honestly", "seriously",
    "killed", "broke", "broken", "fail", "failed", "nightmare", "perfect",
    "beautiful", "gorgeous", "ruined", "chaos", "mental", "bonkers",
}

CATCHPHRASES = {
    "how hard can it be", "tonight", "on that bombshell", "some say",
    "the stig", "ambitious but rubbish", "oh no", "oh cock",
    "power", "speed", "and on that terrible disappointment",
    "in the world", "what could possibly go wrong",
}

# Hashtag pools for rotation
CORE_HASHTAGS = ["#topgear", "#fyp", "#foryou"]
PRESENTER_HASHTAGS = ["#jeremyclarkson", "#richardhammond", "#jamesmay", "#thestig"]
VIBE_HASHTAGS = ["#cars", "#funny", "#comedy", "#viral", "#carsoftiktok", "#automotive"]
EPISODE_HASHTAGS = {
    "botswana": ["#botswana", "#africa", "#adventure", "#safari", "#roadtrip"],
    "vietnam": ["#vietnam", "#asia", "#adventure", "#motorbike", "#roadtrip"],
    "bolivia": ["#bolivia", "#southamerica", "#adventure", "#deathroad", "#roadtrip"],
    "burma": ["#burma", "#myanmar", "#asia", "#adventure", "#bridge"],
    "usa": ["#usa", "#america", "#roadtrip", "#muscle"],
    "middle east": ["#middleeast", "#desert", "#adventure", "#roadtrip"],
    "india": ["#india", "#asia", "#adventure", "#roadtrip", "#train"],
    "africa": ["#africa", "#adventure", "#safari", "#roadtrip", "#nile"],
    "winter": ["#winter", "#olympics", "#snow", "#ice", "#norway"],
}


def get_episode_hashtags(episode_name: str) -> list:
    """Get episode-specific hashtags based on episode name."""
    name_lower = episode_name.lower()
    for key, tags in EPISODE_HASHTAGS.items():
        if key in name_lower:
            return tags
    return ["#adventure", "#roadtrip"]


def generate_hashtags(episode_name: str) -> str:
    """Generate a rotated set of 6-8 hashtags."""
    tags = list(CORE_HASHTAGS)  # Always include core
    tags.append(random.choice(PRESENTER_HASHTAGS))
    tags.extend(random.sample(VIBE_HASHTAGS, min(2, len(VIBE_HASHTAGS))))
    ep_tags = get_episode_hashtags(episode_name)
    tags.extend(random.sample(ep_tags, min(2, len(ep_tags))))
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return " ".join(unique[:8])


def is_music_heavy(text: str) -> bool:
    """Check if text is mostly music/non-speech tags."""
    words = text.split()
    if not words:
        return True
    music_words = sum(1 for w in words if w.upper().strip("[]()") in {"MUSIC", "APPLAUSE", "LAUGHTER"})
    return music_words / len(words) > 0.5


def score_segment(segment: dict, prev_segment: dict = None, next_segment: dict = None) -> float:
    """Score a transcript segment for viral potential."""
    text = segment["text"].lower()
    score = 0.0

    # Penalize music-heavy segments
    if is_music_heavy(text):
        return -10.0

    for word in EXCITEMENT_WORDS:
        if word in text:
            score += 2.0

    for phrase in CATCHPHRASES:
        if phrase in text:
            score += 5.0

    score += text.count("!") * 1.5
    score += text.count("?") * 1.0

    caps_words = len(re.findall(r'\b[A-Z]{2,}\b', segment["text"]))
    score += caps_words * 2.0

    word_count = len(text.split())
    if word_count <= 8:
        score += 1.5

    if prev_segment:
        gap = segment["start"] - prev_segment["end"]
        if gap < 0.5:
            score += 2.0

    if any(w in text for w in ["ha", "haha", "laugh", "[laughter]", "[applause]"]):
        score += 3.0

    return score


def _text_ends_sentence(text: str) -> bool:
    """Check if text ends at a sentence boundary (period, question mark, exclamation)."""
    stripped = text.rstrip()
    return stripped.endswith((".", "?", "!"))


def _find_sentence_end_time(segment: dict) -> float | None:
    """
    Find the timestamp of the last sentence-ending word in a segment using
    word-level timestamps.  Returns the end time of that word, or None if
    the segment has no word-level data or no sentence-ender.
    """
    words = segment.get("words", [])
    if not words:
        return None
    # Walk backwards through words looking for one that ends a sentence
    for w in reversed(words):
        word_text = w.get("word", "").rstrip()
        if word_text.endswith((".", "?", "!")):
            return w["end"]
    return None


def find_clip_boundaries(segments: list[dict], center_idx: int, target_duration: tuple = (12, 45)) -> dict:
    """
    Find natural clip boundaries around a high-scoring segment.
    Target 15-30s for growth phase (better completion rates).

    Clips are extended to the nearest sentence boundary so speakers are not
    cut off mid-sentence.  Hard max is 55s to stay TikTok-friendly.
    A 0.3s silence buffer is added after the last word.
    """
    min_dur, max_dur = target_duration
    HARD_MAX = 55  # absolute ceiling including sentence extension
    SILENCE_BUFFER = 0.3  # breathing room after last word

    start_idx = center_idx
    end_idx = center_idx

    while start_idx > 0:
        candidate = start_idx - 1
        new_start = segments[candidate]["start"]
        duration = segments[end_idx]["end"] - new_start
        if duration > max_dur:
            break
        gap = segments[start_idx]["start"] - segments[candidate]["end"]
        if gap > 1.2 and duration >= min_dur:
            break
        start_idx = candidate

    while end_idx < len(segments) - 1:
        candidate = end_idx + 1
        new_end = segments[candidate]["end"]
        duration = new_end - segments[start_idx]["start"]
        if duration > max_dur:
            break
        gap = segments[candidate]["start"] - segments[end_idx]["end"]
        if gap > 1.2 and duration >= min_dur:
            break
        end_idx = candidate

    # --- Sentence-boundary extension ---
    # If the last segment ends mid-sentence, keep extending until we hit a
    # sentence boundary or the HARD_MAX duration ceiling.
    if not _text_ends_sentence(segments[end_idx]["text"]):
        extended = end_idx
        while extended < len(segments) - 1:
            candidate = extended + 1
            new_end = segments[candidate]["end"]
            duration = new_end - segments[start_idx]["start"]
            if duration > HARD_MAX:
                break
            extended = candidate
            if _text_ends_sentence(segments[extended]["text"]):
                break
        end_idx = extended

    # Determine precise end time.
    # If the final segment contains a sentence ender but also trailing words
    # after it, use word-level timestamps to land exactly on the sentence end.
    end_sec = segments[end_idx]["end"]
    if _text_ends_sentence(segments[end_idx]["text"]):
        sent_end = _find_sentence_end_time(segments[end_idx])
        if sent_end is not None:
            end_sec = sent_end

    # Add a small silence buffer so the cut doesn't feel abrupt
    end_sec += SILENCE_BUFFER

    clip_segments = segments[start_idx:end_idx + 1]
    full_text = " ".join(s["text"] for s in clip_segments)

    return {
        "start_sec": segments[start_idx]["start"],
        "end_sec": end_sec,
        "duration": end_sec - segments[start_idx]["start"],
        "text_preview": full_text[:150],
        "segment_indices": (start_idx, end_idx),
        "score": sum(score_segment(s) for s in clip_segments),
        "is_music_heavy": is_music_heavy(full_text),
    }


def select_clips_heuristic(transcript: dict, max_clips: int = 12, episode_name: str = "") -> list[dict]:
    """Select the best clips using text analysis heuristics."""
    segments = transcript["segments"]
    if not segments:
        return []

    scored = []
    for i, seg in enumerate(segments):
        prev_seg = segments[i - 1] if i > 0 else None
        next_seg = segments[i + 1] if i < len(segments) - 1 else None
        score = score_segment(seg, prev_seg, next_seg)
        scored.append((i, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    clips = []
    used_ranges = []

    for seg_idx, seg_score in scored:
        if len(clips) >= max_clips:
            break
        if seg_score < 2.0:
            break

        clip = find_clip_boundaries(segments, seg_idx)

        # Skip music-heavy clips
        if clip["is_music_heavy"]:
            continue

        # Skip very short clips
        if clip["duration"] < 10 or clip["score"] < 2.0:
            continue

        overlaps = False
        for used_start, used_end in used_ranges:
            if clip["start_sec"] < used_end and clip["end_sec"] > used_start:
                overlaps = True
                break

        if overlaps:
            continue

        clips.append(clip)
        used_ranges.append((clip["start_sec"], clip["end_sec"]))

    clips.sort(key=lambda c: c["start_sec"])

    ep_name = episode_name or transcript.get("episode", "")
    for i, clip in enumerate(clips):
        clip["name"] = f"clip_{i+1:03d}"
        clip["caption"] = generate_caption(clip["text_preview"], ep_name)
        clip["hashtags"] = generate_hashtags(ep_name)

    return clips


def generate_caption(text_preview: str, episode_name: str = "") -> str:
    """Generate a TikTok caption + hashtags from clip content."""
    hashtags = generate_hashtags(episode_name)
    short = text_preview[:80].strip()
    if not short.endswith((".", "!", "?")):
        short += "..."
    return f"{short} {hashtags}"


def get_transcript_summary(transcript: dict) -> str:
    """Get a text summary for Claude to analyze."""
    lines = []
    for seg in transcript["segments"]:
        minutes = int(seg["start"] // 60)
        seconds = int(seg["start"] % 60)
        lines.append(f"[{minutes:02d}:{seconds:02d}] {seg['text']}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Select best TikTok clips from transcript")
    parser.add_argument("transcript", help="Path to transcript JSON file")
    parser.add_argument("--max-clips", type=int, default=12, help="Maximum clips to select")
    parser.add_argument("--output", help="Output JSON path for clip list")
    parser.add_argument("--summary", action="store_true", help="Print timestamped transcript for Claude")
    args = parser.parse_args()

    with open(args.transcript) as f:
        transcript = json.load(f)

    if args.summary:
        print(get_transcript_summary(transcript))
        return

    clips = select_clips_heuristic(transcript, args.max_clips)

    print(f"\nSelected {len(clips)} clips from {transcript.get('episode', 'unknown')}:\n")
    for clip in clips:
        print(f"  {clip['name']}: {clip['start_sec']:.1f}s - {clip['end_sec']:.1f}s ({clip['duration']:.1f}s) [score: {clip['score']:.1f}]")
        print(f"    {clip['text_preview'][:100]}...")
        print()

    output_path = args.output or str(PROJECT_ROOT / "data" / "transcripts" / f"{Path(args.transcript).stem}_clips.json")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(clips, f, indent=2)
    print(f"Saved clip list: {output_path}")


if __name__ == "__main__":
    main()
