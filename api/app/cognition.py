"""Heuristic candidate thoughts.

This is the stand-in for a learned attention policy. It only treats a saved,
verified note as evidence. It does not pretend to have checked a live source.
"""

from __future__ import annotations

import re

from app.policy import Proposal

STOP = {
    "the", "and", "for", "with", "that", "this", "was", "were", "are", "been",
    "from", "have", "has", "his", "her", "their", "they", "you", "your", "about",
    "just", "than", "then", "into", "over", "last", "year", "season", "per",
    "game", "but", "not", "what", "when", "where", "who", "how", "why", "can",
    "could", "would", "should", "of", "to", "in", "on", "it", "he", "she", "we",
    "is", "or", "as", "at", "by", "be", "if", "so", "do", "did", "does", "a", "an",
}

WORD_NUMS = {
    "zero": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
    "fifteen": "15",
    "twenty": "20",
}

REMEMBER = re.compile(r"^\s*remember that\s+(.+?)\s*$", re.IGNORECASE | re.DOTALL)
ADDRESS = re.compile(
    r"^(please|hey|hi|hello|can you|could you|would you|will you|tell me|explain|"
    r"help|what|why|how|who|when|where|did you|do you|show me)\b",
    re.IGNORECASE,
)
REFERS_BACK = re.compile(
    r"\b(he|she|they|him|her|his|hers|no way|barely|wrong|incorrect|i disagree)\b",
    re.IGNORECASE,
)
DISAGREEMENT = re.compile(
    r"\b(no way|that's wrong|that is wrong|you're wrong|you are wrong|incorrect|"
    r"i disagree|no chance|barely|hardly|nah)\b",
    re.IGNORECASE,
)
OPEN_QUESTION = re.compile(
    r"(\?|\b(i wonder|not sure|do you know|who led|what did|how many)\b)",
    re.IGNORECASE,
)


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9']+", text.lower())
    return {word for word in words if len(word) > 2 and word not in STOP and not word.isdigit()}


def proper_tokens(text: str) -> set[str]:
    return {match.group().lower() for match in re.finditer(r"\b[A-Z][a-z]{2,}\b", text)}


def normalize_number_words(text: str) -> str:
    return re.sub(
        r"\b(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)\b",
        lambda match: WORD_NUMS[match.group().lower()],
        text,
        flags=re.IGNORECASE,
    )


def extract_numbers(text: str) -> list[float]:
    masked = normalize_number_words(text)
    masked = re.sub(r"\b(?:19|20)\d{2}\s*[-–/]\s*\d{2,4}\b", " ", masked)
    values: list[float] = []
    for match in re.finditer(r"\d+(?:\.\d+)?", masked):
        value = float(match.group())
        if match.group().isdigit() and 1900 <= value <= 2100:
            continue
        values.append(value)
    return values


