"""SQLite storage for the local MVP. One process, one file."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

_lock = threading.Lock()
_connection: sqlite3.Connection | None = None
_path: str | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  proactivity_mode TEXT NOT NULL DEFAULT 'conservative',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  initiator TEXT NOT NULL,
  reason TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS thoughts (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  trigger_event_id TEXT,
  kind TEXT NOT NULL,
  draft TEXT,
  score_json TEXT NOT NULL,
  status TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  source_message_id TEXT,
  source_label TEXT,
  confidence REAL NOT NULL DEFAULT 0.5,
  verified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
  id TEXT PRIMARY KEY,
  message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_thoughts_conv ON thoughts(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_memories_conv ON memories(conversation_id, created_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id() -> str:
    return str(uuid.uuid4())


def db_path() -> Path:
    raw = os.environ.get("UNPROMPTED_DB")
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[1] / "data" / "unprompted.db"


def close_db() -> None:
    global _connection, _path
    with _lock:
        if _connection is not None:
            _connection.close()
        _connection = None
        _path = None


def _connect() -> sqlite3.Connection:
    global _connection, _path
    path = db_path()
    if _connection is not None and _path == str(path):
        return _connection
    if _connection is not None:
        _connection.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    _connection = connection
    _path = str(path)
    return connection


def init_db() -> None:
    with _lock:
        connection = _connect()
        connection.executescript(SCHEMA)
        connection.commit()
    from app.brain.store import ensure_schema

    ensure_schema()


def run_script(sql: str) -> None:
    with _lock:
        connection = _connect()
        connection.executescript(sql)
        connection.commit()


def fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    return _rows(sql, params)


def fetch_one(sql: str, params: tuple = ()) -> dict | None:
    return _one(sql, params)


def execute(sql: str, params: tuple = ()) -> None:
    _write(sql, params)


def transaction(work):
    """Run writes on one connection. `work` must not call back into db helpers."""
    with _lock:
        connection = _connect()
        try:
            result = work(connection)
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        cursor = _connect().execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]


def _one(sql: str, params: tuple = ()) -> dict | None:
    rows = _rows(sql, params)
    return rows[0] if rows else None


def _write(sql: str, params: tuple = ()) -> None:
    with _lock:
        connection = _connect()
        connection.execute(sql, params)
        connection.commit()


def _memory_out(row: dict) -> dict:
    row["verified"] = bool(row["verified"])
    return row


def _message_out(row: dict) -> dict:
    return row


def _thought_out(row: dict) -> dict:
    row["score"] = json.loads(row.pop("score_json") or "{}")
    return row


def list_conversations() -> list[dict]:
    return _rows("SELECT * FROM conversations ORDER BY updated_at DESC")


def get_conversation(conversation_id: str) -> dict | None:
    return _one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))


def create_conversation(title: str = "New conversation", proactivity_mode: str = "conservative") -> dict:
    conversation_id = new_id()
    timestamp = now()
    _write(
        """
        INSERT INTO conversations (id, title, proactivity_mode, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (conversation_id, title, proactivity_mode, timestamp, timestamp),
    )
    conversation = get_conversation(conversation_id)
    assert conversation is not None
    return conversation


def update_conversation(
    conversation_id: str,
    *,
    title: str | None = None,
    proactivity_mode: str | None = None,
) -> dict | None:
    current = get_conversation(conversation_id)
    if current is None:
        return None
    next_title = current["title"] if title is None else title.strip() or current["title"]
    next_mode = current["proactivity_mode"] if proactivity_mode is None else proactivity_mode
    _write(
        """
        UPDATE conversations
        SET title = ?, proactivity_mode = ?, updated_at = ?
        WHERE id = ?
        """,
        (next_title, next_mode, now(), conversation_id),
    )
    return get_conversation(conversation_id)


