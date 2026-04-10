#!/usr/bin/env python3
"""
Generate TikTok post captions and hashtags using Claude Code Opus.
Creates engaging, searchable captions optimized for TikTok discovery.
Falls back to heuristic generation if Claude unavailable.

Content-agnostic: works for any source material, not tied to a specific show.
"""

import subprocess
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLAUDE_PATH = "/Users/hermes/.local/bin/claude"

CAPTION_PROMPT = """Generate a TikTok post caption and hashtags for this video clip.

RULES:
1. Caption: 1-2 short punchy sentences that create intrigue. Conversational, funny, or dramatic.
2. DO NOT just describe what happens — create curiosity or emotion.
3. MUST end with a comment-bait question or hot take that provokes debate. Examples:
   - "Would you survive this?"
   - "Who's side are you on?"
   - "Best road trip moment ever?"
   - "Could you handle this?"
   - "Agree or disagree?"
   - "What would you do here?"
   The question should feel natural, not forced. It should make someone want to type a reply.
4. Hashtags: 8-12 total, organized in tiers:
   - ALWAYS include: #fyp #foryou #viral
   - Content-type tags (pick 3-4 based on clip content): #cars #funny #adventure #roadtrip #travel #comedy #fails #challenge #epic #nature
   - Specific tags (pick 2-3 based on what's actually in the clip): people, places, situations, vehicles, emotions
   - DO NOT hardcode show-specific tags unless the show name adds real discovery value
5. Total caption + hashtags must be under 300 characters.
6. Return ONLY the caption text followed by hashtags on the same line. No quotes, no explanation.

EXAMPLE OUTPUTS:
He bought a car for $150 and drove it across Africa. Would you trust your life to this thing? #fyp #foryou #viral #cars #adventure #africa #roadtrip #funny #fails

Three guys. One challenge. Zero chance of survival. Who would you pick for your team? #fyp #viral #funny #challenge #cars #comedy #epic #teamwork

This man just proved everyone wrong in the most dramatic way possible. Agree or disagree? #fyp #foryou #viral #epic #cars #adventure #travel"""


SERIES_TAG = "Part"


def generate_caption_with_claude(transcript_text: str, episode_name: str = "",
                                  hook_text: str = "", clip_num: int = 0) -> str:
    """Use Claude Code Opus to generate post caption."""
    series_context = ""
    if clip_num > 0:
        series_context = f"\nThis is {SERIES_TAG} {clip_num} from this episode. Include '{SERIES_TAG} {clip_num}' naturally in the caption or at the end before hashtags."

    prompt = f"""{CAPTION_PROMPT}
{series_context}
EPISODE/SOURCE: {episode_name}
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
                                hook_text: str = "", clip_num: int = 0) -> str:
    """Fallback heuristic caption generation."""
    text_lower = transcript_text.lower()

    # Tier 1: always
    tags = ["#fyp", "#foryou", "#viral"]

    # Tier 2: content-type detection
    if any(w in text_lower for w in ["car", "drive", "engine", "speed", "road", "motor"]):
        tags.extend(["#cars", "#carsoftiktok"])
    if any(w in text_lower for w in ["funny", "laugh", "idiot", "stupid", "hilarious"]):
        tags.append("#funny")
    if any(w in text_lower for w in ["africa", "vietnam", "bolivia", "india", "burma", "travel", "journey"]):
        tags.extend(["#adventure", "#travel", "#roadtrip"])
    if any(w in text_lower for w in ["crash", "broke", "fail", "destroy", "dead"]):
        tags.append("#fails")
    tags.extend(["#comedy", "#epic"])

    # Tier 3: specific context
    ep_lower = (episode_name or "").lower()
    for loc in ["botswana", "vietnam", "bolivia", "india", "burma", "africa", "usa", "middle east"]:
        if loc in ep_lower or loc in text_lower:
            tags.append(f"#{loc.replace(' ', '')}")
            break

    # Caption from hook or first sentence
    sentences = re.split(r'(?<=[.!?])\s+', transcript_text)
    if hook_text and "YOU NEED TO SEE THIS" not in hook_text:
        caption = hook_text
    elif sentences:
        caption = sentences[0][:100]
    else:
        caption = "This is what happens when things go sideways."

    # Add series tag
    if clip_num > 0:
        caption = f"{caption} {SERIES_TAG} {clip_num}."

    # Add comment bait
    baits = [
        "Would you survive this?",
        "Could you handle this?",
        "What would you do?",
        "Best moment ever?",
        "Agree or disagree?",
        "Who's side are you on?",
        "Tag someone who needs to see this.",
        "Have you seen anything like this?",
    ]
    import hashlib
    bait_idx = int(hashlib.md5(transcript_text[:50].encode()).hexdigest(), 16) % len(baits)
    caption = f"{caption} {baits[bait_idx]}"

    tag_str = " ".join(dict.fromkeys(tags))
    result = f"{caption} {tag_str}"
    return result[:300]


def generate_post_caption(words: list[dict], episode_name: str = "",
                           hook_text: str = "", clip_num: int = 0) -> str:
    """Generate post caption - tries Claude Opus first, falls back to heuristic."""
    transcript_text = " ".join(w["word"].strip() for w in words[:60])

    caption = generate_caption_with_claude(transcript_text, episode_name, hook_text, clip_num)
    if caption:
        print(f"  Post caption (Opus): {caption[:80]}...")
        return caption

    caption = generate_caption_heuristic(transcript_text, episode_name, hook_text, clip_num)
    print(f"  Post caption (heuristic): {caption[:80]}...")
    return caption
