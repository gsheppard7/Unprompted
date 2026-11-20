"""Brain storage, extraction, questions, and the boundary with chat."""

from app.brain.extract import deterministic_extract, extract_answer, validate_extraction
from app.brain.ingest import MAX_BYTES, read_upload
from app.brain.questions import is_sensitive, rank_gaps
from app.brain.synthesize import compile_summary
from app.brain.textutil import same_memory


SAMPLE = (
    "I'm a computer science student interested in robotics, machine learning, and software engineering. "
    "I enjoy climbing and baseball."
)


def _contents(payload):
    return {item["content"] for item in payload["proposed"]}


def _accept_all(client, payload):
    review = client.post(
        "/brain/memories/review",
        json={"accept_ids": [item["id"] for item in payload["proposed"]], "reject_ids": []},
    )
    assert review.status_code == 200
    return review.json()["accepted"]


def test_fallback_splits_a_self_description_into_memories():
    memories = deterministic_extract(SAMPLE)
    contents = [memory.content for memory in memories]
    assert "User studies computer science." in contents
    assert "User is interested in robotics." in contents
    assert "User is interested in machine learning." in contents
    assert "User is interested in software engineering." in contents
    assert "User enjoys climbing." in contents
    assert "User enjoys baseball." in contents
    assert all(memory.stance == "explicit" for memory in memories)
    assert SAMPLE not in contents


def test_project_sentence_marks_inferences():
    memories = deterministic_extract("I'm working on a robotics project using Python and computer vision.")
    by_content = {memory.content: memory for memory in memories}
    assert by_content["User works on a robotics project."].stance == "explicit"
    assert by_content["Robotics Project uses Python."].stance == "explicit"
    assert by_content["Robotics Project uses Computer Vision."].stance == "explicit"
    related = by_content["Robotics Project is related to Computer Vision."]
    assert related.stance == "inferred"
    assert related.confidence < by_content["Robotics Project uses Python."].confidence


def test_extraction_schema_drops_invalid_items():
    parsed = validate_extraction(
        {
            "memories": [
                {"content": "", "category": "identity", "stance": "explicit"},
                {"content": "User studies painting.", "category": "nope", "stance": "explicit"},
                {"content": "User enjoys chess.", "category": "interests", "stance": "guess"},
                {
                    "content": "User enjoys chess.",
                    "category": "interests",
                    "stance": "explicit",
                    "evidence": "chess",
                    "entities": [{"name": "User", "entity_type": "person"}, {"name": "Chess", "entity_type": "interest"}],
                    "relationships": [
                        {"source": "User", "target": "Chess", "relationship_type": "enjoys", "stance": "explicit"}
                    ],
                },
            ]
        }
    )
    assert [memory.content for memory in parsed] == ["User studies painting.", "User enjoys chess."]
    chess = next(memory for memory in parsed if memory.content == "User enjoys chess.")
    assert any(memory.category == "other" and memory.content == "User studies painting." for memory in parsed)
    assert chess.relationships[0]["target"] == "Chess"


def test_summary_does_not_invent_a_person():
    empty = compile_summary([])
    assert empty["accepted_count"] == 0
    assert "invent" in empty["text"].lower() or "No accepted" in empty["text"]
    compiled = compile_summary(
        [{"status": "accepted", "category": "education", "stance": "explicit", "content": "User studies computer science."}]
    )
    assert "computer science" in compiled["text"]
    assert "No accepted goals." in compiled["uncertainties"]


def test_questions_prefer_gaps_over_sensitive_topics():
    memories = [
        {"id": "1", "status": "accepted", "category": "education", "stance": "explicit", "content": "User studies computer science."},
        {"id": "2", "status": "accepted", "category": "interests", "stance": "explicit", "content": "User is interested in robotics."},
        {"id": "3", "status": "accepted", "category": "interests", "stance": "explicit", "content": "User is interested in machine learning."},
        {"id": "4", "status": "accepted", "category": "interests", "stance": "explicit", "content": "User is interested in software engineering."},
        {"id": "5", "status": "accepted", "category": "interests", "stance": "explicit", "content": "User enjoys climbing."},
        {"id": "6", "status": "accepted", "category": "interests", "stance": "explicit", "content": "User enjoys baseball."},
    ]
    gaps = rank_gaps(memories)
    assert [gap.key for gap in gaps[:3]] == [
        "aspect:robotics",
        "aspect:machine learning",
        "project:robotics",
    ]
    assert all(not is_sensitive(gap.question) for gap in gaps)
    assert is_sensitive("What religion do you practice?")


def test_duplicate_wording_matches():
    assert same_memory("User enjoys climbing.", "User enjoys climbing")
    assert not same_memory("User enjoys climbing.", "User enjoys baseball.")


