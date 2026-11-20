"""Brain operations used by the API and by chat retrieval."""

from __future__ import annotations

import logging
from pathlib import Path

from app import db, llm
from app.brain import events, extract, hooks, ingest, questions, retrieve, store, synthesize
from app.brain.extract import Candidate
from app.brain.models import CATEGORIES, CONFIDENCE_NOTE, SOURCE_TYPES, STANCES, STATUSES
from app.brain.textutil import clip

logger = logging.getLogger(__name__)

_SUMMARY_SYSTEM = (
    "Write a short profile in plain prose from the compiled notes. "
    "Use only those notes. If something is missing, say it is unknown. "
    "Do not add biography, traits, or advice. Keep inferences labeled as inferences. "
    "Four short paragraphs at most."
)

_PRIVACY = (
    "This Brain is stored in the local SQLite database for one user on this machine. "
    "Deleting a memory removes it from the Brain. An activity-log line can still mention it "
    "until the whole Brain is cleared. Text already sent to a model provider is not deleted there."
)


class BrainError(ValueError):
    pass


def availability_notice() -> str:
    if not llm.available():
        return "No model key is configured. Extraction, questions, and summaries stay on this machine."
    settings = llm.model_settings()
    return (
        f"A model key is configured ({settings['model']} at {settings['base_url']}). "
        "Teach, upload, question wording, and summary refresh may send the relevant text to that provider. "
        "The key stays on the server."
    )


def extraction_notice(extractor: str, attempted: bool) -> str:
    if extractor == "model":
        settings = llm.model_settings()
        return (
            f"Extraction used {settings['model']} at {settings['base_url']}. "
            "The source text was sent to that provider. The API key stayed on the server. "
            "Review every candidate before accepting it."
        )
    if attempted:
        return (
            "The on-device fallback produced these candidates after a model call did not return "
            "usable structure. The source text was still sent to the configured provider."
        )
    return (
        "Extraction used the on-device fallback. This text was not sent to a model provider. "
        "The fallback only catches recognizable patterns, plus a few leftover factual sentences."
    )


def _chat_source(memory: dict) -> str:
    source = memory.get("source") or {}
    kind = source.get("source_type")
    if kind == "upload" and source.get("filename"):
        return f"Brain · {source['filename']}"
    if kind == "question_answer":
        return "Brain · your answer"
    if kind == "conversation":
        return "Brain · conversation"
    if kind == "manual":
        return "Brain · manual"
    return "Brain"


def memories_for_chat(query: str, limit: int = 6) -> list[dict]:
    """Accepted memories shaped like conversation notes. Does not create a profile."""
    profile = store.get_profile()
    if profile is None or not query.strip():
        return []
    rows = retrieve.retrieve_memories(profile["id"], query, limit=limit, status="accepted")
    shaped = []
    for row in rows:
        shaped.append(
            {
                "id": row["id"],
                "kind": "fact",
                "content": row["content"],
                "source_label": _chat_source(row),
                "verified": row["stance"] == "explicit",
                "confidence": row["confidence"],
                "brain": True,
                "stance": row["stance"],
                "category": row["category"],
            }
        )
    return shaped


def _require_memory(memory_id: str) -> dict:
    profile = store.ensure_profile()
    memory = store.get_memory(memory_id)
    if memory is None or memory["brain_id"] != profile["id"]:
        raise KeyError(memory_id)
    return memory


def _require_question(question_id: str) -> tuple[dict, dict]:
    profile = store.ensure_profile()
    question = store.get_question(question_id)
    if question is None or question["brain_id"] != profile["id"]:
        raise KeyError(question_id)
    return profile, question


def overview() -> dict:
    profile = store.ensure_profile()
    accepted = store.list_memories(profile["id"], status="accepted")
    summary = synthesize.compile_summary(accepted)
    recent = sorted(accepted, key=lambda memory: memory["updated_at"], reverse=True)[:6]
    narrative = None
    if profile.get("summary_mode") == "model" and profile.get("summary"):
        narrative = {
            "text": profile["summary"],
            "notice": "This narrative was written by the configured model from an earlier compiled summary. It is not itself a memory.",
        }
    return {
        "profile": profile,
        "summary": summary,
        "narrative": narrative,
        "counts": store.counts(profile["id"]),
        "recent_memories": recent,
        "recent_changes": store.list_changes(profile["id"], limit=12),
        "extractor": "model" if llm.available() else "fallback",
        "confidence_note": CONFIDENCE_NOTE,
        "notices": [_PRIVACY, availability_notice()],
    }


