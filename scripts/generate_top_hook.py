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

    return "YOU NEED TO SEE THIS..."


def generate_hook_from_transcript(words: list[dict], episode_name: str = "", clip_num: int = 0) -> str:
    """Generate hook from transcript."""
    hook = generate_hook_heuristic(words, episode_name)
    print(f"  Hook: {hook}")
    return hook