def test_teach_review_edit_and_delete(client):
    taught = client.post("/brain/teach", json={"text": SAMPLE})
    assert taught.status_code == 200
    body = taught.json()
    assert body["extractor"] == "fallback"
    assert "not sent" in body["notice"].lower()
    assert "User studies computer science." in _contents(body)
    assert len(body["proposed"]) >= 6

    again = client.post("/brain/teach", json={"text": SAMPLE})
    assert again.status_code == 200
    assert again.json()["proposed"] == []
    assert len(again.json()["duplicates"]) >= 6
    assert again.json()["duplicates"][0]["matched_status"] == "proposed"

    accepted = _accept_all(client, body)
    assert all(item["status"] == "accepted" for item in accepted)
    study = next(item for item in accepted if item["content"] == "User studies computer science.")
    source_id = study["source_id"]
    source = client.get(f"/brain/sources/{source_id}")
    assert source.status_code == 200
    assert "computer science student" in source.json()["original_text"]

    edited = client.patch(
        f"/brain/memories/{study['id']}",
        json={"content": "User studies computer science and mathematics.", "valid_from": "2024-09-01"},
    )
    assert edited.status_code == 200
    assert edited.json()["content"].endswith("mathematics.")
    assert edited.json()["valid_from"] == "2024-09-01"
    source_after = client.get(f"/brain/sources/{source_id}").json()
    assert source_after["original_text"] == source.json()["original_text"]

    listed = client.get("/brain/memories", params={"status": "accepted", "category": "education"})
    assert listed.status_code == 200
    assert any("mathematics" in item["content"] for item in listed.json()["memories"])

    assert client.delete(f"/brain/memories/{study['id']}").status_code == 200
    remaining = client.get("/brain/memories", params={"status": "accepted"}).json()["memories"]
    assert all(item["id"] != study["id"] for item in remaining)
    assert client.get(f"/brain/sources/{source_id}").status_code == 200

    missing = client.post("/brain/memories/review", json={"accept_ids": ["missing-id"], "reject_ids": []})
    assert missing.status_code == 200
    assert missing.json()["missing"] == ["missing-id"]


def test_upload_accepts_markdown_and_rejects_other_files(client):
    uploaded = client.post(
        "/brain/upload",
        files={"file": ("notes.md", b"I enjoy climbing and baseball.", "text/markdown")},
    )
    assert uploaded.status_code == 200
    assert "User enjoys climbing." in _contents(uploaded.json())

    rejected = client.post(
        "/brain/upload",
        files={"file": ("secret.exe", b"not a document", "application/octet-stream")},
    )
    assert rejected.status_code == 400

    huge = client.post(
        "/brain/upload",
        files={"file": ("notes.txt", b"a" * (MAX_BYTES + 1), "text/plain")},
    )
    assert huge.status_code == 413

    pdf = _pdf("I enjoy climbing and baseball.")
    parsed = read_upload("notes.pdf", "application/pdf", pdf)
    assert "climbing" in parsed[0]
    uploaded_pdf = client.post("/brain/upload", files={"file": ("notes.pdf", pdf, "application/pdf")})
    assert uploaded_pdf.status_code == 200
    pdf_body = uploaded_pdf.json()
    seen = [item["content"] for item in pdf_body["proposed"]] + [item["matched_content"] for item in pdf_body["duplicates"]]
    assert any("climbing" in item.lower() for item in seen)


def test_questions_are_not_repeated_and_can_be_paused(client):
    taught = client.post("/brain/teach", json={"text": SAMPLE})
    _accept_all(client, taught.json())
    generated = client.post("/brain/questions/generate", json={})
    assert generated.status_code == 200
    body = generated.json()
    assert body["paused"] is False
    assert len(body["questions"]) == 3
    assert body["generator"] == "fallback"
    first_ids = {item["id"] for item in body["questions"]}
    first_text = {item["question_text"] for item in body["questions"]}
    assert "What aspect of robotics interests you the most?" in first_text

    again = client.post("/brain/questions/generate", json={})
    assert {item["id"] for item in again.json()["questions"]} == first_ids

    aspect = next(item for item in body["questions"] if item["question_text"].startswith("What aspect of robotics"))
    answered = client.post(f"/brain/questions/{aspect['id']}/answer", json={"text": "Control systems"})
    assert answered.status_code == 200
    assert any("control systems" in item["content"].lower() for item in answered.json()["proposed"])
    assert answered.json()["question"]["status"] == "answered"

    pending = client.get("/brain/questions").json()["questions"]
    assert all(item["id"] != aspect["id"] for item in pending)
    skipped = pending[0]
    dismissed = pending[1]
    skip = client.post(f"/brain/questions/{skipped['id']}/skip")
    dismiss = client.post(f"/brain/questions/{dismissed['id']}/dismiss")
    assert skip.status_code == 200
    assert dismiss.status_code == 200
    visible = {item["question_text"] for item in client.get("/brain/questions").json()["questions"]}
    assert skipped["question_text"] not in visible
    assert dismissed["question_text"] not in visible
    assert aspect["question_text"] not in visible

    paused = client.patch("/brain", json={"questioning_paused": True})
    assert paused.status_code == 200
    quiet = client.post("/brain/questions/generate", json={"refresh": True})
    assert quiet.json()["paused"] is True
    assert quiet.json()["questions"] == []
    client.patch("/brain", json={"questioning_paused": False})
    resumed = client.get("/brain/questions").json()
    assert resumed["paused"] is False


