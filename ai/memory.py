from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from config.settings import PROJECT_ROOT, get_settings

DEFAULT_MEMORY_FILE = PROJECT_ROOT / "data" / "memories.json"
# Backward-compatible name for external scripts. Runtime operations use the
# configured path returned by ``_memory_file``.
MEMORY_FILE = DEFAULT_MEMORY_FILE
MEMORY_SCHEMA_VERSION = 2
FACT_EXTRACTION_VERSION = 2


def _memory_file() -> Path:
    configured = Path(str(get_settings().memory.file)).expanduser()
    if not configured.is_absolute():
        configured = PROJECT_ROOT / configured
    return configured.resolve()

STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "to", "of", "in",
    "on", "for", "with", "without", "is", "are", "was", "were", "be", "been", "being",
    "i", "me", "my", "mine", "you", "your", "yours", "it", "its", "this", "that", "these",
    "those", "do", "does", "did", "so", "just", "like", "what", "when", "where", "why",
    "how", "can", "could", "would", "should", "will", "about", "last", "thing", "said",
}


_TRANSIENT_IDENTITY_PREFIXES = (
    "tired",
    "sleepy",
    "hungry",
    "thirsty",
    "sick",
    "busy",
    "bored",
    "ready",
    "testing",
    "cold",
    "hot",
    "upset",
    "sad",
    "happy",
    "angry",
    "fine",
    "okay",
    "ok",
)

_META_OR_UNCERTAIN_PREFIXES = (
    "sorry",
    "maybe",
    "perhaps",
    "i think",
    "i guess",
    "i suppose",
    "i don't know",
    "i do not know",
    "i'm not sure",
    "i am not sure",
    "just testing",
    "i'm testing",
    "i am testing",
    "i was just testing",
)

_META_NEGATION_PATTERNS = (
    r"\b(?:it(?:'s| is))\s+not\s+like\s+i\b",
    r"\bnot\s+that\s+i\b",
    r"\bi\s+(?:wouldn't|would not)\s+say\s+i\b",
    r"\bi\s+(?:don't|do not)\s+mean\s+that\s+i\b",
)


@dataclass(frozen=True)
class FactCandidate:
    text: str
    kind: str
    confidence: float
    source_text: str
    explicit: bool = False


def _trim_to_limit(items: list[Any], limit: int) -> list[Any]:
    limit = max(0, int(limit))
    return items[-limit:] if limit else []


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _blank_memory() -> dict[str, Any]:
    return {
        "schema_version": MEMORY_SCHEMA_VERSION,
        "facts": [],
        "turns": [],
    }


