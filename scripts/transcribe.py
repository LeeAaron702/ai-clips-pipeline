#!/usr/bin/env python3
"""
Transcribe Top Gear episodes using faster-whisper with word-level timestamps.
Caches results as JSON in data/transcripts/.

Usage:
    python3 scripts/transcribe.py input/episodes/S01E01.mp4
    python3 scripts/transcribe.py input/episodes/S01E01.mp4 --model large-v3
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TRANSCRIPTS_DIR = PROJECT_ROOT / "data" / "transcripts"


def transcribe_episode(episode_path: str, model_size: str = "large-v3") -> dict:
    """
    Transcribe a video/audio file with word-level timestamps.
    Returns dict with segments and word data.
    Caches to data/transcripts/<filename>.json.
    """
    episode_path = Path(episode_path).resolve()
    if not episode_path.exists():
        print(f"ERROR: File not found: {episode_path}")
        sys.exit(1)

    # Check cache
    cache_path = TRANSCRIPTS_DIR / f"{episode_path.stem}.json"
    if cache_path.exists():
        print(f"Using cached transcript: {cache_path}")
        with open(cache_path) as f:
            return json.load(f)

    print(f"Transcribing: {episode_path.name}")
    print(f"Model: {model_size}")

    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    segments_iter, info = model.transcribe(
        str(episode_path),
        word_timestamps=True,
        language="en",
    )

    print(f"Detected language: {info.language} (prob: {info.language_probability:.2f})")
    print(f"Duration: {info.duration:.1f}s ({info.duration/60:.1f} min)")

    segments = []
    word_count = 0
    for segment in segments_iter:
        words = []
        if segment.words:
            for w in segment.words:
                words.append({
                    "word": w.word.strip(),
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "probability": round(w.probability, 3),
                })
                word_count += 1

        segments.append({
            "id": segment.id,
            "start": round(segment.start, 3),
            "end": round(segment.end, 3),
            "text": segment.text.strip(),
            "words": words,
        })

    result = {
        "episode": episode_path.name,
        "duration": round(info.duration, 3),
        "language": info.language,
        "model": model_size,
        "segments": segments,
        "word_count": word_count,
    }

    # Cache
    TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w") as f:
        json.dump(result, f, indent=2)

    print(f"Transcribed: {word_count} words, {len(segments)} segments")
    print(f"Saved: {cache_path}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Transcribe episodes with word timestamps")
    parser.add_argument("episode", help="Path to episode video file")
    parser.add_argument("--model", default="large-v3", help="Whisper model size (default: large-v3)")
    parser.add_argument("--force", action="store_true", help="Force re-transcription (ignore cache)")
    args = parser.parse_args()

    if args.force:
        cache_path = TRANSCRIPTS_DIR / f"{Path(args.episode).stem}.json"
        if cache_path.exists():
            cache_path.unlink()
            print(f"Cleared cache: {cache_path}")

    result = transcribe_episode(args.episode, args.model)
    print(f"\nDone. {result['word_count']} words from {len(result['segments'])} segments.")


if __name__ == "__main__":
    main()