def update_profile(*, name: str | None = None, questioning_paused: bool | None = None) -> dict:
    profile = store.ensure_profile()
    fields = {}
    if name is not None:
        cleaned = " ".join(name.split())
        if not cleaned:
            raise BrainError("The profile needs a name.")
        fields["name"] = cleaned[:80]
    if questioning_paused is not None:
        fields["questioning_paused"] = questioning_paused
    if not fields:
        return profile
    updated = store.update_profile(profile["id"], **fields)
    assert updated is not None
    if "name" in fields:
        store.add_change(profile["id"], "rename", f"Profile name is now {updated['name']}.")
    if "questioning_paused" in fields:
        detail = "Paused Brain questions." if updated["questioning_paused"] else "Resumed Brain questions."
        store.add_change(profile["id"], "pause", detail)
    return updated


async def refresh_summary() -> dict:
    profile = store.ensure_profile()
    accepted = store.list_memories(profile["id"], status="accepted")
    compiled = synthesize.compile_summary(accepted)
    notice = "Compiled again from accepted memories on this machine. No model call was made."
    summary = None
    mode = "compiled"
    if llm.available() and compiled["accepted_count"]:
        settings = llm.model_settings()
        try:
            text = await llm.complete_text(_SUMMARY_SYSTEM, compiled["text"], max_tokens=500)
        except Exception:
            logger.exception("brain summary failed")
            text = ""
        if text:
            summary = text
            mode = "model"
            notice = (
                f"The narrative was rewritten by {settings['model']} from the compiled summary. "
                "That summary was sent to the provider. The compiled summary is still the grounding."
            )
        else:
            notice = "The model summary failed, so only the compiled summary is shown."
    store.update_profile(
        profile["id"],
        summary=summary,
        summary_mode=mode,
        summary_updated_at=db.now(),
    )
    store.add_change(profile["id"], "summary", "Refreshed the Brain summary.")
    payload = overview()
    payload["summary_notice"] = notice
    return payload


def _save_candidates(
    profile: dict,
    *,
    text: str,
    source_type: str,
    candidates: list[Candidate],
    extractor: str,
    attempted: bool,
    filename: str | None = None,
    mime_type: str | None = None,
    byte_size: int | None = None,
    question_id: str | None = None,
    truncated: bool = False,
) -> dict:
    if source_type not in SOURCE_TYPES:
        raise BrainError("Unknown source type.")
    note_parts = [f"extractor={extractor}"]
    if truncated:
        note_parts.append("stored text truncated to 50000 characters")
    source = store.add_source(
        profile["id"],
        source_type=source_type,
        original_text=text,
        filename=Path(filename).name[:180] if filename else None,
        mime_type=mime_type,
        byte_size=byte_size,
        extract_note="; ".join(note_parts),
        question_id=question_id,
    )
    proposed = []
    duplicates = []
    for candidate in candidates:
        match = store.find_similar(profile["id"], candidate.content)
        if match is not None:
            duplicates.append(
                {
                    "content": candidate.content,
                    "matched_id": match["id"],
                    "matched_status": match["status"],
                    "matched_content": match["content"],
                }
            )
            continue
        proposed.append(
            store.add_memory(
                profile["id"],
                content=candidate.content,
                category=candidate.category,
                stance=candidate.stance,
                confidence=candidate.confidence,
                source_id=source["id"],
                evidence_text=candidate.evidence_text,
                entities=candidate.entities,
                relationships=candidate.relationships,
            )
        )
    detail = f"Proposed {len(proposed)} memories"
    if duplicates:
        detail += f" and skipped {len(duplicates)} duplicates"
    store.add_change(profile["id"], source_type, detail + ".")
    hooks.notify(
        {
            "type": "brain.ingested",
            "brain_id": profile["id"],
            "source_id": source["id"],
            "proposed": len(proposed),
        }
    )
    return {
        "source_id": source["id"],
        "extractor": extractor,
        "notice": extraction_notice(extractor, attempted),
        "proposed": proposed,
        "duplicates": duplicates,
    }


