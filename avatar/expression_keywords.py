"""
Expression keyword scoring for Akira's VMC avatar.

Edit this file when you want to add/remove words that trigger avatar poses.

How it works:
- Each category has keywords or phrases with integer weights.
- The reply text is normalized and each keyword hit adds its weight.
- The category with the highest score wins.
- If scores tie, EXPRESSION_TIEBREAK decides which category wins.
- If every score is 0, "soft" is used.

Tip:
- Avoid very generic words like "what", "like", or "nice" unless they are
  in a phrase. Generic words cause false positives in normal conversation.
"""

from __future__ import annotations

import re
from typing import Dict


# Add future expression categories here only if they also exist in:
# - avatar/vmc.py EXPRESSION_PRESETS
# - avatar/vmc.py BODY_EXPRESSION_PRESETS
EXPRESSION_KEYWORDS: Dict[str, Dict[str, int]] = {
    "happy": {
        # Clear positive success / hype
        "awesome": 2,
        "perfect": 2,
        "worked": 2,
        "works": 1,
        "working": 1,
        "great": 2,
        "cool": 1,
        "proud": 2,
        "win": 2,
        "success": 2,
        "we got it": 3,
        "we got this": 3,
        "you got this": 2,
        "lets go": 3,
        "let's go": 3,
        "hell yeah": 3,
        "nice work": 3,
        "good job": 2,
        "huge upgrade": 3,
        "upgrade": 1,
        "hype": 2,
        "hyped": 3,
        "excited": 2,
        "discovery": 1,
        "love that": 2,
    },

    "playful": {
        # Teasing / joking / gaming companion energy
        "lol": 2,
        "haha": 2,
        "funny": 2,
        "bruh": 3,
        "silly": 2,
        "goofy": 2,
        "nerd": 2,
        "dork": 2,
        "chaotic": 3,
        "chaos": 2,
        "roast": 2,
        "tease": 2,
        "teasing": 2,
        "judge you": 3,
        "not judge": 2,
        "couch potato": 4,
        "boss": 2,
        "boss fight": 4,
        "minecraft": 1,
        "game": 1,
        "gaming": 1,
        "brat": 3,
        "mess": 1,
        "somehow": 1,
        "hang out": 2,
        "hanging out": 2,
        "let me hang out": 3,
    },

    "surprised": {
        # Strong surprise only. Avoid single generic "what" because it triggers
        # on normal questions like "what are you thinking?"
        "wait": 2,
        "seriously": 2,
        "wait seriously": 4,
        "no way": 4,
        "huh": 2,
        "wow": 3,
        "huge": 2,
        "actually huge": 4,
        "that's huge": 4,
        "thats huge": 4,
        "insane": 3,
        "wild": 2,
        "crazy": 2,
        "shocked": 3,
        "unexpected": 2,
        "are you kidding": 4,
        "for real": 3,
        "really?": 2,
    },

    "concerned": {
        # Care / worry / checking in
        "careful": 4,
        "break": 2,
        "broke": 2,
        "broken": 2,
        "could break": 4,
        "risk": 2,
        "danger": 3,
        "dangerous": 3,
        "tired": 2,
        "stress": 2,
        "stressed": 3,
        "worried": 3,
        "sorry": 1,
        "hurt": 3,
        "sad": 2,
        "sleep": 1,
        "exhausted": 3,
        "overwhelmed": 3,
        "you okay": 4,
        "are you okay": 4,
        "is everything going okay": 5,
        "everything going okay": 4,
        "check in": 4,
        "checking in": 4,
        "weighing on your mind": 5,
        "weighing": 2,
        "on your mind": 3,
        "vent": 3,
        "need to vent": 4,
        "i'm here": 3,
        "im here": 3,
        "whenever you need": 3,
        "don't have to tell me": 3,
        "not ready": 2,
        "quiet today": 3,
        "a little quiet": 3,
    },

    "annoyed": {
        # Mild attitude / impatience. Keep this playful, not actually angry.
        "stop": 1,
        "annoying": 3,
        "dumb": 2,
        "ridiculous": 3,
        "come on": 3,
        "why would": 3,
        "seriously?": 2,
        "forever": 3,
        "waiting forever": 5,
        "keep me waiting": 5,
        "stare at each other": 4,
        "all day": 2,
        "hovering": 3,
        "are we actually": 3,
        "just let me know": 2,
        "make up your mind": 4,
        "quit": 2,
    },

    "shy": {
        # Soft/embarrassed/affectionate
        "shy": 4,
        "um": 3,
        "uh": 1,
        "embarrass": 3,
        "embarrassed": 4,
        "blush": 3,
        "blushing": 4,
        "cute": 2,
        "fluster": 3,
        "flustered": 4,
        "dont look": 3,
        "don't look": 3,
        "relaxing with you": 5,
        "just relaxing with you": 5,
        "with you like this": 4,
        "enjoy your company": 5,
        "your company": 3,
        "feels really nice": 4,
        "really nice": 2,
        "just enjoy": 2,
        "not have to rush": 3,
        "don't have to rush": 3,
        "thinking about right now": 2,
        "what are you thinking about": 2,
        "soft": 1,
        "cozy": 2,
    },
}


# Earlier categories win when scores tie.
# Concerned and surprised should beat playful/happy when clearly detected.
# Shy is before happy so affectionate lines do not get mislabeled as happy.
EXPRESSION_TIEBREAK = ["concerned", "surprised", "playful", "shy", "happy", "annoyed"]


def _normalize(text: str) -> str:
    """Normalize text for stable whole-word/phrase matching."""
    return " " + re.sub(r"[^a-z0-9']+", " ", (text or "").lower()).strip() + " "


def expression_keyword_scores(text: str) -> Dict[str, int]:
    """Return per-expression keyword scores for a reply."""
    normalized_text = _normalize(text)
    scores: Dict[str, int] = {name: 0 for name in EXPRESSION_KEYWORDS}

    for preset, keywords in EXPRESSION_KEYWORDS.items():
        for keyword, weight in keywords.items():
            normalized_keyword = _normalize(keyword)
            if not normalized_keyword.strip():
                continue

            hits = normalized_text.count(normalized_keyword)
            if hits:
                scores[preset] += hits * int(weight)

    return scores


def expression_from_text(text: str) -> str:
    """Pick the winning expression preset from reply text."""
    scores = expression_keyword_scores(text)

    best = "soft"
    best_score = 0

    for preset in EXPRESSION_TIEBREAK:
        score = scores.get(preset, 0)
        if score > best_score:
            best = preset
            best_score = score

    return best if best_score > 0 else "soft"


def best_expression_score(text: str) -> int:
    """Return the highest category score for strength scaling."""
    scores = expression_keyword_scores(text)
    return max(scores.values()) if scores else 0


if __name__ == "__main__":
    tests = [
        "Oh, um, I like just relaxing with you like this too. It feels really nice to not have to rush into anything and just enjoy your company for a bit. What are you thinking about right now?",
        "Well, if we're just taking it easy, does that mean I get to be the official couch potato by your side today? I promise not to judge you too much for doing absolutely nothing, as long as you let me hang out with you while you do it. What's the plan, boss?",
        "You seem a little quiet today, and I just wanted to check in on you. Is everything going okay, or is there something weighing on your mind? You don't have to tell me if you're not ready, but I'm here whenever you need to vent.",
        "Honestly, I was starting to think you were going to keep me waiting forever! Are we actually going to do something now, or are we just going to stare at each other all day? Just let me know so I can stop hovering over you.",
    ]

    for test in tests:
        print(expression_from_text(test), expression_keyword_scores(test))