def test_graph_distinguishes_inferred_edges(client):
    taught = client.post(
        "/brain/teach",
        json={"text": "I'm working on a robotics project using Python and computer vision."},
    )
    _accept_all(client, taught.json())
    graph = client.get("/brain/graph")
    assert graph.status_code == 200
    edges = graph.json()["edges"]
    inferred = [edge for edge in edges if edge["stance"] == "inferred"]
    explicit = [edge for edge in edges if edge["stance"] == "explicit"]
    assert inferred
    assert explicit
    assert any(edge["label"] == "related to" for edge in inferred)
    assert any(edge["label"] == "uses" for edge in explicit)
    assert any(node["is_self"] for node in graph.json()["nodes"])


def test_retrieval_uses_only_accepted_memories(client):
    taught = client.post("/brain/teach", json={"text": "I enjoy climbing and baseball."})
    proposed = taught.json()["proposed"]
    before = client.get("/brain/retrieve", params={"q": "climbing"})
    assert before.json()["memories"] == []
    climbing = next(item for item in proposed if "climbing" in item["content"])
    client.post(f"/brain/memories/{climbing['id']}/accept")
    found = client.get("/brain/retrieve", params={"q": "climbing"}).json()["memories"]
    assert found and found[0]["id"] == climbing["id"]
    assert found[0]["source"]["label"]
    assert found[0]["stance"] == "explicit"
    baseball = next(item for item in proposed if "baseball" in item["content"])
    client.post(f"/brain/memories/{baseball['id']}/reject")
    rejected = client.get("/brain/retrieve", params={"q": "baseball"}).json()["memories"]
    assert rejected == []


def test_overview_counts_and_export_and_clear_keep_chat(client):
    conversation = client.post("/conversations", json={"title": "Keep me"}).json()
    client.post(
        f"/conversations/{conversation['id']}/memories",
        json={"content": "Only in the conversation.", "source_label": "saved by you", "verified": True, "kind": "fact"},
    )
    taught = client.post("/brain/teach", json={"text": SAMPLE, "origin": "conversation"})
    assert taught.json()["proposed"][0]["source"]["source_type"] == "conversation"
    _accept_all(client, taught.json())
    overview = client.get("/brain")
    assert overview.status_code == 200
    body = overview.json()
    assert body["counts"]["accepted"] >= 6
    assert body["counts"]["relationships"] >= 1
    assert "computer science" in body["summary"]["text"]
    assert body["summary"]["basis"] == "accepted memories"
    assert body["extractor"] == "fallback"

    exported = client.get("/brain/export")
    assert exported.status_code == 200
    assert exported.json()["memories"]
    assert exported.json()["sources"][0]["original_text"]

    conversation_notes = client.get(f"/conversations/{conversation['id']}/memories").json()
    assert any(item["content"] == "Only in the conversation." for item in conversation_notes)
    brain_notes = client.get("/brain/memories").json()["memories"]
    assert all(item["content"] != "Only in the conversation." for item in brain_notes)

    cleared = client.delete("/brain", params={"confirm": True})
    assert cleared.status_code == 200
    assert client.delete("/brain").status_code == 400
    after = client.get("/brain/memories").json()["memories"]
    assert after == []
    assert client.get(f"/conversations/{conversation['id']}/messages").status_code == 200
    assert client.get(f"/conversations/{conversation['id']}/memories").json()


