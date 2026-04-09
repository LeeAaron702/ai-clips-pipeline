#!/usr/bin/env python3
"""
Post-processing effects: snap zoom punches and sound effects.
Applied after captioning, before trending audio.

Snap zooms: 8-12% zoom in 3 frames, ease back over 15 frames.
Triggered by exclamations, dramatic words in transcript.

SFX: whoosh on hook (first 0.5s), bass hit on high-energy moments.
"""

import json
import os
import random
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SFX_DIR = PROJECT_ROOT / "assets" / "sfx"

# Words that trigger snap zooms and bass hits
ZOOM_TRIGGERS = {
    "die", "died", "dead", "kill", "crash", "crashed", "fire", "explode",
    "destroyed", "smash", "impossible", "incredible", "amazing", "insane",
    "terrible", "horrible", "brilliant", "genius", "perfect", "ruined",
    "disaster", "nightmare", "ridiculous", "magnificent", "hilarious",
    "yes", "no", "wow", "oh", "god", "hell", "bloody",
}


def find_zoom_moments(words: list[dict], max_zooms: int = 4) -> list[float]:
    """Find timestamps of high-energy words for snap zooms."""
    moments = []
    for w in words:
        text = w["word"].strip().lower().rstrip(".,!?;:'\"")
        is_exclamation = w["word"].strip().endswith("!")
        is_trigger = text in ZOOM_TRIGGERS

        if is_trigger or is_exclamation:
            # Don't place zooms too close together (min 3s apart)
            if not moments or (w["start"] - moments[-1]) > 3.0:
                moments.append(w["start"])

    # Limit to max_zooms, spread throughout clip
    if len(moments) > max_zooms:
        # Pick evenly spaced ones
        step = len(moments) / max_zooms
        moments = [moments[int(i * step)] for i in range(max_zooms)]

    return moments


def apply_snap_zooms(input_path: str, output_path: str, zoom_times: list[float],
                     zoom_pct: float = 8.0) -> str:
    """Apply snap zoom punches at specific timestamps using ffmpeg."""
    if not zoom_times:
        # Just copy
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

    # Build zoompan filter with snap zooms
    # Each zoom: ramp up over 3 frames (0.12s), ease back over 15 frames (0.6s)
    # Total zoom event: ~0.72s
    zoom_up_dur = 0.12
    zoom_down_dur = 0.6
    max_zoom = 1.0 + zoom_pct / 100.0

    # Build a complex expression for zoom factor based on time
    # For each zoom moment, create a pulse
    zoom_parts = []
    for t in zoom_times:
        # Ramp up: t to t+zoom_up_dur
        # Ramp down: t+zoom_up_dur to t+zoom_up_dur+zoom_down_dur
        zoom_parts.append(
            f"if(between(t,{t:.3f},{t+zoom_up_dur:.3f}),"
            f"{max_zoom - 1.0}*(t-{t:.3f})/{zoom_up_dur},"
            f"if(between(t,{t+zoom_up_dur:.3f},{t+zoom_up_dur+zoom_down_dur:.3f}),"
            f"{max_zoom - 1.0}*(1-(t-{t+zoom_up_dur:.3f})/{zoom_down_dur}),"
            f"0))"
        )

    if not zoom_parts:
        zoom_expr = "1"
    else:
        zoom_expr = "1+" + "+".join(zoom_parts)

    # Apply via ffmpeg zoompan
    vf = (
        f"scale=1188:2112,"  # Scale up 10% so we have room to crop when zoomed
        f"zoompan=z='{zoom_expr}'"
        f":x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2'"
        f":d=1:s=1080x1920:fps=25"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  Snap zoom failed: {result.stderr[-200:]}")
        # Fall back to copy
        import shutil
        shutil.copy2(input_path, output_path)
    return output_path


def add_sfx(input_path: str, output_path: str, zoom_times: list[float],
            add_whoosh: bool = True) -> str:
    """Mix sound effects into the video at specific moments."""
    whoosh_path = SFX_DIR / "whoosh.mp3"
    bass_path = SFX_DIR / "bass_drop.mp3"

    if not bass_path.exists() and not whoosh_path.exists():
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

    # Build filter chain to overlay SFX at specific times
    inputs = ["-i", input_path]
    filter_parts = []
    input_idx = 1

    # Whoosh at 0.3s (hook appearance)
    if add_whoosh and whoosh_path.exists():
        inputs.extend(["-i", str(whoosh_path)])
        filter_parts.append(
            f"[{input_idx}:a]volume=0.4,adelay=300|300[whoosh]"
        )
        input_idx += 1

    # Bass hits at zoom moments
    bass_labels = []
    if bass_path.exists():
        for i, t in enumerate(zoom_times[:4]):
            inputs.extend(["-i", str(bass_path)])
            delay_ms = int(t * 1000)
            label = f"bass{i}"
            filter_parts.append(
                f"[{input_idx}:a]volume=0.3,adelay={delay_ms}|{delay_ms}[{label}]"
            )
            bass_labels.append(f"[{label}]")
            input_idx += 1

    if not filter_parts:
        import shutil
        shutil.copy2(input_path, output_path)
        return output_path

    # Mix all audio streams
    all_audio = "[0:a]"
    if add_whoosh and whoosh_path.exists():
        all_audio += "[whoosh]"
    all_audio += "".join(bass_labels)

    n_inputs = 1 + (1 if add_whoosh and whoosh_path.exists() else 0) + len(bass_labels)
    filter_parts.append(
        f"{all_audio}amix=inputs={n_inputs}:duration=first:dropout_transition=2[aout]"
    )

    filter_complex = ";".join(filter_parts)

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  SFX mix failed: {result.stderr[-200:]}")
        import shutil
        shutil.copy2(input_path, output_path)

    return output_path