async def ingest_text(
    text: str,
    *,
    source_type: str = "manual",
    filename: str | None = None,
    mime_type: str | None = None,
    byte_size: int | None = None,
    question_id: str | None = None,
    fallback_limit: int = 8,
    prepared: list[Candidate] | None = None,
    prepared_extractor: str | None = None,
    prepared_attempted: bool = False,
) -> dict:
    cleaned = text.strip()
    if not cleaned:
        raise BrainError("There is no text to read.")
    truncated = len(cleaned) > 50_000
    if truncated:
        cleaned = cleaned[:50_000]
    profile = store.ensure_profile()
    if prepared is None:
        candidates, extractor, attempted = await extract.extract_text(cleaned, fallback_limit=fallback_limit)
    else:
        candidates, extractor, attempted = prepared, prepared_extractor or "fallback", prepared_attempted
    return _save_candidates(
        profile,
        text=cleaned,
        source_type=source_type,
        candidates=candidates,
        extractor=extractor,
        attempted=attempted,
        filename=filename,
        mime_type=mime_type,
        byte_size=byte_size,
        question_id=question_id,
        truncated=truncated,
    )


async def ingest_file(filename: str, content_type: str | None, data: bytes) -> dict:
    text, kind = ingest.read_upload(filename, content_type, data)
    if not text.strip():
        raise BrainError("No readable text found in that file.")
    limit = 12 if kind == "pdf" else 10
    return await ingest_text(
        text,
        source_type="upload",
        filename=filename or f"upload.{kind}",
        mime_type=content_type,
        byte_size=len(data),
        fallback_limit=limit,
    )


def list_memories(status: str | None, category: str | None, query: str | None, limit: int) -> dict:
    profile = store.ensure_profile()
    if query and query.strip():
        memories = retrieve.retrieve_memories(profile["id"], query, limit=limit, status=status)
        if category:
            memories = [memory for memory in memories if memory["category"] == category]
    else:
        memories = store.list_memories(profile["id"], status=status, category=category, limit=limit)
    return {"memories": memories}


def set_status(memory_id: str, status: str) -> dict:
    if status not in STATUSES:
        raise BrainError("Unknown memory status.")
    memory = _require_memory(memory_id)
    updated = store.update_memory(memory_id, status=status)
    assert updated is not None
    label = "stated" if memory["stance"] == "explicit" else memory["stance"]
    store.add_change(memory["brain_id"], status, f"Marked a {label} memory as {status}: {clip(memory['content'], 120)}")
    hooks.notify({"type": f"memory.{status}", "brain_id": memory["brain_id"], "memory_id": memory_id})
    return updated


def review(accept_ids: list[str], reject_ids: list[str]) -> dict:
    accepted = []
    rejected = []
    missing = []
    for memory_id in accept_ids:
        try:
            accepted.append(set_status(memory_id, "accepted"))
        except KeyError:
            missing.append(memory_id)
    for memory_id in reject_ids:
        try:
            rejected.append(set_status(memory_id, "rejected"))
        except KeyError:
            missing.append(memory_id)
    return {"accepted": accepted, "rejected": rejected, "missing": missing}


def edit_memory(memory_id: str, **fields) -> dict:
    memory = _require_memory(memory_id)
    updates = {}
    if "content" in fields and fields["content"] is not None:
        content = " ".join(fields["content"].split())
        if len(content) < 3:
            raise BrainError("Memory text is too short.")
        updates["content"] = content
    if fields.get("category") is not None:
        if fields["category"] not in CATEGORIES:
            raise BrainError("Unknown category.")
        updates["category"] = fields["category"]
    if fields.get("stance") is not None:
        if fields["stance"] not in STANCES:
            raise BrainError("Unknown stance.")
        updates["stance"] = fields["stance"]
    if fields.get("status") is not None:
        if fields["status"] not in STATUSES:
            raise BrainError("Unknown memory status.")
        updates["status"] = fields["status"]
    for key in ("valid_from", "valid_to"):
        if key in fields:
            value = fields[key]
            updates[key] = value.strip() if isinstance(value, str) and value.strip() else None
    if fields.get("supersedes_id"):
        other = store.get_memory(fields["supersedes_id"])
        if other is None or other["brain_id"] != memory["brain_id"] or other["id"] == memory["id"]:
            raise BrainError("That memory cannot supersede the selected note.")
        updates["supersedes_id"] = other["id"]
        if other["status"] == "accepted":
            store.update_memory(other["id"], status="superseded")
            store.add_change(memory["brain_id"], "supersede", "Marked an older memory as superseded. Its source text was kept.")
    if not updates:
        return memory
    updated = store.update_memory(memory_id, **updates)
    assert updated is not None
    store.add_change(memory["brain_id"], "edit", f"Edited a memory: {clip(updated['content'], 120)}")
    hooks.notify({"type": "memory.edited", "brain_id": memory["brain_id"], "memory_id": memory_id})
    return updated