def delete_conversation(conversation_id: str) -> bool:
    if get_conversation(conversation_id) is None:
        return False
    _write("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    return True


def list_messages(conversation_id: str) -> list[dict]:
    rows = _rows(
        """
        SELECT m.*, (
            SELECT f.label FROM feedback f
            WHERE f.message_id = m.id
            ORDER BY f.created_at DESC
            LIMIT 1
        ) AS feedback_label
        FROM messages m
        WHERE m.conversation_id = ?
        ORDER BY m.created_at ASC, m.rowid ASC
        """,
        (conversation_id,),
    )
    return [_message_out(row) for row in rows]


def add_message(
    conversation_id: str,
    *,
    role: str,
    text: str,
    initiator: str,
    reason: str | None = None,
) -> dict:
    message_id = new_id()
    timestamp = now()
    _write(
        """
        INSERT INTO messages (id, conversation_id, role, text, initiator, reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (message_id, conversation_id, role, text, initiator, reason, timestamp),
    )
    _write("UPDATE conversations SET updated_at = ? WHERE id = ?", (timestamp, conversation_id))
    message = _one(
        """
        SELECT m.*, NULL AS feedback_label
        FROM messages m WHERE m.id = ?
        """,
        (message_id,),
    )
    assert message is not None
    return _message_out(message)


def list_thoughts(conversation_id: str, limit: int = 100) -> list[dict]:
    rows = _rows(
        """
        SELECT * FROM thoughts
        WHERE conversation_id = ?
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        (conversation_id, limit),
    )
    return [_thought_out(row) for row in rows]


def add_thought(
    conversation_id: str,
    *,
    trigger_event_id: str | None,
    kind: str,
    draft: str | None,
    score: dict,
    status: str,
    reason: str,
) -> dict:
    thought_id = new_id()
    _write(
        """
        INSERT INTO thoughts (
            id, conversation_id, trigger_event_id, kind, draft, score_json, status, reason, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            thought_id,
            conversation_id,
            trigger_event_id,
            kind,
            draft,
            json.dumps(score),
            status,
            reason,
            now(),
        ),
    )
    row = _one("SELECT * FROM thoughts WHERE id = ?", (thought_id,))
    assert row is not None
    return _thought_out(row)


def list_memories(conversation_id: str) -> list[dict]:
    rows = _rows(
        """
        SELECT * FROM memories
        WHERE conversation_id = ?
        ORDER BY created_at ASC, rowid ASC
        """,
        (conversation_id,),
    )
    return [_memory_out(row) for row in rows]


def add_memory(
    conversation_id: str,
    *,
    kind: str,
    content: str,
    source_label: str | None,
    verified: bool,
    confidence: float = 0.6,
    source_message_id: str | None = None,
) -> dict:
    memory_id = new_id()
    _write(
        """
        INSERT INTO memories (
            id, conversation_id, kind, content, source_message_id, source_label,
            confidence, verified, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            memory_id,
            conversation_id,
            kind,
            content,
            source_message_id,
            source_label,
            confidence,
            1 if verified else 0,
            now(),
        ),
    )
    row = _one("SELECT * FROM memories WHERE id = ?", (memory_id,))
    assert row is not None
    return _memory_out(row)


def find_memory(conversation_id: str, kind: str, content: str) -> dict | None:
    row = _one(
        """
        SELECT * FROM memories
        WHERE conversation_id = ? AND kind = ? AND content = ?
        """,
        (conversation_id, kind, content),
    )
    return _memory_out(row) if row else None


def delete_memory(memory_id: str) -> bool:
    if _one("SELECT id FROM memories WHERE id = ?", (memory_id,)) is None:
        return False
    _write("DELETE FROM memories WHERE id = ?", (memory_id,))
    return True


def add_event(conversation_id: str, event_type: str, payload: dict) -> dict:
    event_id = new_id()
    _write(
        """
        INSERT INTO events (id, conversation_id, type, payload_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, conversation_id, event_type, json.dumps(payload), now()),
    )
    return {"id": event_id, "conversation_id": conversation_id, "type": event_type, "payload": payload}


def add_feedback(message_id: str, label: str) -> dict | None:
    if _one("SELECT id FROM messages WHERE id = ?", (message_id,)) is None:
        return None
    _write(
        "INSERT INTO feedback (id, message_id, label, created_at) VALUES (?, ?, ?, ?)",
        (new_id(), message_id, label, now()),
    )
    row = _one(
        """
        SELECT m.*, (
            SELECT f.label FROM feedback f
            WHERE f.message_id = m.id
            ORDER BY f.created_at DESC
            LIMIT 1
        ) AS feedback_label
        FROM messages m WHERE m.id = ?
        """,
        (message_id,),
    )
    return _message_out(row) if row else None


def count_proactive_since(conversation_id: str, since_iso: str) -> int:
    row = _one(
        """
        SELECT COUNT(*) AS n FROM messages
        WHERE conversation_id = ? AND initiator = 'proactive' AND created_at >= ?
        """,
        (conversation_id, since_iso),
    )
    return int(row["n"]) if row else 0


def seconds_since(iso: str | None) -> float | None:
    if not iso:
        return None
    then = datetime.fromisoformat(iso)
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds()


def hour_ago() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")


def seed_example() -> None:
    if list_conversations():
        return
    conversation = create_conversation("Example: passer debate", "conservative")
    add_memory(
        conversation["id"],
        kind="fact",
        content="Jordan Hale averaged 9.1 assists per game in the 2025-26 season.",
        source_label="demo ledger",
        verified=True,
        confidence=0.9,
    )
