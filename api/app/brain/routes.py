"""HTTP and WebSocket routes for the local Brain."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.brain import events, ingest, service
from app.brain.models import CATEGORIES, QUESTION_STATUSES

router = APIRouter(tags=["brain"])


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    questioning_paused: bool | None = None


class TeachIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    origin: str = "manual"


class MemoryPatch(BaseModel):
    content: str | None = Field(default=None, max_length=400)
    category: str | None = None
    stance: str | None = None
    status: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    supersedes_id: str | None = None


class ReviewIn(BaseModel):
    accept_ids: list[str] = Field(default_factory=list)
    reject_ids: list[str] = Field(default_factory=list)


class GenerateIn(BaseModel):
    refresh: bool = False


class AnswerIn(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)


def _missing(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"{name} not found")


def _bad(exc: Exception, code: int = 400) -> HTTPException:
    return HTTPException(status_code=code, detail=str(exc))


@router.get("/brain")
def get_brain() -> dict:
    return service.overview()


@router.patch("/brain")
async def patch_brain(body: ProfilePatch) -> dict:
    try:
        profile = service.update_profile(name=body.name, questioning_paused=body.questioning_paused)
    except service.BrainError as exc:
        raise _bad(exc) from exc
    await service.publish("profile")
    return profile


@router.delete("/brain")
async def clear_brain(confirm: bool = False) -> dict:
    if not confirm:
        raise HTTPException(status_code=400, detail="Pass confirm=true to clear the Brain.")
    profile = service.clear()
    await service.publish("clear")
    return {"ok": True, "profile": profile}


@router.post("/brain/summary")
async def refresh_summary() -> dict:
    payload = await service.refresh_summary()
    await service.publish("summary")
    return payload


@router.get("/brain/export")
def export_brain() -> dict:
    return service.export_data()


@router.post("/brain/teach")
async def teach(body: TeachIn) -> dict:
    origin = body.origin if body.origin in {"manual", "conversation"} else "manual"
    try:
        result = await service.ingest_text(body.text, source_type=origin, fallback_limit=8)
    except service.BrainError as exc:
        raise _bad(exc) from exc
    await service.publish("teach")
    return result


@router.post("/brain/upload")
async def upload(file: UploadFile = File(...)) -> dict:
    data = await file.read(ingest.MAX_BYTES + 1)
    try:
        result = await service.ingest_file(file.filename or "", file.content_type, data)
    except ingest.PayloadTooLarge as exc:
        raise _bad(exc, 413) from exc
    except (ingest.UnsupportedFormat, service.BrainError, ValueError) as exc:
        raise _bad(exc) from exc
    await service.publish("upload")
    return result


@router.get("/brain/memories")
def list_memories(
    status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = Query(default=300, ge=1, le=500),
) -> dict:
    if status == "all":
        status = None
    if category == "all":
        category = None
    if status is not None and status not in {"proposed", "accepted", "rejected", "superseded"}:
        raise HTTPException(status_code=400, detail="Unknown memory status")
    if category is not None and category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown category")
    return service.list_memories(status, category, q, limit)


@router.get("/brain/memories/{memory_id}")
def get_memory(memory_id: str) -> dict:
    try:
        return service._require_memory(memory_id)
    except KeyError:
        raise _missing("Memory") from None


@router.patch("/brain/memories/{memory_id}")
async def patch_memory(memory_id: str, body: MemoryPatch) -> dict:
    try:
        memory = service.edit_memory(memory_id, **body.model_dump(exclude_unset=True))
    except KeyError:
        raise _missing("Memory") from None
    except service.BrainError as exc:
        raise _bad(exc) from exc
    await service.publish("memory")
    return memory


@router.delete("/brain/memories/{memory_id}")
async def delete_memory(memory_id: str) -> dict:
    try:
        service.remove_memory(memory_id)
    except KeyError:
        raise _missing("Memory") from None
    await service.publish("memory")
    return {"ok": True}


@router.post("/brain/memories/{memory_id}/accept")
async def accept_memory(memory_id: str) -> dict:
    try:
        memory = service.set_status(memory_id, "accepted")
    except KeyError:
        raise _missing("Memory") from None
    await service.publish("memory")
    return memory


@router.post("/brain/memories/{memory_id}/reject")
async def reject_memory(memory_id: str) -> dict:
    try:
        memory = service.set_status(memory_id, "rejected")
    except KeyError:
        raise _missing("Memory") from None
    await service.publish("memory")
    return memory


@router.post("/brain/memories/review")
async def review_memories(body: ReviewIn) -> dict:
    result = service.review(body.accept_ids, body.reject_ids)
    await service.publish("memory")
    return result


@router.get("/brain/graph")
def brain_graph() -> dict:
    return service.graph()


@router.get("/brain/retrieve")
def retrieve(q: str = "", limit: int = Query(default=8, ge=1, le=50)) -> dict:
    profile = service.store.ensure_profile()
    memories = service.retrieve.retrieve_memories(profile["id"], q, limit=limit, status="accepted")
    return {"memories": memories, "query": q}


@router.get("/brain/sources/{source_id}")
def get_source(source_id: str) -> dict:
    profile = service.store.ensure_profile()
    source = service.store.get_source(source_id)
    if source is None or source["brain_id"] != profile["id"]:
        raise _missing("Source")
    return source


@router.get("/brain/questions")
def list_questions(status: str | None = "pending") -> dict:
    if status is not None and status not in QUESTION_STATUSES:
        raise HTTPException(status_code=400, detail="Unknown question status")
    if status == "pending" or status is None:
        return service.current_questions()
    profile = service.store.ensure_profile()
    return {"questions": service.store.list_questions(profile["id"], status=status), "paused": profile["questioning_paused"]}


@router.post("/brain/questions/generate")
async def generate_questions(body: GenerateIn) -> dict:
    result = await service.generate_questions(refresh=body.refresh)
    await service.publish("questions")
    return result


@router.post("/brain/questions/{question_id}/answer")
async def answer_question(question_id: str, body: AnswerIn) -> dict:
    try:
        result = await service.answer_question(question_id, body.text)
    except KeyError:
        raise _missing("Question") from None
    except service.BrainError as exc:
        raise _bad(exc) from exc
    await service.publish("questions")
    return result


@router.post("/brain/questions/{question_id}/skip")
async def skip_question(question_id: str) -> dict:
    try:
        result = await service.skip_question(question_id, "skipped")
    except KeyError:
        raise _missing("Question") from None
    except service.BrainError as exc:
        raise _bad(exc) from exc
    await service.publish("questions")
    return result


@router.post("/brain/questions/{question_id}/dismiss")
async def dismiss_question(question_id: str) -> dict:
    try:
        result = await service.skip_question(question_id, "dismissed")
    except KeyError:
        raise _missing("Question") from None
    except service.BrainError as exc:
        raise _bad(exc) from exc
    await service.publish("questions")
    return result


@router.websocket("/ws/brain")
async def brain_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    events.hub.join(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        events.hub.leave(websocket)