def remove_memory(memory_id: str) -> None:
    memory = _require_memory(memory_id)
    if not store.delete_memory(memory_id):
        raise KeyError(memory_id)
    store.add_change(memory["brain_id"], "delete", "Deleted a memory. The activity log may still mention it until the Brain is cleared.")
    hooks.notify({"type": "memory.deleted", "brain_id": memory["brain_id"], "memory_id": memory_id})


def graph() -> dict:
    profile = store.ensure_profile()
    rows = store.list_relationships(profile["id"], status="accepted")
    grouped: dict[tuple, dict] = {}
    memories: dict[str, dict] = {}
    for row in rows:
        key = (row["source_entity_id"], row["target_entity_id"], row["relationship_type"], row["stance"])
        bucket = grouped.setdefault(key, {"row": row, "memory_ids": [], "categories": []})
        memory_id = row.get("source_memory_id")
        if memory_id and memory_id not in bucket["memory_ids"]:
            bucket["memory_ids"].append(memory_id)
            if row.get("category") and row["category"] not in bucket["categories"]:
                bucket["categories"].append(row["category"])
            memories[memory_id] = {
                "id": memory_id,
                "content": row.get("memory_content"),
                "category": row.get("category"),
                "stance": row.get("memory_stance"),
                "status": row.get("memory_status"),
            }
    nodes: dict[str, dict] = {}
    edges = []
    display_name = profile["name"] if profile["name"].strip().lower() not in {"you", "user"} else "User"
    for key, bucket in grouped.items():
        row = bucket["row"]
        for entity_id, name, entity_type in (
            (row["source_entity_id"], row["source_name"], row["source_entity_type"]),
            (row["target_entity_id"], row["target_name"], row["target_entity_type"]),
        ):
            node = nodes.setdefault(
                entity_id,
                {
                    "id": entity_id,
                    "label": display_name if name == "User" else name,
                    "entity_type": entity_type,
                    "is_self": name == "User",
                    "memory_ids": [],
                },
            )
            for memory_id in bucket["memory_ids"]:
                if memory_id not in node["memory_ids"]:
                    node["memory_ids"].append(memory_id)
        edges.append(
            {
                "id": row["id"],
                "source": row["source_entity_id"],
                "target": row["target_entity_id"],
                "label": row["relationship_type"],
                "stance": row["stance"],
                "status": "accepted",
                "memory_ids": bucket["memory_ids"],
                "categories": bucket["categories"],
            }
        )
    return {"nodes": list(nodes.values()), "edges": edges, "memories": list(memories.values())}


def _available_gaps(brain_id: str, ignore_ids: set[str] | None = None) -> list[questions.Gap]:
    ignore_ids = ignore_ids or set()
    memories = store.list_memories(brain_id, status="accepted", limit=500)
    ranked = [gap for gap in questions.rank_gaps(memories) if not questions.is_sensitive(gap.question)]
    blocked_keys: set[str] = set()
    blocked_text: set[str] = set()
    for question in store.list_questions(brain_id):
        if question["id"] in ignore_ids:
            continue
        if question.get("gap_key"):
            blocked_keys.add(question["gap_key"])
        blocked_text.add(question["question_text"].strip().lower())
    return [
        gap
        for gap in ranked
        if gap.key not in blocked_keys and gap.question.strip().lower() not in blocked_text
    ]


async def _create_questions(brain_id: str, needed: int) -> str:
    if needed <= 0:
        return "fallback"
    chosen = _available_gaps(brain_id)[:needed]
    if not chosen:
        return "fallback"
    phrased, generator, _attempted = await questions.phrase_questions(chosen)
    for gap in phrased:
        if questions.is_sensitive(gap.question):
            continue
        if store.known_question(brain_id, gap.key, gap.question):
            continue
        store.add_question(
            brain_id,
            question_text=gap.question,
            motivation=gap.motivation,
            gap_key=gap.key,
            usefulness=gap.usefulness,
            generator=generator,
        )
        hooks.notify({"type": "question.created", "brain_id": brain_id, "gap_key": gap.key})
    return generator