def test_chat_can_use_an_accepted_brain_fact_and_not_a_proposal(client):
    taught = client.post(
        "/brain/teach",
        json={"text": "Jordan Hale averaged 9.1 assists per game in the 2025-26 season."},
    )
    proposed = taught.json()["proposed"]
    assert proposed
    conversation = client.post("/conversations", json={"title": "Brain chat", "proactivity_mode": "off"}).json()
    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as ws:
        ws.send_json({"type": "user.message", "payload": {"text": "What did Jordan Hale average?"}})
        _until_settled(ws, "Jordan Hale")
    before = client.get(f"/conversations/{conversation['id']}/messages").json()
    assert "9.1" not in before[-1]["text"]

    client.post(f"/brain/memories/{proposed[0]['id']}/accept")
    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as ws:
        ws.send_json({"type": "user.message", "payload": {"text": "What did Jordan Hale average?"}})
        _until_settled(ws, "What did Jordan Hale")
    after = client.get(f"/conversations/{conversation['id']}/messages").json()
    assert "9.1" in after[-1]["text"]
    assert "Brain" in after[-1]["text"]


def test_local_reply_can_use_a_short_brain_fact(client):
    taught = client.post("/brain/teach", json={"text": "I enjoy climbing and baseball."})
    climbing = next(item for item in taught.json()["proposed"] if "climbing" in item["content"])
    assert client.post(f"/brain/memories/{climbing['id']}/accept").status_code == 200
    conversation = client.post("/conversations", json={"title": "Climbing"}).json()
    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as ws:
        ws.send_json({"type": "user.message", "payload": {"text": "What do you know about my climbing?"}})
        _until_settled(ws, "climbing")
    messages = client.get(f"/conversations/{conversation['id']}/messages").json()
    assert "climbing" in messages[-1]["text"].lower()
    assert "Brain" in messages[-1]["text"]


def test_self_description_offers_a_save_without_storing(client):
    conversation = client.post("/conversations", json={"title": "Offer"}).json()
    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as ws:
        ws.send_json(
            {
                "type": "user.message",
                "payload": {"text": "I'm a computer science student interested in robotics."},
            }
        )
        events = _until_settled(ws, "computer science")
    assert any(event["type"] == "brain.suggestion" for event in events)
    memories = client.get("/brain/memories").json()["memories"]
    assert memories == []


def test_answer_patterns_do_not_drop_short_replies():
    memories = extract_answer("What aspect of robotics interests you the most?", "Control systems")
    assert any("control systems" in memory.content.lower() and "robotics" in memory.content.lower() for memory in memories)


def test_model_extraction_is_used_when_configured(client, monkeypatch):
    async def fake_json(*_args, **_kwargs):
        return {
            "memories": [
                {
                    "content": "User enjoys chess.",
                    "category": "interests",
                    "stance": "explicit",
                    "evidence": "chess",
                    "entities": [
                        {"name": "User", "entity_type": "person"},
                        {"name": "Chess", "entity_type": "interest"},
                    ],
                    "relationships": [
                        {"source": "User", "target": "Chess", "relationship_type": "enjoys", "stance": "explicit"}
                    ],
                }
            ]
        }

    monkeypatch.setattr("app.llm.available", lambda: True)
    monkeypatch.setattr("app.llm.complete_json", fake_json)
    monkeypatch.setattr("app.llm.model_settings", lambda: {"model": "test-model", "base_url": "https://example.test/v1", "api_key": "secret"})
    taught = client.post("/brain/teach", json={"text": "I enjoy chess."})
    assert taught.status_code == 200
    body = taught.json()
    assert body["extractor"] == "model"
    assert "example.test" in body["notice"]
    assert "secret" not in body["notice"]
    assert _contents(body) == {"User enjoys chess."}


def _until_settled(ws, needle: str):
    seen_user = False
    events = []
    for _ in range(16):
        event = ws.receive_json()
        events.append(event)
        message = (event.get("payload") or {}).get("message") or {}
        if event["type"] == "message.created" and needle in message.get("text", ""):
            seen_user = True
        state = (event.get("payload") or {}).get("state")
        if seen_user and event["type"] == "system.status" and state in {"listening", "paused"}:
            return events
    raise AssertionError(f"turn did not settle: {events}")


def _pdf(text: str) -> bytes:
    safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 18 Tf 72 720 Td ({safe}) Tj ET\n".encode()
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [3 0 R] >>\nendobj\n",
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n",
        f"4 0 obj\n<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream\nendobj\n",
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    header = b"%PDF-1.4\n"
    cursor = len(header)
    chunks = [header]
    offsets = [0]
    for item in objects:
        offsets.append(cursor)
        chunks.append(item)
        cursor += len(item)
    xref = [f"xref\n0 {len(offsets)}\n", "0000000000 65535 f \n"]
    for offset in offsets[1:]:
        xref.append(f"{offset:010d} 00000 n \n")
    trailer = f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{cursor}\n%%EOF\n"
    return b"".join(chunks) + "".join(xref).encode() + trailer.encode()

