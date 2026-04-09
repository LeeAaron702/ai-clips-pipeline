#!/usr/bin/env python3
"""
Generate TikTok hook captions. Smart heuristic with pattern matching.
Falls back to Claude CLI if available.
"""

import re
import random
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Pattern-based hook templates
HOOK_PATTERNS = {
    # Questions get "?!" treatment
    "question": lambda q: q.upper().rstrip("?") + "?!",
    # Danger/death
    "danger": lambda ctx: f"THIS COULD END BADLY...",
    # Reactions/surprises
    "reaction": lambda ctx: f"{ctx.upper()}",
    # Challenges/competitions
    "competition": lambda ctx: "WHO WINS THIS?!",
}

# Named entities in Top Gear
PRESENTERS = {
    "clarkson": "CLARKSON", "jeremy": "CLARKSON",
    "hammond": "HAMMOND", "richard": "HAMMOND",
    "james": "MAY", "may": "MAY",
    "stig": "THE STIG",
}

DRAMATIC_WORDS = {
    "die", "died", "dead", "death", "kill", "crash", "fire", "explode",
    "destroyed", "smash", "broke", "broken", "fail", "ruined", "disaster",
    "impossible", "insane", "ridiculous", "terrible", "horrible", "worst",
    "never", "nightmare", "dangerous", "water", "lost", "stuck",
}

EXCITEMENT_WORDS = {
    "incredible", "amazing", "brilliant", "perfect", "beautiful",
    "magnificent", "spectacular", "genius", "fastest", "best", "won",
    "love", "yes", "done",
}

ACTION_WORDS = {
    "crash", "race", "drive", "build", "fix", "modify", "remove",
    "cross", "ford", "climb", "jump", "overtake", "destroy",
}


def find_presenter(words_text: str) -> str:
    """Find which presenter is speaking/referenced."""
    lower = words_text.lower()
    for key, name in PRESENTERS.items():
        if key in lower:
            return name
    return None


def extract_key_noun(text: str) -> str:
    """Extract the most interesting noun/object from text."""
    # Look for car names, places, animals, objects
    interesting = re.findall(
        r'\b(Mercedes|BMW|Opel|Lancia|Volkswagen|VW|Cadett?|Oliver|'
        r'desert|river|bridge|mountain|hill|salt|pan|mud|'
        r'lion|elephant|hippo|cow|badger|'
        r'engine|wheel|brake|radiator|gearbox|'
        r'Africa|Botswana|Makgadikgadi|Vietnam|Bolivia)\b',
        text, re.IGNORECASE
    )
    return interesting[0].upper() if interesting else None


