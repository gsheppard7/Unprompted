"""Event loop: save a turn, maybe answer it, maybe speak without being asked."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import suppress
from typing import Awaitable, Callable

from fastapi import WebSocket

from app import cognition, db, llm
from app.brain.textutil import looks_like_self_knowledge
from app.policy import MODES, decide

logger = logging.getLogger(__name__)


def envelope(event_type: str, conversation_id: str, payload: dict) -> dict:
    return {
        "type": event_type,
        "conversation_id": conversation_id,
        "event_id": db.new_id(),
        "timestamp": db.now(),
        "payload": payload,
    }


class Hub:
    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = {}

    def join(self, conversation_id: str, socket: WebSocket) -> None:
        self.rooms.setdefault(conversation_id, set()).add(socket)

    def leave(self, conversation_id: str, socket: WebSocket) -> None:
        room = self.rooms.get(conversation_id)
        if not room:
            return
        room.discard(socket)
        if not room:
            self.rooms.pop(conversation_id, None)

    async def broadcast(self, conversation_id: str, message: dict) -> None:
        for socket in list(self.rooms.get(conversation_id, set())):
            try:
                await socket.send_json(message)
            except Exception:
                self.leave(conversation_id, socket)


class Engine:
    def __init__(self) -> None:
        self._master = threading.Lock()
        self._guards: dict[str, threading.Lock] = {}
        self.generation: dict[str, int] = {}
        self.cancel_reason: dict[str, str] = {}
        self.typing_until: dict[str, float] = {}
        self.busy: set[str] = set()
        self.reserved: set[str] = set()
        self.idle_for: dict[str, str] = {}
        self.chains: dict[str, asyncio.Task] = {}

    def guard(self, conversation_id: str) -> threading.Lock:
        with self._master:
            if conversation_id not in self._guards:
                self._guards[conversation_id] = threading.Lock()
            return self._guards[conversation_id]

    def bump(self, conversation_id: str, reason: str) -> None:
        self.generation[conversation_id] = self.generation.get(conversation_id, 0) + 1
        self.cancel_reason[conversation_id] = reason

    def set_typing(self, conversation_id: str, active: bool) -> None:
        if active:
            self.typing_until[conversation_id] = time.monotonic() + 5
        else:
            self.typing_until.pop(conversation_id, None)

    def is_typing(self, conversation_id: str) -> bool:
        until = self.typing_until.get(conversation_id)
        if until is None:
            return False
        if time.monotonic() > until:
            self.typing_until.pop(conversation_id, None)
            return False
        return True

    def enqueue(self, conversation_id: str, work: Callable[[], Awaitable[None]]) -> None:
        previous = self.chains.get(conversation_id)

        async def chained() -> None:
            if previous is not None:
                with suppress(Exception):
                    await previous
            try:
                await work()
            except Exception:
                logger.exception("cognition failed for %s", conversation_id)

        task = asyncio.create_task(chained())
        self.chains[conversation_id] = task


hub = Hub()
engine = Engine()


def speak_delay(mode: str) -> float:
    override = os.environ.get("UNPROMPTED_SPEAK_DELAY_S")
    if override:
        return float(override)
    return 1.7 if mode == "conservative" else 1.0


def idle_threshold(mode: str) -> float:
    override = os.environ.get("UNPROMPTED_FORCE_IDLE_S")
    if override is not None and override != "":
        return float(override)
    return MODES[mode]["idle_s"]


def status_payload(conversation_id: str, state: str | None = None) -> dict:
    conversation = db.get_conversation(conversation_id)
    mode = conversation["proactivity_mode"] if conversation else "off"
    if state is None:
        if mode == "off":
            state = "paused"
        elif conversation_id in engine.busy:
            state = "replying"
        else:
            state = "listening"
    return {"state": state, "proactivity_mode": mode, "model_mode": llm.mode_name()}


async def emit_status(conversation_id: str, state: str | None = None) -> None:
    await hub.broadcast(conversation_id, envelope("system.status", conversation_id, status_payload(conversation_id, state)))


def _score(proposal, mode: str, utility: float) -> dict:
    threshold = None if mode == "off" else MODES[mode]["threshold"]
    return {
        "relevance": round(proposal.relevance, 3),
        "evidence": round(proposal.evidence, 3),
        "novelty": round(proposal.novelty, 3),
        "urgency": round(proposal.urgency, 3),
        "interruption_cost": round(proposal.interruption_cost, 3),
        "utility": round(utility, 3),
        "threshold": threshold,
        "memory_ids": proposal.memory_ids,
    }


def _gates(conversation_id: str, mode: str) -> dict:
    config = MODES[mode]
    used = db.count_proactive_since(conversation_id, db.hour_ago())
    budget_remaining = int(config["hourly_budget"] - used)
    return {
        "typing": engine.is_typing(conversation_id),
        "busy": conversation_id in engine.busy,
        "budget_remaining": budget_remaining,
        "reserved": conversation_id in engine.reserved,
    }


def _maybe_open_thread(conversation_id: str, status: str, proposal) -> dict | None:
    if status != "save_only" or proposal.kind != "clarification":
        return None
    messages = db.list_messages(conversation_id)
    texts = cognition.user_texts(messages)
    if not texts:
        return None
    content = cognition.sentence(texts[-1])[:280]
    if db.find_memory(conversation_id, "open_thread", content):
        return None
    return db.add_memory(
        conversation_id,
        kind="open_thread",
        content=content,
        source_label="noticed, not verified",
        verified=False,
        confidence=0.3,
    )


async def _record(
    conversation_id: str,
    event_id: str | None,
    proposal,
    status: str,
    reason: str,
    utility: float,
    mode: str,
) -> dict:
    thought = db.add_thought(
        conversation_id,
        trigger_event_id=event_id,
        kind=proposal.kind,
        draft=proposal.draft or None,
        score=_score(proposal, mode, utility),
        status=status,
        reason=reason,
    )
    await hub.broadcast(
        conversation_id,
        envelope("thought.decision", conversation_id, {"thought": thought}),
    )
    memory = _maybe_open_thread(conversation_id, status, proposal)
    if memory:
        await hub.broadcast(
            conversation_id,
            envelope("memory.updated", conversation_id, {"memory": memory}),
        )
    return thought


def _brain_context(messages: list[dict]) -> list[dict]:
    """Relevant accepted Brain notes. A failure here must not stop the chat."""
    try:
        from app.brain.service import memories_for_chat

        return memories_for_chat(cognition.focus_text(messages))
    except Exception:
        logger.exception("brain context unavailable")
        return []


async def evaluate(conversation_id: str, event_id: str | None, event_type: str) -> str:
    conversation = db.get_conversation(conversation_id)
    if conversation is None:
        return "ignored"
    mode = conversation["proactivity_mode"]
    messages = db.list_messages(conversation_id)
    memories = db.list_memories(conversation_id)
    brain_memories = [item for item in _brain_context(messages) if item.get("verified")]
    proposal = cognition.propose(event_type, messages, memories + brain_memories)

    with engine.guard(conversation_id):
        gates = _gates(conversation_id, mode)
        decision = decide(proposal, mode=mode, **gates)
        if decision.status != "speak":
            should_speak = False
            generation = None
        else:
            should_speak = True
            generation = engine.generation.get(conversation_id, 0)
            engine.reserved.add(conversation_id)

    if not should_speak:
        await _record(conversation_id, event_id, proposal, decision.status, decision.reason, decision.utility, mode)
        await emit_status(conversation_id)
        return decision.status

    await emit_status(conversation_id, "considering")
    try:
        await asyncio.sleep(speak_delay(mode))
        with engine.guard(conversation_id):
            current = db.get_conversation(conversation_id)
            mode = current["proactivity_mode"] if current else "off"
            if engine.generation.get(conversation_id, 0) != generation:
                decision_status = "deferred"
                decision_reason = engine.cancel_reason.get(conversation_id, "Cancelled before speaking.")
            else:
                gates = _gates(conversation_id, mode)
                gates["reserved"] = False
                decision = decide(proposal, mode=mode, **gates)
                decision_status = decision.status
                decision_reason = decision.reason
            if decision_status == "speak":
                message = db.add_message(
                    conversation_id,
                    role="assistant",
                    text=proposal.draft,
                    initiator="proactive",
                    reason=decision_reason,
                )
            else:
                message = None
        if message is not None:
            await hub.broadcast(
                conversation_id,
                envelope("assistant.proactive", conversation_id, {"message": message}),
            )
        await _record(
            conversation_id,
            event_id,
            proposal,
            "delivered" if message is not None else decision_status,
            decision_reason,
            decision.utility,
            mode,
        )
        await emit_status(conversation_id)
        return "delivered" if message is not None else decision_status
    finally:
        engine.reserved.discard(conversation_id)


async def _direct_reply(conversation_id: str) -> None:
    engine.busy.add(conversation_id)
    await emit_status(conversation_id, "replying")
    try:
        conversation = db.get_conversation(conversation_id)
        mode = conversation["proactivity_mode"] if conversation else "off"
        messages = db.list_messages(conversation_id)
        memories = db.list_memories(conversation_id)
        brain_memories = _brain_context(messages)
        try:
            if llm.available():
                text = await llm.complete(messages, memories, brain_memories)
            else:
                text = cognition.direct_reply(messages, memories + brain_memories, mode)
        except Exception:
            logger.exception("direct reply failed")
            text = cognition.direct_reply(messages, memories + brain_memories, mode)
        if not text:
            return
        message = db.add_message(
            conversation_id,
            role="assistant",
            text=text,
            initiator="reply",
        )
        await hub.broadcast(
            conversation_id,
            envelope("assistant.message", conversation_id, {"message": message}),
        )
    finally:
        engine.busy.discard(conversation_id)


def _should_answer_directly(text: str, mode: str) -> bool:
    if cognition.parse_remember(text):
        return False
    return mode == "off" or cognition.is_addressed(text)


async def _process(conversation_id: str, text: str, event_id: str) -> None:
    remembered = cognition.parse_remember(text)
    if remembered:
        if not db.find_memory(conversation_id, "fact", remembered):
            memory = db.add_memory(
                conversation_id,
                kind="fact",
                content=remembered,
                source_label="saved by you",
                verified=True,
                confidence=0.8,
            )
            await hub.broadcast(
                conversation_id,
                envelope("memory.updated", conversation_id, {"memory": memory}),
            )
        message = db.add_message(
            conversation_id,
            role="assistant",
            text=f"Saved to memory: {remembered}",
            initiator="reply",
        )
        await hub.broadcast(
            conversation_id,
            envelope("assistant.message", conversation_id, {"message": message}),
        )
        proposal = cognition._quiet("Saved to memory.")
        mode = conversation["proactivity_mode"] if (conversation := db.get_conversation(conversation_id)) else "conservative"
        await _record(conversation_id, event_id, proposal, "ignored", proposal.reason, 0, mode)
        await emit_status(conversation_id)
        return

    conversation = db.get_conversation(conversation_id)
    mode = conversation["proactivity_mode"] if conversation else "off"
    if _should_answer_directly(text, mode):
        await _direct_reply(conversation_id)
        proposal = cognition._quiet("Answered directly.")
        await _record(conversation_id, event_id, proposal, "ignored", proposal.reason, 0, mode)
        await emit_status(conversation_id)
        return

    await evaluate(conversation_id, event_id, "user.message")


async def ingest_user_message(conversation_id: str, text: str) -> None:
    cleaned = text.strip()
    if not cleaned:
        return
    cleaned = cleaned[:4000]
    with engine.guard(conversation_id):
        engine.bump(conversation_id, "A newer message arrived before speaking.")
        engine.set_typing(conversation_id, False)
        message = db.add_message(
            conversation_id,
            role="user",
            text=cleaned,
            initiator="user",
        )
        event = db.add_event(conversation_id, "user.message", {"message_id": message["id"]})
        conversation = db.get_conversation(conversation_id)
        if conversation and conversation["title"] == "New conversation":
            title = cleaned.replace("\n", " ")
            if len(title) > 42:
                title = title[:42].rstrip() + "..."
            db.update_conversation(conversation_id, title=title)
            conversation = db.get_conversation(conversation_id)
    await hub.broadcast(
        conversation_id,
        envelope("message.created", conversation_id, {"message": message}),
    )
    if looks_like_self_knowledge(cleaned):
        await hub.broadcast(
            conversation_id,
            envelope("brain.suggestion", conversation_id, {"message_id": message["id"]}),
        )
    if conversation is not None:
        await hub.broadcast(
            conversation_id,
            envelope("conversation.updated", conversation_id, {"conversation": conversation}),
        )
    engine.enqueue(conversation_id, lambda: _process(conversation_id, cleaned, event["id"]))


def set_proactivity(conversation_id: str, mode: str) -> dict | None:
    conversation = db.update_conversation(conversation_id, proactivity_mode=mode)
    engine.bump(conversation_id, "Proactivity was changed before speaking.")
    if mode == "off":
        engine.reserved.discard(conversation_id)
    return conversation


async def maybe_idle(conversation_id: str) -> None:
    if engine.is_typing(conversation_id) or conversation_id in engine.busy or conversation_id in engine.reserved:
        return
    conversation = db.get_conversation(conversation_id)
    if conversation is None or conversation["proactivity_mode"] == "off":
        return
    messages = db.list_messages(conversation_id)
    if not messages:
        return
    last = messages[-1]
    elapsed = db.seconds_since(last["created_at"])
    if elapsed is None or elapsed < idle_threshold(conversation["proactivity_mode"]):
        return
    if last["initiator"] == "proactive":
        return
    if engine.idle_for.get(conversation_id) == last["id"]:
        return
    engine.idle_for[conversation_id] = last["id"]
    event = db.add_event(conversation_id, "session.idle", {"after_message_id": last["id"]})

    async def work() -> None:
        status = await evaluate(conversation_id, event["id"], "session.idle")
        if status == "deferred":
            engine.idle_for.pop(conversation_id, None)

    engine.enqueue(conversation_id, work)


async def idle_loop() -> None:
    while True:
        await asyncio.sleep(float(os.environ.get("UNPROMPTED_IDLE_POLL_S", "4")))
        for conversation_id in list(hub.rooms):
            if not hub.rooms.get(conversation_id):
                continue
            try:
                await maybe_idle(conversation_id)
            except Exception:
                logger.exception("idle check failed for %s", conversation_id)
