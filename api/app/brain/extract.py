"""Turn raw text into memory candidates.

A configured model may see the source text. Without a key, a pattern fallback
runs on this machine. Either path returns the same candidate shape, and nothing
is authoritative until a person accepts it.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from app import llm
from app.brain.models import STANCE_CONFIDENCE, STANCES, ExtractedMemory
from app.brain.textutil import canon_entity, clip, same_memory, sentence, title_phrase, tokens

logger = logging.getLogger(__name__)

LANGUAGES = {
    "python",
    "javascript",
    "typescript",
    "java",
    "rust",
    "go",
    "golang",
    "c",
    "c++",
    "c#",
    "sql",
    "ruby",
    "kotlin",
    "swift",
    "html",
    "css",
    "r",
}

_SKIP_ITEMS = {"it", "them", "this", "that", "things", "stuff", "something", "someone"}

_NAME = re.compile(r"\bmy name is\s+([A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,2})\b")
_STUDENT = re.compile(r"\b(?:i'm|i am)\s+an?\s+(.+?)\s+student\b", re.IGNORECASE)
_STUDY = re.compile(
    r"\b(?:i study|i'm studying|i am studying|i studied|i major in|my major is)\s+(.+?)(?:\.|$)",
    re.IGNORECASE,
)
_INTEREST = re.compile(r"\binterested in\s+(.+?)(?:\.|$)", re.IGNORECASE)
_INTO = re.compile(r"\b(?:i'm|i am)\s+into\s+(.+?)(?:\.|$)", re.IGNORECASE)
_ENJOY = re.compile(r"\b(?:i enjoy|i like|i love|who enjoys)\s+(.+?)(?:\.|$)", re.IGNORECASE)
_WORK = re.compile(r"\b(?:i'm|i am|i)\s+work(?:ing)?\s+on\s+(.+?)(?:\.|$)", re.IGNORECASE)
_GOAL = re.compile(
    r"\b(?:my goal is to|i want to|i hope to|i aim to|i'm trying to|i am trying to|my goal is)\s+(.+?)(?:\.|$)",
    re.IGNORECASE,
)
_PREFER = re.compile(r"\b(?:i prefer|i'd rather|i would rather)\s+(.+?)(?:\.|$)", re.IGNORECASE)
_SKILL = re.compile(
    r"\b(?:i'm skilled in|i am skilled in|i'm good at|i am good at|i know how to)\s+(.+?)(?:\.|$)",
    re.IGNORECASE,
)
_PERSON = re.compile(
    r"\bmy\s+(friend|partner|advisor|professor|colleague|mentor|sister|brother|parent|mom|dad)\s+"
    r"(?:is\s+)?([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)\b"
)
_DID = re.compile(
    r"\b(?:i built|i made|i led|i designed|i shipped|i wrote|i competed in|i played)\s+(.+?)(?:\.|$)",
    re.IGNORECASE,
)
_ROLE = re.compile(
    r"\b(?:i'm|i am)\s+an?\s+([a-z][\w+#.-]*(?:\s+[a-z][\w+#.-]*){0,4})\b",
    re.IGNORECASE,
)
_USING = re.compile(r"\s+using\s+(.+)$", re.IGNORECASE)
_NO = re.compile(r"^(no|nope|nah|not really|none|nothing|i have not|i haven't)\b", re.IGNORECASE)

_MODEL_SYSTEM = (
    "You extract personal knowledge candidates from text a user supplied. "
    'Return JSON {"memories":[...]}. Each memory has content, category, stance, evidence, '
    "entities, and relationships. category is one of identity, education, interests, projects, "
    "experiences, goals, preferences, skills, relationships, other. stance is explicit when the "
    "text states it, inferred for a careful implication, or hypothesis when uncertain. "
    "entities are {name, entity_type}. entity_type is person, field, interest, skill, project, "
    "technology, organization, goal, preference, or other. relationships are "
    "{source, target, relationship_type, stance}. Call the subject User. "
    "Split lists into separate memories. Do not invent facts the text does not support. "
    "Do not add medical, political, religious, or financial guesses. "
    'If nothing is worth storing, return {"memories":[]}.'
)


@dataclass
class Candidate:
    content: str
    category: str
    stance: str
    confidence: float
    evidence_text: str
    entities: list[dict] = field(default_factory=list)
    relationships: list[dict] = field(default_factory=list)


def dedupe(items: list[Candidate]) -> list[Candidate]:
    kept: list[Candidate] = []
    for item in items:
        if any(same_memory(item.content, previous.content) for previous in kept):
            continue
        kept.append(item)
    return kept


def _split_items(text: str) -> list[str]:
    cleaned = text.strip().rstrip(".")
    cleaned = re.sub(r"\s+(?:and|or)\s+", ", ", cleaned, flags=re.IGNORECASE)
    items: list[str] = []
    for part in cleaned.split(","):
        item = re.sub(r"^(?:also|especially)\s+", "", part.strip(" .;:"), flags=re.IGNORECASE)
        if not item or item.lower() in _SKIP_ITEMS or len(item) > 80:
            continue
        if re.search(r"\b(?:i'm|i am|\bi\b)\b", item, re.IGNORECASE):
            continue
        items.append(item)
    return items


def make(
    content: str,
    category: str,
    stance: str,
    evidence: str,
    entities: list[tuple[str, str]],
    relationships: list[tuple[str, str, str, str]],
) -> Candidate | None:
    content = sentence(content)
    if stance not in STANCES or len(tokens(content)) < 3:
        return None
    packed: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for name, entity_type in entities:
        canon = canon_entity(name)
        if not canon:
            continue
        key = (canon, entity_type)
        if key in seen:
            continue
        seen.add(key)
        packed.append({"name": canon, "entity_type": entity_type})
    names = {item["name"] for item in packed}
    rels: list[dict] = []
    for source, target, rel_type, rel_stance in relationships:
        source_name = canon_entity(source)
        target_name = canon_entity(target)
        if source_name not in names or target_name not in names or source_name == target_name:
            continue
        rels.append(
            {
                "source": source_name,
                "target": target_name,
                "relationship_type": " ".join(rel_type.lower().split())[:40],
                "stance": rel_stance if rel_stance in STANCES else stance,
            }
        )
    return Candidate(
        content=content,
        category=category,
        stance=stance,
        confidence=STANCE_CONFIDENCE[stance],
        evidence_text=clip(evidence, 500),
        entities=packed,
        relationships=rels,
    )


def _interest(item: str, evidence: str, *, enjoys: bool) -> Candidate | None:
    if item.lower().startswith("to "):
        return make(
            f"User wants {item}.",
            "goals",
            "explicit",
            evidence,
            [("User", "person"), (title_phrase(item), "goal")],
            [("User", title_phrase(item), "wants", "explicit")],
        )
    label = title_phrase(item)
    if enjoys:
        return make(
            f"User enjoys {item}.",
            "interests",
            "explicit",
            evidence,
            [("User", "person"), (label, "interest")],
            [("User", label, "enjoys", "explicit")],
        )
    return make(
        f"User is interested in {item}.",
        "interests",
        "explicit",
        evidence,
        [("User", "person"), (label, "interest")],
        [("User", label, "interested in", "explicit")],
    )


def _study(raw: str, evidence: str) -> list[Candidate]:
    found: list[Candidate] = []
    items = _split_items(raw) or [raw.strip(" .")]
    for item in items:
        label = title_phrase(item)
        if not label:
            continue
        memory = make(
            f"User studies {item}.",
            "education",
            "explicit",
            evidence,
            [("User", "person"), (label, "field")],
            [("User", label, "studies", "explicit")],
        )
        if memory:
            found.append(memory)
    return found


def _project(body: str, evidence: str) -> list[Candidate]:
    using = _USING.search(body.strip())
    project_text = body[: using.start()].strip(" .") if using else body.strip(" .")
    if not project_text:
        return []
    project_name = title_phrase(project_text)
    phrase = project_text if re.match(r"(?i)(?:a|an|the)\s+", project_text) else project_text
    found: list[Candidate] = []
    work = make(
        f"User works on {phrase}.",
        "projects",
        "explicit",
        evidence,
        [("User", "person"), (project_name, "project")],
        [("User", project_name, "works on", "explicit")],
    )
    if work:
        found.append(work)
    if not using:
        return found
    for tool in _split_items(using.group(1)):
        tool_name = title_phrase(tool)
        uses = make(
            f"{project_name} uses {tool_name}.",
            "projects",
            "explicit",
            evidence,
            [(project_name, "project"), (tool_name, "technology")],
            [(project_name, tool_name, "uses", "explicit")],
        )
        if uses:
            found.append(uses)
        if tool.lower() not in LANGUAGES:
            related = make(
                f"{project_name} is related to {tool_name}.",
                "projects",
                "inferred",
                evidence,
                [(project_name, "project"), (tool_name, "technology")],
                [(project_name, tool_name, "related to", "inferred")],
            )
            if related:
                found.append(related)
    return found


def extract_sentence(text: str) -> list[Candidate]:
    evidence = text.strip()
    found: list[Candidate] = []
    student = _STUDENT.search(text)

    name = _NAME.search(text)
    if name:
        person = title_phrase(name.group(1))
        memory = make(f"The user's name is {person}.", "identity", "explicit", evidence, [("User", "person")], [])
        if memory:
            found.append(memory)

    if student:
        found.extend(_study(student.group(1), evidence))
    else:
        study = _STUDY.search(text)
        if study:
            found.extend(_study(study.group(1), evidence))

    for pattern in (_INTEREST, _INTO):
        for match in pattern.finditer(text):
            for item in _split_items(match.group(1)):
                memory = _interest(item, evidence, enjoys=False)
                if memory:
                    found.append(memory)

    enjoy = _ENJOY.search(text)
    if enjoy:
        for item in _split_items(enjoy.group(1)):
            memory = _interest(item, evidence, enjoys=True)
            if memory:
                found.append(memory)

    work = _WORK.search(text)
    if work:
        found.extend(_project(work.group(1), evidence))

    goal = _GOAL.search(text)
    if goal:
        phrase = goal.group(1).strip(" .")
        goal_name = title_phrase(phrase)
        memory = make(
            f"User wants to {phrase}.",
            "goals",
            "explicit",
            evidence,
            [("User", "person"), (goal_name, "goal")],
            [("User", goal_name, "wants", "explicit")],
        )
        if memory:
            found.append(memory)

    prefer = _PREFER.search(text)
    if prefer:
        for item in _split_items(prefer.group(1)):
            label = title_phrase(item)
            kind = "technology" if item.lower() in LANGUAGES else "preference"
            memory = make(
                f"User prefers {item}.",
                "preferences",
                "explicit",
                evidence,
                [("User", "person"), (label, kind)],
                [("User", label, "prefers", "explicit")],
            )
            if memory:
                found.append(memory)

    skill = _SKILL.search(text)
    if skill:
        for item in _split_items(skill.group(1)):
            label = title_phrase(item)
            memory = make(
                f"User is skilled in {item}.",
                "skills",
                "explicit",
                evidence,
                [("User", "person"), (label, "skill")],
                [("User", label, "skilled in", "explicit")],
            )
            if memory:
                found.append(memory)

    for match in _PERSON.finditer(text):
        role = match.group(1).lower()
        person = title_phrase(match.group(2))
        memory = make(
            f"User's {role} is {person}.",
            "relationships",
            "explicit",
            evidence,
            [("User", "person"), (person, "person")],
            [("User", person, role, "explicit")],
        )
        if memory:
            found.append(memory)

    did = _DID.search(text)
    if did:
        phrase = did.group(1).strip(" .")
        label = title_phrase(phrase)
        verb = did.group(0).split()[1].lower()
        memory = make(
            f"User {verb} {phrase}.",
            "experiences",
            "explicit",
            evidence,
            [("User", "person"), (label, "other")],
            [("User", label, verb, "explicit")],
        )
        if memory:
            found.append(memory)

    if not student:
        role = _ROLE.search(text)
        if role:
            phrase = re.split(r"\b(?:interested|working|who|that)\b", role.group(1), maxsplit=1, flags=re.IGNORECASE)[0]
            label = title_phrase(phrase)
            if label and label.lower() not in {"student", "person"}:
                memory = make(
                    f"User is a {phrase.strip()}.",
                    "identity",
                    "explicit",
                    evidence,
                    [("User", "person"), (label, "other")],
                    [("User", label, "is", "explicit")],
                )
                if memory:
                    found.append(memory)
    return found


def sentences(text: str) -> list[str]:
    chunks: list[str] = []
    for block in text.replace("\r", "\n").split("\n"):
        block = " ".join(block.split())
        if not block:
            continue
        parts = re.split(r"(?<=[.!?])\s+", block)
        chunks.extend(part.strip() for part in parts if part.strip())
    return chunks


def deterministic_extract(text: str, *, fallback_limit: int = 8) -> list[Candidate]:
    found: list[Candidate] = []
    leftovers: list[str] = []
    for chunk in sentences(text):
        structured = extract_sentence(chunk)
        if structured:
            found.extend(structured)
        else:
            leftovers.append(chunk)
    used = 0
    for chunk in leftovers:
        if used >= fallback_limit:
            break
        words = re.findall(r"[A-Za-z0-9']+", chunk)
        if len(words) < 5 or chunk.endswith("?"):
            continue
        memory = make(sentence(chunk), "other", "explicit", chunk, [], [])
        if memory:
            found.append(memory)
            used += 1
    return dedupe(found)


def validate_extraction(payload: object) -> list[Candidate]:
    """Keep well-formed model items and drop the rest. One bad item does not reject the batch."""
    if not isinstance(payload, dict):
        return []
    items = payload.get("memories")
    if not isinstance(items, list):
        return []
    found: list[Candidate] = []
    for item in items[:40]:
        try:
            parsed = ExtractedMemory.model_validate(item)
        except ValidationError:
            continue
        entities = [(entity.name, entity.entity_type) for entity in parsed.entities]
        known = {canon_entity(name) for name, _kind in entities}
        relationships: list[tuple[str, str, str, str]] = []
        for rel in parsed.relationships:
            for endpoint, kind in ((rel.source, "person" if canon_entity(rel.source) == "User" else "other"), (rel.target, "other")):
                canon = canon_entity(endpoint)
                if canon and canon not in known:
                    entities.append((endpoint, "person" if canon == "User" else kind))
                    known.add(canon)
            relationships.append((rel.source, rel.target, rel.relationship_type, rel.stance))
        content = re.sub(r"^[-*\d.)\s]+", "", parsed.content.strip())
        memory = make(
            content,
            parsed.category,
            parsed.stance,
            parsed.evidence or parsed.content,
            entities,
            relationships,
        )
        if memory:
            found.append(memory)
    return dedupe(found)[:40]


async def model_extract(text: str) -> list[Candidate]:
    raw = await llm.complete_json(_MODEL_SYSTEM, text[:12_000], max_tokens=1400)
    return validate_extraction(raw)


async def extract_text(text: str, *, fallback_limit: int = 8) -> tuple[list[Candidate], str, bool]:
    """Return candidates, extractor name, and whether a provider call was attempted."""
    attempted = False
    if llm.available():
        attempted = True
        try:
            parsed = await model_extract(text)
            if parsed:
                return parsed, "model", True
        except Exception:
            logger.exception("brain model extraction failed")
    return deterministic_extract(text, fallback_limit=fallback_limit), "fallback", attempted


def extract_answer(question: str, answer: str) -> list[Candidate]:
    """Deterministic memories for a Brain question. The caller may also try a model."""
    answer = " ".join(answer.split()).strip()
    question = " ".join(question.split()).strip()
    if not answer:
        return []
    targeted = _from_question(question, answer)
    generic = deterministic_extract(answer, fallback_limit=0 if targeted else 4)
    merged = dedupe(targeted + generic)
    if merged:
        return merged[:12]
    wrapped = make(
        f'When asked "{clip(question, 140)}", the user answered: {sentence(answer)}',
        "other",
        "explicit",
        answer,
        [("User", "person")],
        [],
    )
    return [wrapped] if wrapped else []


def _within(topic: str, item: str, answer: str) -> Candidate | None:
    label = title_phrase(item)
    topic_label = title_phrase(topic)
    return make(
        f"User is especially interested in {item} within {topic}.",
        "interests",
        "explicit",
        answer,
        [("User", "person"), (label, "interest"), (topic_label, "interest")],
        [
            ("User", label, "interested in", "explicit"),
            (label, topic_label, "related to", "inferred"),
        ],
    )


def _from_question(question: str, answer: str) -> list[Candidate]:
    negative = bool(_NO.match(answer))
    aspect = re.fullmatch(r"What aspect of (.+?) interests you the most\?", question, flags=re.IGNORECASE)
    if aspect:
        topic = aspect.group(1)
        if negative:
            memory = make(
                f"User did not name a specific aspect of {topic}.",
                "interests",
                "explicit",
                answer,
                [("User", "person"), (title_phrase(topic), "interest")],
                [],
            )
            return [memory] if memory else []
        if len(answer.split()) <= 14:
            found = [item for item in (_within(topic, piece, answer) for piece in _split_items(answer)) if item]
            if found:
                return found
        return []

    project = re.fullmatch(
        r"Have you worked on any (.+?) projects outside of coursework\?",
        question,
        flags=re.IGNORECASE,
    )
    if project:
        topic = project.group(1)
        if negative:
            memory = make(
                f"User said they have not worked on a {topic} project outside coursework.",
                "projects",
                "explicit",
                answer,
                [("User", "person"), (title_phrase(topic), "interest")],
                [],
            )
            return [memory] if memory else []
        if len(answer.split()) <= 18:
            label = title_phrase(answer.rstrip("."))
            memory = make(
                f"User works on {answer.rstrip('.')} related to {topic}.",
                "projects",
                "explicit",
                answer,
                [("User", "person"), (label, "project"), (title_phrase(topic), "interest")],
                [
                    ("User", label, "works on", "explicit"),
                    (label, title_phrase(topic), "related to", "explicit"),
                ],
            )
            return [memory] if memory else []
        return []

    goal = re.fullmatch(r"What are you hoping to do with your (.+?) studies\?", question, flags=re.IGNORECASE)
    if goal and not negative:
        field = goal.group(1)
        phrase = answer.rstrip(".")
        memory = make(
            f"User wants to {phrase}.",
            "goals",
            "explicit",
            answer,
            [("User", "person"), (title_phrase(phrase), "goal"), (title_phrase(field), "field")],
            [("User", title_phrase(phrase), "wants", "explicit")],
        )
        return [memory] if memory else []

    hobby = re.fullmatch(r"What do you enjoy most about (.+?)\?", question, flags=re.IGNORECASE)
    if hobby and not negative and len(answer.split()) <= 16:
        topic = hobby.group(1)
        memory = make(
            f"User enjoys {answer.rstrip('.')} about {topic}.",
            "interests",
            "explicit",
            answer,
            [("User", "person"), (title_phrase(topic), "interest")],
            [("User", title_phrase(topic), "enjoys", "explicit")],
        )
        return [memory] if memory else []

    tools = re.fullmatch(
        r"What tools, languages, or methods are you using on (.+?)\?",
        question,
        flags=re.IGNORECASE,
    )
    if tools and not negative:
        project = title_phrase(tools.group(1).rstrip("."))
        found: list[Candidate] = []
        for tool in _split_items(answer):
            tool_name = title_phrase(tool)
            memory = make(
                f"{project} uses {tool_name}.",
                "projects",
                "explicit",
                answer,
                [(project, "project"), (tool_name, "technology")],
                [(project, tool_name, "uses", "explicit")],
            )
            if memory:
                found.append(memory)
        return found

    confirm = re.fullmatch(r"Is this accurate: (.+)\?", question, flags=re.IGNORECASE)
    if confirm:
        claim = confirm.group(1).strip()
        if re.match(r"^(yes|yeah|yep|correct|it is)\b", answer, flags=re.IGNORECASE):
            memory = make(
                f"User confirmed: {sentence(claim)}",
                "other",
                "explicit",
                answer,
                [("User", "person")],
                [],
            )
            return [memory] if memory else []
        if negative or re.match(r"^(no|not)\b", answer, flags=re.IGNORECASE):
            memory = make(
                f"User said this is not accurate: {sentence(claim)}",
                "other",
                "explicit",
                answer,
                [("User", "person")],
                [],
            )
            return [memory] if memory else []

    if re.fullmatch(r"What name should the Brain use for you\?", question, flags=re.IGNORECASE):
        person = title_phrase(answer.rstrip("."))
        if person and len(person.split()) <= 4:
            memory = make(
                f"The user's name is {person}.",
                "identity",
                "explicit",
                answer,
                [("User", "person")],
                [],
            )
            return [memory] if memory else []
    return []
