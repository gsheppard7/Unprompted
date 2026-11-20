"""Retrieve Brain memories for a query.

SQLite FTS5 is the current index. Callers should keep using `retrieve_memories`
if a vector index is added later; the signature is the stable boundary.
"""

from __future__ import annotations

import re

from app.brain import store
from app.brain.textutil import tokens

_STOP = {
    "the", "and", "for", "with", "that", "this", "was", "were", "are", "from",
    "have", "has", "you", "your", "about", "what", "when", "where", "who", "how",
    "why", "can", "could", "would", "should", "into", "over", "just", "than",
    "then", "but", "not", "of", "to", "in", "on", "it", "is", "or", "as", "at",
    "by", "be", "if", "so", "do", "did", "does", "a", "an", "user",
}


def _match_query(query: str) -> str:
    words = [
        word
        for word in re.findall(r"[a-z0-9]+", query.lower())
        if len(word) > 2 and word not in _STOP
    ][:8]
    if not words:
        return ""
    return " AND ".join(f'"{word}"' for word in words)


def _overlap(query: str, brain_id: str, status: str | None, limit: int) -> list[dict]:
    wanted = {word for word in tokens(query) if len(word) > 2 and word not in _STOP}
    if not wanted:
        return []
    pool = store.list_memories(brain_id, status=status, limit=500)
    ranked: list[tuple[int, dict]] = []
    for memory in pool:
        shared = wanted & tokens(f"{memory['content']} {memory['category']}")
        if shared:
            ranked.append((len(shared), memory))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [memory for _score, memory in ranked[:limit]]


def retrieve_memories(
    brain_id: str,
    query: str,
    limit: int = 8,
    *,
    status: str | None = "accepted",
) -> list[dict]:
    """Return memories with source and stance metadata. Empty query returns nothing."""
    cleaned = query.strip()
    if not cleaned:
        return []
    limit = max(1, min(int(limit), 50))
    match = _match_query(cleaned)
    if match:
        ids = store.search_memory_ids(brain_id, match, status, limit)
        if ids:
            found: list[dict] = []
            for memory_id in ids:
                memory = store.get_memory(memory_id)
                if memory is None:
                    continue
                if status is not None and memory["status"] != status:
                    continue
                found.append(memory)
            if found:
                return found[:limit]
    return _overlap(cleaned, brain_id, status, limit)