def sentence(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return cleaned
    if cleaned[-1] not in ".!?":
        cleaned += "."
    return cleaned


def is_addressed(text: str) -> bool:
    stripped = text.strip()
    return "?" in stripped or bool(ADDRESS.match(stripped))


def parse_remember(text: str) -> str | None:
    match = REMEMBER.match(text.strip())
    if not match:
        return None
    content = sentence(match.group(1))
    return content or None


def refers_back(text: str) -> bool:
    return bool(REFERS_BACK.search(text))


def user_texts(messages: list[dict]) -> list[str]:
    return [message["text"] for message in messages if message.get("role") == "user"]


def focus_text(messages: list[dict]) -> str:
    texts = user_texts(messages)
    if not texts:
        return ""
    latest = texts[-1]
    if len(texts) >= 2 and refers_back(latest):
        return texts[-2] + "\n" + latest
    return latest


def overlap_ok(memory: dict, text: str) -> bool:
    text_tokens = tokens(text)
    if memory.get("brain"):
        # Brain notes are written as "User ...", so a person's name is not the overlap signal.
        content_tokens = tokens(memory.get("content", "")) - {"user"}
        return bool(content_tokens & text_tokens)
    proper = proper_tokens(memory.get("content", ""))
    if proper:
        return bool(proper & text_tokens)
    return len(tokens(memory.get("content", "")) & text_tokens) >= 3


def best_memory(text: str, memories: list[dict]) -> dict | None:
    ranked: list[tuple[int, dict]] = []
    for memory in memories:
        if memory.get("kind", "fact") != "fact":
            continue
        if not overlap_ok(memory, text):
            continue
        shared = len(tokens(memory.get("content", "")) & tokens(text))
        score = shared + (2 if memory.get("verified") else 0)
        ranked.append((score, memory))
    if not ranked:
        return None
    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def conflicting_pair(user_nums: list[float], memory_nums: list[float]) -> tuple[float, float] | None:
    best: tuple[float, float, float] | None = None
    for user_value in user_nums:
        for memory_value in memory_nums:
            scale = max(abs(user_value), abs(memory_value))
            if scale < 1e-9:
                continue
            gap = abs(user_value - memory_value) / scale
            if gap > 0.2 and (best is None or gap < best[0]):
                best = (gap, user_value, memory_value)
    if best is None:
        return None
    return best[1], best[2]


def novelty_score(draft: str, prior_texts: list[str]) -> float:
    draft_tokens = tokens(draft)
    if not draft_tokens:
        return 0.2
    for prior in prior_texts:
        prior_tokens = tokens(prior)
        if not prior_tokens:
            continue
        overlap = len(draft_tokens & prior_tokens) / len(draft_tokens | prior_tokens)
        if overlap > 0.55:
            return 0.08
    return 0.86


def _fmt_num(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _proposal(
    *,
    kind: str,
    draft: str,
    relevance: float,
    evidence: float,
    novelty: float,
    urgency: float,
    interruption_cost: float,
    reason: str,
    memory_ids: list[str] | None = None,
) -> Proposal:
    return Proposal(
        kind=kind,
        draft=draft,
        relevance=relevance,
        evidence=evidence,
        novelty=novelty,
        urgency=urgency,
        interruption_cost=interruption_cost,
        reason=reason,
        memory_ids=memory_ids or [],
    )


def _quiet(reason: str, kind: str = "none") -> Proposal:
    return _proposal(
        kind=kind,
        draft="",
        relevance=0.12,
        evidence=0.05,
        novelty=0.2,
        urgency=0.05,
        interruption_cost=0.8,
        reason=reason,
    )


def assistant_spoke_after_last_user(messages: list[dict]) -> bool:
    last_user = -1
    for index, message in enumerate(messages):
        if message.get("role") == "user":
            last_user = index
    if last_user < 0:
        return False
    return any(message.get("role") == "assistant" for message in messages[last_user + 1 :])


def propose(event_type: str, messages: list[dict], memories: list[dict]) -> Proposal:
    texts = user_texts(messages)
    if not texts:
        return _quiet("Nothing has been said yet.")

    focus = focus_text(messages)
    prior = [message["text"] for message in messages if message.get("initiator") == "proactive"]
    memory = best_memory(focus, memories)

    if event_type == "session.idle":
        if assistant_spoke_after_last_user(messages):
            return _quiet("Already responded to the latest turn.")
        if (
            memory
            and memory.get("verified")
            and OPEN_QUESTION.search(texts[-1])
            and overlap_ok(memory, focus)
        ):
            source = memory.get("source_label") or "untitled"
            draft = f"Coming back to this — a saved reference ({source}) says {sentence(memory['content'])}"
            return _proposal(
                kind="follow_up",
                draft=draft,
                relevance=0.84,
                evidence=0.9,
                novelty=novelty_score(draft, prior),
                urgency=0.55,
                interruption_cost=0.32,
                reason="The conversation went quiet, and a saved reference speaks to an open question.",
                memory_ids=[memory["id"]] if memory.get("id") else [],
            )

    user_numbers = extract_numbers(focus)
    if memory and user_numbers:
        pair = conflicting_pair(user_numbers, extract_numbers(memory.get("content", "")))
        if pair and overlap_ok(memory, focus):
            verified = bool(memory.get("verified"))
            source = memory.get("source_label") or "untitled"
            draft = (
                f"Small correction: a saved reference ({source}) says {sentence(memory['content'])} "
                f"That conflicts with {_fmt_num(pair[0])} in the conversation. "
                "I haven't checked a live source."
            )
            reason = (
                "A saved reference conflicts with a number just mentioned."
                if verified
                else "A number conflicts with an unverified note, so this stays quiet."
            )
            return _proposal(
                kind="correction",
                draft=draft,
                relevance=0.92,
                evidence=0.95 if verified else 0.35,
                novelty=novelty_score(draft, prior),
                urgency=0.72,
                interruption_cost=0.38,
                reason=reason,
                memory_ids=[memory["id"]] if memory.get("id") else [],
            )

    if DISAGREEMENT.search(texts[-1]):
        draft = "There's a disagreement here, but I don't have a verified reference to weigh in."
        return _proposal(
            kind="clarification",
            draft=draft,
            relevance=0.7,
            evidence=0.15,
            novelty=novelty_score(draft, prior),
            urgency=0.4,
            interruption_cost=0.62,
            reason="Disagreement without a verified reference. Staying quiet.",
        )

    if memory and overlap_ok(memory, focus):
        return _quiet("Related memory, but nothing to correct or answer.", kind="connection")

    return _quiet("Low expected value. Staying silent.")


def direct_reply(messages: list[dict], memories: list[dict], mode: str) -> str:
    """Local answer used when no model key is configured, or the model call fails."""
    latest = user_texts(messages)[-1] if user_texts(messages) else ""
    if re.match(r"(hi|hello|hey)\b", latest.strip(), re.IGNORECASE) and len(latest) < 24:
        if mode == "off":
            return "Hello. Proactivity is off, so I'll only respond when you address me."
        return "Hello. Ask a question if you want an answer now, or leave a claim and I'll decide whether to weigh in."

    focus = focus_text(messages)
    memory = best_memory(focus, memories)
    grounded_ask = bool(is_addressed(latest) or extract_numbers(focus) or DISAGREEMENT.search(latest))
    if memory and memory.get("verified") and grounded_ask:
        source = memory.get("source_label") or "untitled"
        return f"From a saved reference ({source}): {sentence(memory['content'])}"
    if memory and memory.get("brain") and not memory.get("verified") and is_addressed(latest):
        source = memory.get("source_label") or "Brain"
        return (
            f"The Brain has an unconfirmed inference ({source}): {sentence(memory['content'])} "
            "It is not a verified fact."
        )
    if is_addressed(latest):
        return (
            "I don't have a verified memory for this, and I won't invent a source. "
            "You can add one in Memory, or connect a model key for a fuller answer."
        )
    if mode == "off":
        return "Noted. Proactivity is off, so I won't bring this up on my own."
    return "I don't have a verified memory for this, and I won't invent a source."
