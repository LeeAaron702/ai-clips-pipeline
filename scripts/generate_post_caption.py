#!/usr/bin/env python3
"""
Generate TikTok post captions and hashtags using Claude Code Opus.
Creates engaging, searchable captions optimized for TikTok discovery.
Falls back to heuristic generation if Claude unavailable.
"""

import subprocess
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_PATH = "/Users/hermes/.local/bin/claude"

CAPTION_PROMPT = """Generate a TikTok post caption and hashtags for this clip from Top Gear.

RULES:
1. Caption: 1-2 short sentences that make people want to watch. Conversational, funny, or dramatic tone.
2. DO NOT just describe what happens - create intrigue or humor.
3. Use the presenters' names when relevant (Clarkson, Hammond, May).
4. End with a question or call to action when natural ("Would you survive this?" / "Tag someone who drives like this")
5. Hashtags: 8-12 relevant hashtags. Mix of:
   - High volume: #topgear #fyp #foryou #viral #cars
   - Medium: #jeremyclarkson #richardhammond #jamesmay #carsoftiktok
   - Niche/episode-specific: location, car model, situation
6. Total caption + hashtags must be under 300 characters.
7. Return ONLY the caption text followed by hashtags on the same line. No quotes, no explanation.

EXAMPLE OUTPUT:
Clarkson bought a car for $150 and somehow it's the best decision he's ever made. Would you trust this thing? #topgear #fyp #jeremyclarkson #cars #africa #adventure #carsoftiktok #funny"""


def generate_caption_with_claude(transcript_text: str, episode_name: str = "",
                                  hook_text: str = "") -> str:
    """Use Claude Code Opus to generate post caption."""
    prompt = f"""{CAPTION_PROMPT}

EPISODE: {episode_name}
HOOK (for context): {hook_text}
TRANSCRIPT OF CLIP:
{transcript_text}

Caption + hashtags:"""

    try:
        result = subprocess.run(
            [CLAUDE_PATH, "-p", prompt, "--model", "opus"],
            capture_output=True, text=True, timeout=45,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            caption = result.stdout.strip().strip('"\'')
            if caption and len(caption) > 10 and "#" in caption:
                # Ensure under 300 chars
                if len(caption) > 300:
                    # Truncate caption part, keep hashtags
                    parts = caption.split("#", 1)
                    if len(parts) == 2:
                        text_part = parts[0].strip()[:150]
                        hash_part = "#" + parts[1]
                        caption = f"{text_part} {hash_part}"[:300]
                return caption
    except Exception as e:
        print(f"  Claude caption gen failed: {e}")
    return None


def generate_caption_heuristic(transcript_text: str, episode_name: str = "",
                                hook_text: str = "") -> str:
    """Fallback heuristic caption generation."""
    # Extract key elements
    ep_lower = episode_name.lower()

    # Base hashtags
    tags = ["#topgear", "#fyp", "#foryou", "#viral"]

    # Presenter detection
    text_lower = transcript_text.lower()
    if "clarkson" in text_lower or "jeremy" in text_lower:
        tags.append("#jeremyclarkson")
    if "hammond" in text_lower or "richard" in text_lower:
        tags.append("#richardhammond")
    if "james" in text_lower or "may" in text_lower:
        tags.append("#jamesmay")

    tags.extend(["#cars", "#carsoftiktok"])

    # Episode-specific
    for loc in ["botswana", "vietnam", "bolivia", "india", "burma", "africa", "usa", "middle east"]:
        if loc in ep_lower:
            tags.append(f"#{loc.replace(' ', '')}")
            tags.append("#adventure")
            break

    tags.extend(["#funny", "#comedy"])

    # Caption from hook or first sentence
    sentences = re.split(r'(?<=[.!?])\s+', transcript_text)
    if hook_text and hook_text != "YOU NEED TO SEE THIS...":
        caption = hook_text
    elif sentences:
        caption = sentences[0][:100]
    else:
        caption = "This is what happens when you let these three loose."

    # Combine
    tag_str = " ".join(dict.fromkeys(tags))  # dedupe preserving order
    result = f"{caption} {tag_str}"
    return result[:300]


def generate_post_caption(words: list[dict], episode_name: str = "",
                           hook_text: str = "") -> str:
    """Generate post caption - tries Claude Opus first, falls back to heuristic."""
    transcript_text = " ".join(w["word"].strip() for w in words[:60])

    caption = generate_caption_with_claude(transcript_text, episode_name, hook_text)
    if caption:
        print(f"  Post caption (Opus): {caption[:80]}...")
        return caption

    caption = generate_caption_heuristic(transcript_text, episode_name, hook_text)
    print(f"  Post caption (heuristic): {caption[:80]}...")
    return caption
