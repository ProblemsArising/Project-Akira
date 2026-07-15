from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

# Project root is AIWaifu/, because this file is AIWaifu/ai/memory.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
MEMORY_FILE = DATA_DIR / "memories.json"

MAX_TURNS = 300
MAX_FACTS = 200
MAX_MEMORY_CHARS = 2500

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "to", "of", "in",
    "on", "for", "with", "without", "is", "are", "was", "were", "be", "been", "being",
    "i", "me", "my", "mine", "you", "your", "yours", "it", "its", "this", "that", "these",
    "those", "do", "does", "did", "so", "just", "like", "what", "when", "where", "why",
    "how", "can", "could", "would", "should", "will", "about", "last", "thing", "said",
}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _blank_memory() -> dict[str, Any]:
    return {
        "facts": [],
        "turns": [],
    }


def load_memory() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not MEMORY_FILE.exists():
        memory = _blank_memory()
        save_memory(memory)
        return memory

    try:
        with MEMORY_FILE.open("r", encoding="utf-8") as f:
            memory = json.load(f)
    except (json.JSONDecodeError, OSError):
        # If the file gets corrupted, keep a backup instead of destroying it.
        backup = MEMORY_FILE.with_suffix(f".broken-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
        try:
            MEMORY_FILE.rename(backup)
            print(f"⚠️ Memory file was corrupted. Backed it up to: {backup}")
        except OSError:
            pass
        memory = _blank_memory()
        save_memory(memory)

    memory.setdefault("facts", [])
    memory.setdefault("turns", [])
    return memory


def save_memory(memory: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with MEMORY_FILE.open("w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, ensure_ascii=False)


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z0-9_']+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in STOPWORDS}


def _score(query: str, text: str) -> int:
    query_words = _keywords(query)
    text_words = _keywords(text)
    if not query_words or not text_words:
        return 0
    return len(query_words & text_words)


def _dedupe_fact(memory: dict[str, Any], fact_text: str) -> bool:
    normalized = _normalize(fact_text)
    for fact in memory.get("facts", []):
        if _normalize(fact.get("text", "")) == normalized:
            fact["last_seen"] = _now()
            fact["times_seen"] = int(fact.get("times_seen", 1)) + 1
            return True
    return False


def add_fact(text: str, source: str = "manual") -> None:
    """Store a long-term fact/preference about the user or Akira's setup."""
    text = text.strip()
    if not text:
        return

    memory = load_memory()
    if not _dedupe_fact(memory, text):
        memory["facts"].append({
            "text": text,
            "source": source,
            "created_at": _now(),
            "last_seen": _now(),
            "times_seen": 1,
        })

    memory["facts"] = memory["facts"][-MAX_FACTS:]
    save_memory(memory)


def _extract_fact_candidates(user_text: str) -> list[str]:
    """
    Lightweight memory extraction without a second LLM call.

    This intentionally stores only obvious preference/identity/setup statements.
    The full conversation is still saved in turns, so recall can work even when
    nothing is promoted to facts.
    """
    text = user_text.strip()
    lowered = text.lower()
    candidates: list[str] = []

    remember_match = re.search(r"\bremember(?: that)?\b[: ,]*(.+)", text, flags=re.IGNORECASE)
    if remember_match:
        candidates.append(remember_match.group(1).strip())

    patterns = [
        r"\bmy\s+[^.!?]{1,80}\s+(?:is|are|was|were)\s+[^.!?]{1,140}",
        r"\bi\s+(?:am|i'm|like|love|hate|prefer|want|need|have|use|usually|always|never)\s+[^.!?]{1,160}",
        r"\bcall me\s+[^.!?]{1,80}",
        r"\bher name is\s+[^.!?]{1,80}",
        r"\bakira\s+(?:is|should|will|can|uses|has)\s+[^.!?]{1,160}",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            candidate = match.group(0).strip()
            if len(candidate) >= 12:
                candidates.append(candidate)

    # Avoid storing throwaway questions as facts unless the user explicitly said remember.
    if "?" in text and not lowered.startswith("remember"):
        candidates = [c for c in candidates if c.lower().startswith("remember")]

    # Keep stable, not gigantic facts.
    cleaned: list[str] = []
    seen = set()
    for candidate in candidates:
        candidate = re.sub(r"\s+", " ", candidate).strip(" .")
        if 8 <= len(candidate) <= 220:
            key = _normalize(candidate)
            if key not in seen:
                seen.add(key)
                cleaned.append(candidate)

    return cleaned


def remember_turn(user_text: str, assistant_text: str) -> None:
    """Persist a completed user/assistant turn and promote obvious facts."""
    user_text = user_text.strip()
    assistant_text = assistant_text.strip()
    if not user_text and not assistant_text:
        return

    memory = load_memory()
    memory["turns"].append({
        "time": _now(),
        "user": user_text,
        "assistant": assistant_text,
    })
    memory["turns"] = memory["turns"][-MAX_TURNS:]

    for fact_text in _extract_fact_candidates(user_text):
        if not _dedupe_fact(memory, fact_text):
            memory["facts"].append({
                "text": fact_text,
                "source": "auto",
                "created_at": _now(),
                "last_seen": _now(),
                "times_seen": 1,
            })

    memory["facts"] = memory["facts"][-MAX_FACTS:]
    save_memory(memory)


def build_memory_context(user_text: str, recent_turns: int = 6, relevant_limit: int = 6) -> str:
    """Return a compact memory block to inject into the LLM prompt."""
    memory = load_memory()
    facts = memory.get("facts", [])
    turns = memory.get("turns", [])

    relevant_facts = sorted(
        facts,
        key=lambda fact: (_score(user_text, fact.get("text", "")), fact.get("last_seen", "")),
        reverse=True,
    )[:relevant_limit]

    # Recent turns help with persistent recall after restarting the script.
    recent = turns[-recent_turns:]

    # Relevant older turns help when the user asks about a specific topic from earlier.
    recent_ids = {id(turn) for turn in recent}
    relevant_old = sorted(
        [turn for turn in turns[:-recent_turns] if _score(user_text, turn.get("user", "")) > 0],
        key=lambda turn: _score(user_text, turn.get("user", "")),
        reverse=True,
    )[:relevant_limit]

    lines: list[str] = []

    if relevant_facts:
        lines.append("Long-term facts/preferences:")
        for fact in relevant_facts:
            lines.append(f"- {fact.get('text', '')}")

    if relevant_old:
        lines.append("Relevant older conversation snippets:")
        for turn in relevant_old:
            lines.append(f"- User said: {turn.get('user', '')}")
            if turn.get("assistant"):
                lines.append(f"  Akira replied: {turn.get('assistant', '')[:180]}")

    if recent:
        lines.append("Recent persistent conversation history:")
        for turn in recent:
            lines.append(f"- User: {turn.get('user', '')}")
            if turn.get("assistant"):
                lines.append(f"  Akira: {turn.get('assistant', '')[:220]}")

    context = "\n".join(lines).strip()
    if len(context) > MAX_MEMORY_CHARS:
        context = context[-MAX_MEMORY_CHARS:]

    return context


def clear_memory() -> None:
    save_memory(_blank_memory())
