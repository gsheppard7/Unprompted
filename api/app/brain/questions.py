"""Find useful gaps in accepted memories and turn them into questions.

Ranking is deterministic. A model may rephrase the chosen questions, and it
cannot add a question that did not come from a gap.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from app import llm
from app.brain.textutil import normalize

logger = logging.getLogger(__name__)

_BROAD = {
    "robotics",
    "machine learning",
    "artificial intelligence",
    "ai",
    "software engineering",
    "computer science",
    "programming",
    "data science",
    "design",
    "music",
    "writing",
    "research",
    "engineering",
    "biology",
    "physics",
    "math",
    "mathematics",
    "art",
}

_TECHNICAL = _BROAD | {
    "python",
    "computer vision",
    "control systems",
    "embedded systems",
    "web development",
    "compilers",
    "databases",
}

_SENSITIVE = re.compile(
    r"\b(diagnos\w*|illness|mental health|suicid\w*|religion|religious|church|mosque|"
    r"synagogue|sexual|sexuality|gender identity|political party|\bvote\b|salary|income|"
    r"social security|\bssn\b|home address|password|credit card|trauma|abuse|pregnant|"
    r"medication|disability)\b",
    re.IGNORECASE,
)

_REWRITE_SYSTEM = (
    "Rewrite knowledge-gap questions so they sound natural. "
    'Return JSON {"questions":[{"gap_key","question","motivation"}]}. '
    "Keep every gap_key. Do not add questions. Do not ask for medical, financial, "
    "political, religious, or other sensitive details. Each question must still ask "
    "for the same missing information and must end with a question mark."
)


@dataclass
class Gap:
    key: str
    question: str
    motivation: str
    usefulness: float


def is_sensitive(text: str) -> bool:
    return bool(_SENSITIVE.search(text))


def _is_technical(term: str) -> bool:
    lowered = term.lower()
    return any(token in lowered or lowered in token for token in _TECHNICAL)


def _accepted(memories: list[dict]) -> list[dict]:
    return [memory for memory in memories if memory.get("status", "accepted") == "accepted"]


def _terms(memories: list[dict]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for memory in memories:
        content = memory.get("content", "").strip()
        interest = re.fullmatch(r"User is interested in (.+?)\.?", content)
        if interest:
            found.append((interest.group(1), "interest"))
            continue
        enjoy = re.fullmatch(r"User enjoys (.+?)\.?", content)
        if enjoy:
            found.append((enjoy.group(1), "enjoy"))
            continue
        special = re.fullmatch(r"User is especially interested in (.+?) within (.+?)\.?", content)
        if special:
            found.append((special.group(2), "covered"))
    return found


def _covered(memories: list[dict]) -> set[str]:
    covered: set[str] = set()
    for memory in memories:
        match = re.fullmatch(
            r"User is especially interested in .+ within (.+?)\.?",
            memory.get("content", "").strip(),
        )
        if match:
            covered.add(match.group(1).lower())
        if memory.get("content", "").lower().startswith("user did not name a specific aspect of"):
            topic = memory["content"].split("aspect of", 1)[-1].strip(" .")
            covered.add(topic.lower())
    return covered


def _project_known(memories: list[dict], term: str) -> bool:
    needle = term.lower()
    for memory in memories:
        text = memory.get("content", "").lower()
        if memory.get("category") == "projects" and needle in text:
            return True
        if f"not worked on a {needle} project" in text:
            return True
    return False


def _studies(memories: list[dict]) -> list[str]:
    found: list[str] = []
    for memory in memories:
        match = re.fullmatch(r"User studies (.+?)\.?", memory.get("content", "").strip())
        if match:
            found.append(match.group(1))
    return found


def _add(gaps: list[Gap], seen: set[str], gap: Gap) -> None:
    if gap.key in seen or is_sensitive(gap.question) or len(gap.question) > 280:
        return
    seen.add(gap.key)
    gaps.append(gap)


def rank_gaps(memories: list[dict]) -> list[Gap]:
    accepted = _accepted(memories)
    if not accepted:
        return [
            Gap(
                "start:focus",
                "What are you studying, working on, or especially interested in?",
                "The Brain has no accepted memories yet.",
                0.6,
            )
        ]
    gaps: list[Gap] = []
    seen: set[str] = set()
    explicit = [memory for memory in accepted if memory.get("stance") == "explicit"]
    for index, left in enumerate(explicit):
        left_tokens = set(normalize(left["content"]).split())
        left_negative = " not " in f" {left['content'].lower()} "
        for right in explicit[index + 1 :]:
            right_tokens = set(normalize(right["content"]).split())
            if not left_tokens or not right_tokens:
                continue
            overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
            right_negative = " not " in f" {right['content'].lower()} "
            if overlap >= 0.55 and left_negative != right_negative:
                pair = " ".join(sorted((left["id"], right["id"])))
                _add(
                    gaps,
                    seen,
                    Gap(
                        f"conflict:{pair}",
                        "Two accepted notes may conflict. Which one should the Brain keep?",
                        f'One note says "{left["content"]}" and another says "{right["content"]}".',
                        0.91,
                    ),
                )
                break

    for memory in accepted:
        valid_to = memory.get("valid_to") or ""
        if valid_to and memory.get("stance") == "explicit":
            _add(
                gaps,
                seen,
                Gap(
                    f"outdated:{memory['id']}",
                    f"Is this still current: {memory['content'].rstrip('.')}?",
                    "This accepted note has an end date, so it may be out of date.",
                    0.88,
                ),
            )
        if memory.get("stance") == "hypothesis":
            _add(
                gaps,
                seen,
                Gap(
                    f"hypothesis:{memory['id']}",
                    f"Should this stay a hypothesis, or do you want it recorded as something you stated? {memory['content']}",
                    "This note was stored as an uncertain hypothesis.",
                    0.78,
                ),
            )

    covered = _covered(accepted)
    aspect_rank = 0
    project_rank = 0
    hobby_rank = 0
    for term, kind in _terms(accepted):
        key = term.lower()
        if kind == "covered":
            continue
        if key not in covered and key in _BROAD:
            _add(
                gaps,
                seen,
                Gap(
                    f"aspect:{normalize(term)}",
                    f"What aspect of {term} interests you the most?",
                    f"An accepted memory mentions {term} without a more specific focus.",
                    round(0.93 - aspect_rank * 0.03, 2),
                ),
            )
            aspect_rank += 1
        if _is_technical(key) and not _project_known(accepted, term) and project_rank < 2:
            _add(
                gaps,
                seen,
                Gap(
                    f"project:{normalize(term)}",
                    f"Have you worked on any {term} projects outside of coursework?",
                    f"There is an interest in {term} and no accepted project that mentions it.",
                    0.88 if project_rank == 0 else 0.84,
                ),
            )
            project_rank += 1
        elif kind == "enjoy" and not _is_technical(key) and hobby_rank < 2:
            _add(
                gaps,
                seen,
                Gap(
                    f"hobby:{normalize(term)}",
                    f"What do you enjoy most about {term}?",
                    f"Enjoyment of {term} is recorded without any detail.",
                    0.46,
                ),
            )
            hobby_rank += 1

    studies = _studies(accepted)
    has_goals = any(memory.get("category") == "goals" for memory in accepted)
    if studies and not has_goals:
        _add(
            gaps,
            seen,
            Gap(
                "goal:studies",
                f"What are you hoping to do with your {studies[0]} studies?",
                "Education is accepted, and there is no accepted goal yet.",
                0.86,
            ),
        )
    elif not has_goals and any(memory.get("category") == "interests" for memory in accepted):
        _add(
            gaps,
            seen,
            Gap(
                "goal:interests",
                "Is there a longer-term goal that connects the interests already in the Brain?",
                "Interests are accepted, and there is no accepted goal yet.",
                0.8,
            ),
        )

    tool_rank = 0
    for memory in accepted:
        if memory.get("category") != "projects" or tool_rank >= 2:
            continue
        match = re.fullmatch(r"User works on (.+?)\.?", memory.get("content", "").strip())
        if not match or " uses " in memory["content"].lower():
            continue
        project = match.group(1)
        _add(
            gaps,
            seen,
            Gap(
                f"tools:{normalize(project)[:80]}",
                f"What tools, languages, or methods are you using on {project}?",
                "A project is accepted without a recorded tool or method.",
                0.8,
            ),
        )
        tool_rank += 1

    inferred = [memory for memory in accepted if memory.get("stance") == "inferred"]
    for memory in inferred[:2]:
        _add(
            gaps,
            seen,
            Gap(
                f"confirm:{memory['id']}",
                f"Is this accurate: {memory['content'].rstrip('.')}?",
                "This accepted note is an inference, not something stated directly.",
                0.62,
            ),
        )

    if not any(memory.get("category") == "identity" for memory in accepted):
        _add(
            gaps,
            seen,
            Gap(
                "identity:name",
                "What name should the Brain use for you?",
                "There is no accepted identity note yet.",
                0.5,
            ),
        )

    gaps.sort(key=lambda gap: (-gap.usefulness, gap.key))
    return gaps[:12]


async def phrase_questions(gaps: list[Gap]) -> tuple[list[Gap], str, bool]:
    """Rephrase gaps with the model when a key exists. Returns gaps, generator, attempted."""
    if not gaps or not llm.available():
        return gaps, "fallback", False
    payload = [
        {"gap_key": gap.key, "question": gap.question, "motivation": gap.motivation}
        for gap in gaps
    ]
    try:
        raw = await llm.complete_json(_REWRITE_SYSTEM, json.dumps(payload), max_tokens=800)
    except Exception:
        logger.exception("question wording failed")
        return gaps, "fallback", True
    if not isinstance(raw, dict):
        return gaps, "fallback", True
    by_key = {gap.key: gap for gap in gaps}
    rewritten: dict[str, Gap] = {}
    for item in raw.get("questions") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("gap_key")
        if key not in by_key:
            continue
        text = " ".join(str(item.get("question") or "").split())
        if not text.endswith("?") or is_sensitive(text) or len(text) > 280:
            continue
        motivation = " ".join(str(item.get("motivation") or by_key[key].motivation).split()) or by_key[key].motivation
        current = by_key[key]
        rewritten[key] = Gap(current.key, text, motivation, current.usefulness)
    ordered = [rewritten.get(gap.key, gap) for gap in gaps]
    return ordered, "model", True
