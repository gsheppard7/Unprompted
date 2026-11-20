"""Small text helpers shared by extraction, storage, and chat hints."""

from __future__ import annotations

import re

_SELF = re.compile(
    r"\b(i'm a|i am a|i'm an|i am an|i study|i'm studying|i am studying|"
    r"i enjoy|i like|i love|i work on|i'm working|i am working|my goal|"
    r"i want to|i hope to|i prefer|i'm interested|i am interested|interested in|"
    r"i'm skilled|i am skilled|i'm good at|i am good at)\b",
    re.IGNORECASE,
)


def normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def tokens(text: str) -> set[str]:
    return set(normalize(text).split())


def same_memory(left: str, right: str) -> bool:
    """True when two memory strings are the same claim, not merely related."""
    a = tokens(left)
    b = tokens(right)
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 4 and shorter <= longer and len(shorter) / len(longer) >= 0.75:
        return True
    return len(a & b) / len(a | b) >= 0.82


def sentence(text: str) -> str:
    cleaned = " ".join(text.split()).strip()
    if not cleaned:
        return ""
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def clip(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def title_phrase(text: str) -> str:
    cleaned = re.sub(r"^(?:a|an|the)\s+", "", " ".join(text.split()), flags=re.IGNORECASE)
    words: list[str] = []
    for word in cleaned.strip(" .").split():
        if word.isupper() or any(char.isupper() for char in word[1:]):
            words.append(word)
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)


def canon_entity(name: str) -> str:
    cleaned = " ".join(name.split()).strip(" .\"'")
    if cleaned.lower() in {"user", "the user", "i", "me", "myself"}:
        return "User"
    return title_phrase(cleaned)


def looks_like_self_knowledge(text: str) -> bool:
    """Cheap hint that a chat line might be worth offering to the Brain."""
    if len(text) > 2000:
        return False
    return bool(_SELF.search(text))
