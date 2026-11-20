def _create(client, mode="conservative"):
    response = client.post("/conversations", json={"title": "Test"})
    assert response.status_code == 200
    conversation = response.json()
    if mode != "conservative":
        patched = client.patch(
            f"/conversations/{conversation['id']}",
            json={"proactivity_mode": mode},
        )
        assert patched.status_code == 200
        conversation = patched.json()
    return conversation


def _memory(client, conversation_id, content="Jordan Hale averaged 9.1 assists per game in the 2025-26 season."):
    response = client.post(
        f"/conversations/{conversation_id}/memories",
        json={"content": content, "source_label": "demo ledger", "verified": True, "kind": "fact"},
    )
    assert response.status_code == 200
    return response.json()


def _until_settled(ws, needle: str):
    seen_user = False
    events = []
    for _ in range(12):
        event = ws.receive_json()
        events.append(event)
        message = (event.get("payload") or {}).get("message") or {}
        if event["type"] == "message.created" and needle in message.get("text", ""):
            seen_user = True
        state = (event.get("payload") or {}).get("state")
        if seen_user and event["type"] == "system.status" and state in {"listening", "paused"}:
            return events
    raise AssertionError(f"turn did not settle: {events}")


def test_health_reports_demo_mode_without_a_key(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_mode"] == "demo"


def test_messages_survive_a_reload(client):
    conversation = _create(client)
    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as ws:
        ws.send_json({"type": "user.message", "payload": {"text": "What did Jordan Hale average?"}})
        _until_settled(ws, "Jordan Hale")
    messages = client.get(f"/conversations/{conversation['id']}/messages").json()
    assert [message["role"] for message in messages] == ["user", "assistant"]
    assert messages[1]["initiator"] == "reply"


def test_statement_can_draw_a_sourced_correction(client):
    conversation = _create(client)
    _memory(client, conversation["id"])
    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as ws:
        ws.send_json(
            {
                "type": "user.message",
                "payload": {"text": "I think Jordan Hale was the best passer in the league last year."},
            }
        )
        first = _until_settled(ws, "best passer")
        assert not any(event["type"] == "assistant.proactive" for event in first)

        ws.send_json(
            {"type": "user.message", "payload": {"text": "No way, he barely averaged five assists."}}
        )
        second = _until_settled(ws, "five assists")
    proactive = [event for event in second if event["type"] == "assistant.proactive"]
    assert len(proactive) == 1
    text = proactive[0]["payload"]["message"]["text"]
    assert "9.1" in text
    assert "haven't checked a live source" in text
    thoughts = client.get(f"/conversations/{conversation['id']}/thoughts").json()
    assert any(thought["status"] == "delivered" for thought in thoughts)
    assert any(thought["status"] == "ignored" for thought in thoughts)


def test_off_blocks_spontaneous_messages(client):
    conversation = _create(client, mode="off")
    _memory(client, conversation["id"])
    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as ws:
        ws.send_json({"type": "user.message", "payload": {"text": "Jordan Hale averaged 5 assists."}})
        events = _until_settled(ws, "averaged 5")
    assert not any(event["type"] == "assistant.proactive" for event in events)
    messages = client.get(f"/conversations/{conversation['id']}/messages").json()
    assert messages[-1]["initiator"] == "reply"
    assert "9.1" in messages[-1]["text"]


def test_deleted_memory_is_not_used(client):
    conversation = _create(client)
    memory = _memory(client, conversation["id"])
    assert client.delete(f"/memories/{memory['id']}").status_code == 200
    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as ws:
        ws.send_json({"type": "user.message", "payload": {"text": "Jordan Hale averaged 5 assists."}})
        events = _until_settled(ws, "averaged 5")
    assert not any(event["type"] == "assistant.proactive" for event in events)


def test_feedback_sticks_to_the_message(client):
    conversation = _create(client)
    _memory(client, conversation["id"])
    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as ws:
        ws.send_json({"type": "user.message", "payload": {"text": "Jordan Hale averaged 5 assists."}})
        events = _until_settled(ws, "averaged 5")
    message = next(event["payload"]["message"] for event in events if event["type"] == "assistant.proactive")
    saved = client.post("/feedback", json={"message_id": message["id"], "label": "useful"})
    assert saved.status_code == 200
    assert saved.json()["feedback_label"] == "useful"


def test_failed_model_call_still_answers(client, monkeypatch):
    async def explode(*_args, **_kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr("app.llm.available", lambda: True)
    monkeypatch.setattr("app.llm.complete", explode)
    conversation = _create(client)
    _memory(client, conversation["id"])
    with client.websocket_connect(f"/ws/conversations/{conversation['id']}") as ws:
        ws.send_json({"type": "user.message", "payload": {"text": "What did Jordan Hale average?"}})
        _until_settled(ws, "What did")
    messages = client.get(f"/conversations/{conversation['id']}/messages").json()
    assert "9.1" in messages[-1]["text"]
    assert messages[-1]["initiator"] == "reply"
