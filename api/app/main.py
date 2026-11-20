"""FastAPI routes and the per-conversation WebSocket."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app import db, llm, runtime
from app.brain.routes import router as brain_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
llm.load_env_file()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    if os.environ.get("UNPROMPTED_SEED", "1") != "0":
        db.seed_example()
    idle_task = asyncio.create_task(runtime.idle_loop())
    yield
    idle_task.cancel()
    with suppress(asyncio.CancelledError):
        await idle_task
    db.close_db()


app = FastAPI(title="Unprompted", lifespan=lifespan)
app.include_router(brain_router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConversationIn(BaseModel):
    title: str | None = None


class ConversationPatch(BaseModel):
    title: str | None = None
    proactivity_mode: str | None = None


class MemoryIn(BaseModel):
    content: str = Field(min_length=1, max_length=1000)
    source_label: str = Field(default="saved by you", max_length=200)
    verified: bool = True
    kind: str = "fact"


class FeedbackIn(BaseModel):
    message_id: str
    label: str


def _check_mode(mode: str | None) -> None:
    if mode is not None and mode not in {"off", "conservative", "balanced"}:
        raise HTTPException(status_code=400, detail="Unknown proactivity mode")


@app.get("/health")
def health() -> dict:
    settings = llm.model_settings()
    return {
        "status": "ok",
        "model_mode": llm.mode_name(),
        "model_name": settings["model"] if llm.available() else None,
    }


@app.get("/conversations")
def list_conversations() -> list[dict]:
    return db.list_conversations()


@app.post("/conversations")
def create_conversation(body: ConversationIn) -> dict:
    title = (body.title or "New conversation").strip() or "New conversation"
    return db.create_conversation(title)


@app.patch("/conversations/{conversation_id}")
async def patch_conversation(conversation_id: str, body: ConversationPatch) -> dict:
    _check_mode(body.proactivity_mode)
    if db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.proactivity_mode is not None:
        conversation = runtime.set_proactivity(conversation_id, body.proactivity_mode)
    else:
        conversation = db.get_conversation(conversation_id)
    if body.title is not None:
        conversation = db.update_conversation(conversation_id, title=body.title)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await runtime.hub.broadcast(
        conversation_id,
        runtime.envelope("conversation.updated", conversation_id, {"conversation": conversation}),
    )
    await runtime.emit_status(conversation_id)
    return conversation


@app.delete("/conversations/{conversation_id}")
def remove_conversation(conversation_id: str) -> dict:
    if not db.delete_conversation(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"ok": True}


@app.get("/conversations/{conversation_id}/messages")
def list_messages(conversation_id: str) -> list[dict]:
    if db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return db.list_messages(conversation_id)


@app.get("/conversations/{conversation_id}/thoughts")
def list_thoughts(conversation_id: str) -> list[dict]:
    if db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return db.list_thoughts(conversation_id)


@app.get("/conversations/{conversation_id}/memories")
def list_memories(conversation_id: str) -> list[dict]:
    if db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return db.list_memories(conversation_id)


@app.post("/conversations/{conversation_id}/memories")
async def create_memory(conversation_id: str, body: MemoryIn) -> dict:
    if db.get_conversation(conversation_id) is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.kind not in {"fact", "open_thread", "preference"}:
        raise HTTPException(status_code=400, detail="Unknown memory kind")
    memory = db.add_memory(
        conversation_id,
        kind=body.kind,
        content=body.content.strip(),
        source_label=body.source_label.strip() or "saved by you",
        verified=body.verified,
        confidence=0.8 if body.verified else 0.4,
    )
    await runtime.hub.broadcast(
        conversation_id,
        runtime.envelope("memory.updated", conversation_id, {"memory": memory}),
    )
    return memory


@app.delete("/memories/{memory_id}")
def remove_memory(memory_id: str) -> dict:
    if not db.delete_memory(memory_id):
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"ok": True}


@app.post("/feedback")
def feedback(body: FeedbackIn) -> dict:
    if body.label not in {"useful", "not_useful", "too_intrusive"}:
        raise HTTPException(status_code=400, detail="Unknown feedback label")
    message = db.add_feedback(body.message_id, body.label)
    if message is None:
        raise HTTPException(status_code=404, detail="Message not found")
    return message


@app.websocket("/ws/conversations/{conversation_id}")
async def conversation_socket(websocket: WebSocket, conversation_id: str) -> None:
    if db.get_conversation(conversation_id) is None:
        await websocket.close(code=4404)
        return
    await websocket.accept()
    runtime.hub.join(conversation_id, websocket)
    await websocket.send_json(runtime.envelope("system.status", conversation_id, runtime.status_payload(conversation_id)))
    try:
        while True:
            raw = await websocket.receive_json()
            event_type = raw.get("type")
            payload = raw.get("payload") or {}
            if event_type == "user.message":
                text = payload.get("text") if isinstance(payload, dict) else None
                if not isinstance(text, str) or not text.strip():
                    await websocket.send_json(
                        runtime.envelope("error", conversation_id, {"message": "Message text is required."})
                    )
                    continue
                await runtime.ingest_user_message(conversation_id, text)
            elif event_type == "user.typing":
                active = bool(payload.get("active")) if isinstance(payload, dict) else False
                runtime.engine.set_typing(conversation_id, active)
            elif event_type == "session.control":
                mode = payload.get("proactivity_mode") if isinstance(payload, dict) else None
                if mode not in {"off", "conservative", "balanced"}:
                    await websocket.send_json(
                        runtime.envelope("error", conversation_id, {"message": "Unknown proactivity mode."})
                    )
                    continue
                conversation = runtime.set_proactivity(conversation_id, mode)
                await runtime.hub.broadcast(
                    conversation_id,
                    runtime.envelope("conversation.updated", conversation_id, {"conversation": conversation}),
                )
                await runtime.emit_status(conversation_id)
            else:
                await websocket.send_json(
                    runtime.envelope("error", conversation_id, {"message": "Unknown event."})
                )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("websocket closed with an error")
    finally:
        runtime.hub.leave(conversation_id, websocket)
        runtime.engine.set_typing(conversation_id, False)
