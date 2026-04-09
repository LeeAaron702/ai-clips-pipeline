#!/usr/bin/env python3
"""
Extract clips from episodes and convert to 9:16 TikTok format.
Supports dynamic face-tracking crop (Opus Clip style) using MediaPipe.

Usage:
    python3 scripts/cut_clips.py input/episodes/S01E01.mp4 --start 120.5 --end 155.0 --output output/clips/clip_001.mp4
    python3 scripts/cut_clips.py input/episodes/S01E01.mp4 --clips-json clips.json --output-dir output/clips/
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Face tracking config
SAMPLE_EVERY_N_FRAMES = 5       # Check every 5th frame for faces
SMOOTHING_WINDOW = 15           # Frames to smooth crop position over
FALLBACK_TO_CENTER = True       # Center crop when no face detected
FACE_PADDING_RATIO = 0.3       # Extra padding around detected face


def get_video_info(path: str) -> dict:
    """Get video width, height, and duration."""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True
    )
    data = json.loads(result.stdout)
    video_stream = next((s for s in data.get("streams", []) if s["codec_type"] == "video"), None)
    if not video_stream:
        return {}
    return {
        "width": int(video_stream["width"]),
        "height": int(video_stream["height"]),
        "duration": float(data["format"]["duration"]),
        "fps": eval(video_stream.get("r_frame_rate", "25/1")),
    }


def detect_face_positions(episode_path: str, start_sec: float, end_sec: float, src_w: int, src_h: int) -> list:
    """
    Detect face center X positions throughout the clip using MediaPipe Tasks API.
    Returns list of (frame_idx, face_center_x) tuples.
    """
    import mediapipe as mp

    model_path = str(PROJECT_ROOT / "assets" / "blaze_face_short_range.tflite")
    base_options = mp.tasks.BaseOptions(model_asset_path=model_path)
    options = mp.tasks.vision.FaceDetectorOptions(
        base_options=base_options,
        min_detection_confidence=0.5,
    )
    detector = mp.tasks.vision.FaceDetector.create_from_options(options)

    cap = cv2.VideoCapture(episode_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    start_frame = int(start_sec * fps)
    end_frame = int(end_sec * fps)
    cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

    positions = []
    frame_idx = 0
    total_frames = end_frame - start_frame

    while cap.isOpened():
        ret, frame = cap.read()
        if not ret or (start_frame + frame_idx) >= end_frame:
            break

        if frame_idx % SAMPLE_EVERY_N_FRAMES == 0:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            results = detector.detect(mp_image)

            if results.detections:
                best = max(results.detections, key=lambda d: d.categories[0].score)
                bbox = best.bounding_box
                face_cx = bbox.origin_x + bbox.width // 2
                positions.append((frame_idx, face_cx))
            else:
                positions.append((frame_idx, None))
        frame_idx += 1

    cap.release()
    detector.close()
    return positions, total_frames, fps


def smooth_crop_positions(positions: list, total_frames: int, src_w: int, crop_w: int) -> list:
    """
    Interpolate and smooth face positions into per-frame crop X offsets.
    Returns list of x_offset for every frame.
    """
    center_x = src_w // 2
    half_crop = crop_w // 2
    min_x = 0
    max_x = src_w - crop_w

    # Fill in all frames with detected or interpolated positions
    frame_positions = [None] * total_frames

    for frame_idx, face_cx in positions:
        if frame_idx < total_frames:
            frame_positions[frame_idx] = face_cx

    # Forward fill None values
    last_known = center_x
    filled = []
    for pos in frame_positions:
        if pos is not None:
            last_known = pos
        filled.append(last_known)

    # Backward fill for leading Nones
    last_known = center_x
    for i in range(len(filled) - 1, -1, -1):
        if frame_positions[i] is not None:
            last_known = filled[i]
        elif filled[i] == center_x and i > 0:
            filled[i] = last_known

    # Apply smoothing (moving average)
    kernel = SMOOTHING_WINDOW
    smoothed = []
    for i in range(len(filled)):
        window_start = max(0, i - kernel // 2)
        window_end = min(len(filled), i + kernel // 2 + 1)
        avg = sum(filled[window_start:window_end]) / (window_end - window_start)
        smoothed.append(int(avg))

    # Convert face center positions to crop X offsets
    offsets = []
    for face_cx in smoothed:
        x_offset = face_cx - half_crop
        x_offset = max(min_x, min(max_x, x_offset))
        offsets.append(x_offset)

    return offsets


def cut_clip(episode_path: str, start_sec: float, end_sec: float, output_path: str, use_face_tracking: bool = True) -> str:
    """
    Extract a clip and convert to 9:16 portrait (1080x1920).
    Uses face-tracking dynamic crop when possible, falls back to center crop.
    Returns output path on success.
    """
    episode_path = Path(episode_path).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    info = get_video_info(str(episode_path))
    src_w = info.get("width", 1920)
    src_h = info.get("height", 1080)
    src_fps = info.get("fps", 25)

    target_aspect = 9 / 16
    src_aspect = src_w / src_h

    if src_aspect > target_aspect:
        crop_w = int(src_h * target_aspect)
        crop_h = src_h
    else:
        crop_w = src_w
        crop_h = int(src_w / target_aspect)

    # Try face tracking for dynamic crop
    face_tracked = False
    if use_face_tracking and src_aspect > target_aspect:
        try:
            print("  Detecting faces for smart crop...")
            positions, total_frames, clip_fps = detect_face_positions(
                str(episode_path), start_sec, end_sec, src_w, src_h
            )

            # Check if we got enough face detections (>30% of sampled frames)
            detected_count = sum(1 for _, pos in positions if pos is not None)
            detection_rate = detected_count / max(len(positions), 1)

            if detection_rate > 0.3:
                offsets = smooth_crop_positions(positions, total_frames, src_w, crop_w)
                face_tracked = True
                print(f"  Face tracking: {detection_rate:.0%} detection rate, {len(offsets)} frames")

                # Write cropdetect data as a text file for ffmpeg sendcmd
                cmd_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, prefix='crop_')
                # Build per-frame crop commands using sendcmd
                # We'll use the crop filter with changing x offset via a Python-rendered approach
                # Actually, easiest: render via OpenCV directly

                # Use OpenCV to render the dynamically cropped video
                cap = cv2.VideoCapture(str(episode_path))
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(start_sec * src_fps))

                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                temp_video = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False, prefix='tracked_')
                temp_video_path = temp_video.name
                temp_video.close()

                out = cv2.VideoWriter(temp_video_path, fourcc, src_fps, (1080, 1920))

                frame_idx = 0
                end_frame = int((end_sec - start_sec) * src_fps)

                while cap.isOpened() and frame_idx < end_frame:
                    ret, frame = cap.read()
                    if not ret:
                        break

                    x_off = offsets[frame_idx] if frame_idx < len(offsets) else offsets[-1] if offsets else (src_w - crop_w) // 2
                    y_off = (src_h - crop_h) // 2

                    cropped = frame[y_off:y_off+crop_h, x_off:x_off+crop_w]
                    resized = cv2.resize(cropped, (1080, 1920), interpolation=cv2.INTER_LANCZOS4)
                    out.write(resized)
                    frame_idx += 1

                cap.release()
                out.release()
                cmd_file.close()
                os.unlink(cmd_file.name)

                # Now mux with audio using ffmpeg
                cmd = [
                    "ffmpeg", "-y",
                    "-i", temp_video_path,
                    "-ss", str(start_sec), "-to", str(end_sec),
                    "-i", str(episode_path),
                    "-map", "0:v", "-map", "1:a?",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                    "-movflags", "+faststart",
                    "-shortest",
                    str(output_path),
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                os.unlink(temp_video_path)

                if result.returncode != 0:
                    print(f"  Face-track mux failed, falling back to center crop")
                    face_tracked = False
            else:
                print(f"  Low face detection ({detection_rate:.0%}), using center crop")

        except Exception as e:
            print(f"  Face tracking error: {e}, falling back to center crop")
            face_tracked = False

    # Fallback: center crop
    if not face_tracked:
        vf = f"crop={crop_w}:{crop_h},scale=1080:1920"
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start_sec),
            "-to", str(end_sec),
            "-i", str(episode_path),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
            "-movflags", "+faststart",
            str(output_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"ERROR cutting clip: {result.stderr[-500:]}")
            return None

    # Verify output
    if output_path.exists():
        out_info = get_video_info(str(output_path))
        duration = end_sec - start_sec
        size_mb = output_path.stat().st_size / (1024 * 1024)
        tracked_label = " [face-tracked]" if face_tracked else ""
        print(f"  Clip: {output_path.name} | {duration:.1f}s | {out_info.get('width', '?')}x{out_info.get('height', '?')} | {size_mb:.1f}MB{tracked_label}")
        return str(output_path)

    print(f"ERROR: Output not created: {output_path}")
    return None


def cut_clips_batch(episode_path: str, clips: list[dict], output_dir: str) -> list[str]:
    """Cut multiple clips from an episode."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    episode_name = Path(episode_path).stem

    for i, clip in enumerate(clips):
        name = clip.get("name", f"clip_{i+1:03d}")
        output_path = output_dir / f"{episode_name}_{name}.mp4"
        print(f"[{i+1}/{len(clips)}] Cutting {clip['start_sec']:.1f}s - {clip['end_sec']:.1f}s...")
        result = cut_clip(episode_path, clip["start_sec"], clip["end_sec"], str(output_path))
        if result:
            results.append(result)

    print(f"\nCut {len(results)}/{len(clips)} clips successfully.")
    return results


def main():
    parser = argparse.ArgumentParser(description="Cut clips from episodes in 9:16 TikTok format")
    parser.add_argument("episode", help="Path to episode video file")
    parser.add_argument("--start", type=float, help="Start time in seconds (single clip mode)")
    parser.add_argument("--end", type=float, help="End time in seconds (single clip mode)")
    parser.add_argument("--output", help="Output path (single clip mode)")
    parser.add_argument("--clips-json", help="JSON file with clip timestamps (batch mode)")
    parser.add_argument("--output-dir", default="output/clips", help="Output directory (batch mode)")
    parser.add_argument("--no-face-tracking", action="store_true", help="Disable face tracking")
    args = parser.parse_args()

    if args.clips_json:
        with open(args.clips_json) as f:
            clips = json.load(f)
        cut_clips_batch(args.episode, clips, args.output_dir)
    elif args.start is not None and args.end is not None:
        output = args.output or f"output/clips/clip_{args.start:.0f}_{args.end:.0f}.mp4"
        cut_clip(args.episode, args.start, args.end, output, use_face_tracking=not args.no_face_tracking)
    else:
        parser.error("Provide --start/--end for single clip or --clips-json for batch mode")


if __name__ == "__main__":
    main()