def generate_hook_heuristic(words: list[dict], episode_name: str = "") -> str:
    """Generate a compelling hook from transcript content."""
    if not words:
        return "WAIT FOR THIS..."

    full_text = " ".join(w["word"].strip() for w in words[:50])
    sentences = re.split(r'(?<=[.!?])\s+', full_text)
    lower_text = full_text.lower()

    presenter = find_presenter(full_text)
    key_noun = extract_key_noun(full_text)

    # Strategy 1: Direct questions from the transcript
    for sent in sentences[:5]:
        if sent.endswith("?") and 3 <= len(sent.split()) <= 8:
            hook = sent.upper().rstrip("?") + "?!"
            return hook

    # Strategy 2: Dramatic words -> presenter + action hook
    for dw in DRAMATIC_WORDS:
        if dw in lower_text:
            if presenter:
                templates = [
                    f"{presenter} MIGHT NOT SURVIVE THIS",
                    f"THIS GOES WRONG FOR {presenter}",
                    f"{presenter} IN SERIOUS TROUBLE",
                    f"DID {presenter} JUST {dw.upper()} IT?!",
                ]
                return random.choice(templates)
            if key_noun:
                return f"THE {key_noun} SITUATION GETS WORSE..."
            return f"THIS GOES HORRIBLY WRONG..."

    # Strategy 3: Excitement words -> positive hook
    for ew in EXCITEMENT_WORDS:
        if ew in lower_text:
            if presenter:
                templates = [
                    f"{presenter} CAN'T BELIEVE THIS",
                    f"WAIT TILL YOU SEE {presenter}'S FACE",
                    f"{presenter} ACTUALLY PULLS IT OFF",
                ]
                return random.choice(templates)
            return "NOBODY EXPECTED THIS..."

    # Strategy 4: Action words with presenter
    for aw in ACTION_WORDS:
        if aw in lower_text:
            if presenter:
                return f"{presenter} TRIES TO {aw.upper()} IT"
            if key_noun:
                return f"THEY {aw.upper()} THE {key_noun}"

    # Strategy 5: Episode-specific context
    ep_lower = episode_name.lower()
    if "botswana" in ep_lower or "africa" in ep_lower:
        if presenter:
            return f"{presenter} VS THE AFRICAN WILDERNESS"
        return "AFRICA WINS AGAIN..."
    if "vietnam" in ep_lower:
        return "VIETNAM BREAKS EVERYTHING..."
    if "bolivia" in ep_lower:
        return "THE DEATH ROAD CLAIMS ANOTHER..."

    # Strategy 6: First punchy sentence
    if sentences and len(sentences[0].split()) <= 6:
        hook = sentences[0].upper()
        if not hook.endswith(("!", "?", ".")):
            hook += "..."
        return hook

    # Strategy 7: Presenter + generic
    if presenter:
        return f"{presenter} WASN'T READY FOR THIS"

    import hashlib
    # Rotate through varied fallbacks based on transcript content
    fallbacks = [
        "THIS DOESN"T END WELL...",
        "NOBODY SAW THIS COMING",
        "THINGS ARE ABOUT TO GO WRONG",
        "THIS IS WHERE IT GETS GOOD",
        "WATCH WHAT HAPPENS NEXT",
        "YOU WON"T BELIEVE THIS",
        "THEY HAD NO IDEA...",
        "THIS CHANGES EVERYTHING",
        "ABSOLUTE CHAOS INCOMING",
        "THE MOMENT IT ALL WENT WRONG",
        "THIS IS WHY WE LOVE TOP GEAR",
        "PEAK TOP GEAR RIGHT HERE",
        "THREE IDIOTS, ONE CHALLENGE",
        "WHEN IT GOES SIDEWAYS...",
        "THE LOOK ON THEIR FACES",
    ]
    # Pick deterministically based on transcript so same clip always gets same hook
    text_hash = int(hashlib.md5(" ".join(w["word"].strip() for w in words[:20]).encode()).hexdigest(), 16)
    return fallbacks[text_hash % len(fallbacks)]


CLAUDE_PATH = "/Users/hermes/.local/bin/claude"

HOOK_PROMPT = """Generate a short TikTok hook caption (max 6 words, ALL CAPS) for this clip.
This hook stays pinned at the TOP of the screen the entire video.

RULES:
- MAX 6 words. Shorter is better.
- ALL CAPS always.
- Create curiosity gap - hint without revealing
- Use emotional triggers: shock, humor, disbelief, danger
- Reference SPECIFIC people/events, not generic phrases
- NEVER spoil the payoff
- Return ONLY the hook text. No quotes, no explanation."""


def generate_hook_with_claude(transcript_text: str, episode_name: str = "") -> str:
    """Use Claude Code (Opus 4.6) to generate hook."""
    import subprocess
    prompt = f"{HOOK_PROMPT}\n\nEPISODE: {episode_name}\nTRANSCRIPT:\n{transcript_text}\n\nHook:"
    try:
        result = subprocess.run(
            [CLAUDE_PATH, "-p", prompt, "--model", "opus"],
            capture_output=True, text=True, timeout=45,
            cwd=str(PROJECT_ROOT),
        )
        if result.returncode == 0:
            hook = result.stdout.strip().strip('"\'').upper()
            words = hook.split()
            if len(words) > 7:
                hook = " ".join(words[:6]) + "..."
            if hook and len(hook) > 3:
                return hook
    except Exception as e:
        print(f"  Claude hook gen failed: {e}")
    return None


def generate_hook_from_transcript(words: list[dict], episode_name: str = "", clip_num: int = 0) -> str:
    """Generate hook - tries Claude Code Opus first, falls back to heuristic."""
    transcript_text = " ".join(w["word"].strip() for w in words[:50])

    hook = generate_hook_with_claude(transcript_text, episode_name)
    if hook:
        print(f"  Hook (Opus): {hook}")
        return hook

    hook = generate_hook_heuristic(words, episode_name)
    print(f"  Hook (heuristic): {hook}")
    return hook