def _unique_backup_path(memory_file: Path, label: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate = memory_file.with_name(
        f"{memory_file.stem}.{label}-{timestamp}{memory_file.suffix}"
    )
    counter = 2
    while candidate.exists():
        candidate = memory_file.with_name(
            f"{memory_file.stem}.{label}-{timestamp}-{counter}{memory_file.suffix}"
        )
        counter += 1
    return candidate


def _migrate_memory(memory: dict[str, Any], memory_file: Path) -> bool:
    """Migrate old facts without silently trusting legacy auto extraction."""

    changed = False
    version = memory.get("schema_version", 1)
    if not isinstance(version, int):
        version = 1

    facts = memory.setdefault("facts", [])
    memory.setdefault("turns", [])

    legacy_auto_facts = [
        fact
        for fact in facts
        if isinstance(fact, dict)
        and fact.get("source") == "auto"
        and not fact.get("extraction_version")
    ]

    if version < MEMORY_SCHEMA_VERSION and legacy_auto_facts:
        backup = _unique_backup_path(memory_file, "pre-memory-safety")
        try:
            shutil.copy2(memory_file, backup)
            print(f"Memory safety backup created: {backup}")
        except OSError:
            pass

    for fact in facts:
        if not isinstance(fact, dict):
            continue

        if fact in legacy_auto_facts:
            fact["status"] = "review"
            fact["review_reason"] = "legacy_unsafe_auto_extraction"
            fact.setdefault("kind", "unknown")
            fact.setdefault("confidence", 0.0)
            changed = True
        else:
            if "status" not in fact:
                fact["status"] = "active"
                changed = True
            if "kind" not in fact:
                fact["kind"] = (
                    "manual" if fact.get("source") == "manual" else "general"
                )
                changed = True
            if "confidence" not in fact:
                fact["confidence"] = (
                    1.0 if fact.get("source") == "manual" else 0.7
                )
                changed = True

    if version != MEMORY_SCHEMA_VERSION:
        memory["schema_version"] = MEMORY_SCHEMA_VERSION
        changed = True

    return changed


def load_memory() -> dict[str, Any]:
    memory_file = _memory_file()
    memory_file.parent.mkdir(parents=True, exist_ok=True)

    if not memory_file.exists():
        memory = _blank_memory()
        save_memory(memory)
        return memory

    try:
        with memory_file.open("r", encoding="utf-8") as f:
            memory = json.load(f)
    except (json.JSONDecodeError, OSError):
        backup = memory_file.with_suffix(
            f".broken-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        )
        try:
            memory_file.rename(backup)
            print(f"Memory file was corrupted. Backed it up to: {backup}")
        except OSError:
            pass
        memory = _blank_memory()
        save_memory(memory)
        return memory

    if not isinstance(memory, dict):
        memory = _blank_memory()
        save_memory(memory)
        return memory

    if _migrate_memory(memory, memory_file):
        save_memory(memory)

    return memory


def save_memory(memory: dict[str, Any]) -> None:
    memory_file = _memory_file()
    memory_file.parent.mkdir(parents=True, exist_ok=True)
    memory["schema_version"] = MEMORY_SCHEMA_VERSION
    memory.setdefault("facts", [])
    memory.setdefault("turns", [])
    with memory_file.open("w", encoding="utf-8") as f:
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


def _fact_entry(
    text: str,
    *,
    source: str,
    kind: str,
    confidence: float,
    source_text: str | None = None,
) -> dict[str, Any]:
    now = _now()
    entry: dict[str, Any] = {
        "text": text,
        "source": source,
        "kind": kind,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 2),
        "status": "active",
        "created_at": now,
        "last_seen": now,
        "times_seen": 1,
    }
    if source == "auto":
        entry["extraction_version"] = FACT_EXTRACTION_VERSION
    if source_text:
        entry["source_text"] = source_text
    return entry


def _store_fact(
    memory: dict[str, Any],
    fact_text: str,
    *,
    source: str,
    kind: str,
    confidence: float,
    source_text: str | None = None,
) -> bool:
    normalized = _normalize(fact_text)
    for fact in memory.get("facts", []):
        if not isinstance(fact, dict):
            continue
        if _normalize(fact.get("text", "")) != normalized:
            continue

        fact["last_seen"] = _now()
        fact["times_seen"] = int(fact.get("times_seen", 1)) + 1
        fact.update(
            {
                "source": source,
                "kind": kind,
                "confidence": round(
                    max(0.0, min(1.0, float(confidence))), 2
                ),
                "status": "active",
            }
        )
        fact.pop("review_reason", None)
        fact.pop("forgotten_at", None)
        if source == "auto":
            fact["extraction_version"] = FACT_EXTRACTION_VERSION
        if source_text:
            fact["source_text"] = source_text
        return False

    memory.setdefault("facts", []).append(
        _fact_entry(
            fact_text,
            source=source,
            kind=kind,
            confidence=confidence,
            source_text=source_text,
        )
    )
    return True


def _dedupe_fact(memory: dict[str, Any], fact_text: str) -> bool:
    """Backward-compatible exact dedupe helper."""

    normalized = _normalize(fact_text)
    for fact in memory.get("facts", []):
        if (
            isinstance(fact, dict)
            and _normalize(fact.get("text", "")) == normalized
        ):
            fact["last_seen"] = _now()
            fact["times_seen"] = int(fact.get("times_seen", 1)) + 1
            return True
    return False


def add_fact(text: str, source: str = "manual") -> None:
    """Store a long-term fact/preference about the user or Akira's setup."""

    text = _clean_fact_text(text)
    if not text:
        return

    memory = load_memory()
    _store_fact(
        memory,
        text,
        source=source,
        kind="manual" if source == "manual" else "general",
        confidence=1.0 if source == "manual" else 0.8,
        source_text=text,
    )
    memory["facts"] = _trim_to_limit(
        memory["facts"], get_settings().memory.max_facts
    )
    save_memory(memory)


def _split_statements(text: str) -> list[str]:
    return [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|[\r\n]+", text.strip())
        if part.strip()
    ]


def _clean_fact_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip(" \t\r\n.,;:!?")
    return cleaned


def _contains_meta_negation(text: str) -> bool:
    return any(
        re.search(pattern, text, flags=re.IGNORECASE)
        for pattern in _META_NEGATION_PATTERNS
    )


def _is_transient_identity(statement: str) -> bool:
    match = re.match(
        r"^(?:i am|i'm)\s+(.+)$", statement, flags=re.IGNORECASE
    )
    if not match:
        return False
    complement = match.group(1).strip().lower()
    return complement.startswith(_TRANSIENT_IDENTITY_PREFIXES)


def _normalize_codeword(statement: str) -> str | None:
    match = re.match(
        r"^(?:the\s+)?(?:code\s*word|codeword|safe\s+word|keyword)"
        r"\s*(?:is|=|:)?\s+(.+)$",
        statement,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    value = _clean_fact_text(match.group(1))
    if not value:
        return None
    return f"The codeword is {value}"


def _infer_kind(statement: str) -> str:
    lowered = statement.lower()
    if _normalize_codeword(statement):
        return "reference"
    if re.match(
        r"^(?:i\s+(?:like|love|hate|prefer|enjoy|dislike)|"
        r"i\s+(?:don't|do not)\s+like)\b",
        lowered,
    ):
        return "preference"
    if re.match(
        r"^(?:i\s+(?:want|plan|hope|intend|need)\s+to|my\s+goal\s+is)\b",
        lowered,
    ):
        return "goal"
    if re.match(
        r"^(?:i\s+(?:use|own|have|run|work on|am working on)|"
        r"my\s+.+\s+(?:is|are))\b",
        lowered,
    ):
        return "setup"
    if re.match(
        r"^(?:i am|i'm|call me|my name is|i live in|i'm from|i am from)\b",
        lowered,
    ):
        return "identity"
    if lowered.startswith("akira "):
        return "akira"
    return "general"


def _explicit_memory_payload(statement: str) -> str | None:
    match = re.match(
        r"^(?:please\s+)?(?:remember|keep\s+in\s+mind)"
        r"(?:\s+that)?\s*[:,-]?\s*(.+)$",
        statement,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.match(
            r"^(?:please\s+)?(?:don't|do\s+not)\s+forget"
            r"(?:\s+that)?\s*[:,-]?\s*(.+)$",
            statement,
            flags=re.IGNORECASE,
        )
    if not match:
        return None
    return _clean_fact_text(match.group(1))


def _automatic_candidate(statement: str) -> FactCandidate | None:
    cleaned = _clean_fact_text(statement)
    lowered = cleaned.lower()
    if not cleaned or len(cleaned) < 8 or len(cleaned) > 220:
        return None
    if "?" in statement:
        return None
    if lowered.startswith(_META_OR_UNCERTAIN_PREFIXES):
        return None
    if _contains_meta_negation(cleaned):
        return None
    if _is_transient_identity(cleaned):
        return None

    codeword = _normalize_codeword(cleaned)
    if codeword:
        return FactCandidate(
            text=codeword,
            kind="reference",
            confidence=0.95,
            source_text=cleaned,
        )

    stable_patterns = (
        r"^(?:my|our)\s+[^.!?]{1,80}\s+(?:is|are|was|were)\s+[^.!?]{1,140}$",
        r"^call\s+me\s+[^.!?]{1,80}$",
        r"^(?:i\s+am|i'm)\s+[^.!?]{1,160}$",
        r"^i\s+(?:like|love|hate|prefer|enjoy|dislike|use|own|have|run|"
        r"usually|always|never|study|major\s+in|live\s+in|work\s+on|"
        r"am\s+working\s+on|want\s+to|plan\s+to|hope\s+to|"
        r"intend\s+to|need\s+to)\s+[^.!?]{1,180}$",
        r"^i\s+(?:don't|do\s+not)\s+(?:like|want|use|have|prefer|enjoy)"
        r"\s+[^.!?]{1,180}$",
        r"^akira\s+(?:is|should|will|can|uses|has|needs)\s+[^.!?]{1,180}$",
    )
    if not any(
        re.match(pattern, cleaned, flags=re.IGNORECASE)
        for pattern in stable_patterns
    ):
        return None

    return FactCandidate(
        text=cleaned,
        kind=_infer_kind(cleaned),
        confidence=0.78,
        source_text=cleaned,
    )


def _extract_fact_records(user_text: str) -> list[FactCandidate]:
    """Extract conservative, auditable memory candidates."""

    records: list[FactCandidate] = []
    seen: set[str] = set()

    for statement in _split_statements(user_text):
        explicit = _explicit_memory_payload(statement)
        if explicit:
            normalized_codeword = _normalize_codeword(explicit)
            fact_text = normalized_codeword or explicit
            if 8 <= len(fact_text) <= 220:
                key = _normalize(fact_text)
                if key not in seen:
                    seen.add(key)
                    records.append(
                        FactCandidate(
                            text=fact_text,
                            kind=_infer_kind(fact_text),
                            confidence=1.0,
                            source_text=_clean_fact_text(statement),
                            explicit=True,
                        )
                    )
            continue

        candidate = _automatic_candidate(statement)
        if candidate:
            key = _normalize(candidate.text)
            if key not in seen:
                seen.add(key)
                records.append(candidate)

    return records


def _extract_fact_candidates(user_text: str) -> list[str]:
    """Backward-compatible text-only view of safe extraction candidates."""

    return [candidate.text for candidate in _extract_fact_records(user_text)]


def remember_turn(user_text: str, assistant_text: str) -> None:
    """Persist a completed turn and promote only safe memory candidates."""

    user_text = user_text.strip()
    assistant_text = assistant_text.strip()
    if not user_text and not assistant_text:
        return

    memory = load_memory()
    memory["turns"].append(
        {
            "time": _now(),
            "user": user_text,
            "assistant": assistant_text,
        }
    )
    memory["turns"] = _trim_to_limit(
        memory["turns"], get_settings().memory.max_turns
    )

    for candidate in _extract_fact_records(user_text):
        _store_fact(
            memory,
            candidate.text,
            source="auto",
            kind=candidate.kind,
            confidence=candidate.confidence,
            source_text=candidate.source_text,
        )

    memory["facts"] = _trim_to_limit(
        memory["facts"], get_settings().memory.max_facts
    )
    save_memory(memory)


def build_memory_context(
    user_text: str,
    recent_turns: int | None = None,
    relevant_limit: int | None = None,
) -> str:
    """Return a compact memory block to inject into the LLM prompt."""
    settings = get_settings().memory
    recent_turns = max(0, int(settings.recent_turns if recent_turns is None else recent_turns))
    relevant_limit = max(0, int(settings.relevant_limit if relevant_limit is None else relevant_limit))
    memory = load_memory()
    facts = [
        fact
        for fact in memory.get("facts", [])
        if isinstance(fact, dict) and fact.get("status", "active") == "active"
    ]
    turns = memory.get("turns", [])

    relevant_facts = sorted(
        facts,
        key=lambda fact: (
            _score(user_text, fact.get("text", "")),
            float(fact.get("confidence", 0.0)),
            fact.get("last_seen", ""),
        ),
        reverse=True,
    )[:relevant_limit]

    # Recent turns help with persistent recall after restarting the script.
    recent = turns[-recent_turns:] if recent_turns else []

    # Relevant older turns help when the user asks about a specific topic from earlier.
    older_turns = turns[:-recent_turns] if recent_turns else turns
    relevant_old = sorted(
        [
            turn
            for turn in older_turns
            if isinstance(turn, dict)
            and _score(user_text, turn.get("user", "")) > 0
        ],
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
    max_chars = max(0, int(settings.max_context_chars))
    if max_chars and len(context) > max_chars:
        context = context[-max_chars:]
    elif max_chars == 0:
        context = ""

    return context


def clear_memory() -> None:
    save_memory(_blank_memory())