def _question_payload(profile: dict, message: str | None, generator: str, refreshed: bool) -> dict:
    pending = store.list_questions(profile["id"], status="pending")[:3]
    if profile["questioning_paused"]:
        pending = []
    notice = (
        "Question wording used the configured model. The gap list, which includes accepted memories, was sent to that provider."
        if generator == "model"
        else "Questions were chosen on this machine from accepted memories. No model call was made."
    )
    return {
        "paused": profile["questioning_paused"],
        "questions": pending,
        "message": message,
        "generator": generator,
        "notice": notice,
        "refreshed": refreshed,
    }


async def generate_questions(refresh: bool = False) -> dict:
    profile = store.ensure_profile()
    if profile["questioning_paused"]:
        return _question_payload(profile, "Questioning is paused.", "fallback", False)
    pending = store.list_questions(profile["id"], status="pending")
    if refresh:
        extra = _available_gaps(profile["id"])
        if not extra:
            return _question_payload(
                profile,
                "No different questions from the current memories.",
                "fallback",
                False,
            )
        for question in pending:
            store.update_question(question["id"], status="skipped")
        pending = []
    if len(pending) >= 3 and not refresh:
        return _question_payload(profile, None, pending[0].get("generator") or "fallback", False)
    generator = await _create_questions(profile["id"], 3 - len(pending))
    created = store.list_questions(profile["id"], status="pending")
    if len(created) > len(pending):
        store.add_change(profile["id"], "question", f"Suggested {len(created) - len(pending)} questions.")
    message = None if created else "No questions to ask from the accepted memories yet."
    return _question_payload(profile, message, generator, refresh)


async def answer_question(question_id: str, text: str) -> dict:
    profile, question = _require_question(question_id)
    if question["status"] != "pending":
        raise BrainError("That question is not waiting for an answer.")
    cleaned = " ".join(text.split())
    if not cleaned:
        raise BrainError("Write an answer first.")
    targeted = extract.extract_answer(question["question_text"], cleaned)
    model_items: list[Candidate] = []
    attempted = False
    if llm.available():
        attempted = True
        try:
            model_items = await extract.model_extract(f"Question: {question['question_text']}\nAnswer: {cleaned}")
        except Exception:
            logger.exception("answer extraction failed")
            model_items = []
    if model_items:
        candidates = extract.dedupe(targeted + model_items)
        extractor = "model"
    else:
        candidates = targeted
        extractor = "fallback"
    store.update_question(
        question_id,
        status="answered",
        answer_text=cleaned,
        answered_at=db.now(),
    )
    result = await ingest_text(
        cleaned,
        source_type="question_answer",
        question_id=question_id,
        prepared=candidates,
        prepared_extractor=extractor,
        prepared_attempted=attempted,
    )
    store.add_change(profile["id"], "answer", "Recorded an answer. New memories still need approval.")
    result["question"] = store.get_question(question_id)
    return result


async def skip_question(question_id: str, status: str) -> dict:
    if status not in {"skipped", "dismissed"}:
        raise BrainError("Unknown question action.")
    profile, question = _require_question(question_id)
    if question["status"] != "pending":
        raise BrainError("That question is not waiting for an answer.")
    updated = store.update_question(question_id, status=status)
    if not profile["questioning_paused"]:
        await _create_questions(profile["id"], 1)
    return {
        "question": updated,
        "questions": store.list_questions(profile["id"], status="pending")[:3],
        "paused": profile["questioning_paused"],
    }


def current_questions() -> dict:
    profile = store.ensure_profile()
    pending = store.list_questions(profile["id"], status="pending")
    generator = "model" if any(question.get("generator") == "model" for question in pending) else "fallback"
    return _question_payload(profile, None, generator, False)


def export_data() -> dict:
    profile = store.ensure_profile()
    return store.export_brain(profile["id"])


def clear() -> dict:
    profile = store.get_profile()
    if profile is None:
        return store.ensure_profile()
    return store.clear_brain(profile["id"])


async def publish(reason: str) -> None:
    await events.hub.broadcast({"type": "brain.updated", "reason": reason, "timestamp": db.now()})
