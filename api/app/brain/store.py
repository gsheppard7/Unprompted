"""SQLite persistence for the Brain.

Tables are additive. Conversation memories stay in `memories`. A later
PostgreSQL port can keep these operations behind the same function names.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from app import db
from app.brain.models import CONFIDENCE_NOTE
from app.brain.textutil import same_memory

logger = logging.getLogger(__name__)

BRAIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  id TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brain_profiles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  questioning_paused INTEGER NOT NULL DEFAULT 0,
  summary TEXT,
  summary_mode TEXT,
  summary_updated_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brain_sources (
  id TEXT PRIMARY KEY,
  brain_id TEXT NOT NULL REFERENCES brain_profiles(id) ON DELETE CASCADE,
  source_type TEXT NOT NULL,
  original_text TEXT,
  filename TEXT,
  mime_type TEXT,
  byte_size INTEGER,
  extract_note TEXT,
  question_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brain_memories (
  id TEXT PRIMARY KEY,
  brain_id TEXT NOT NULL REFERENCES brain_profiles(id) ON DELETE CASCADE,
  content TEXT NOT NULL,
  category TEXT NOT NULL,
  status TEXT NOT NULL,
  stance TEXT NOT NULL,
  confidence REAL NOT NULL,
  source_id TEXT REFERENCES brain_sources(id) ON DELETE SET NULL,
  evidence_text TEXT,
  extraction_json TEXT,
  supersedes_id TEXT,
  valid_from TEXT,
  valid_to TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brain_entities (
  id TEXT PRIMARY KEY,
  brain_id TEXT NOT NULL REFERENCES brain_profiles(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (brain_id, name, entity_type)
);

CREATE TABLE IF NOT EXISTS brain_relationships (
  id TEXT PRIMARY KEY,
  brain_id TEXT NOT NULL REFERENCES brain_profiles(id) ON DELETE CASCADE,
  source_entity_id TEXT NOT NULL REFERENCES brain_entities(id) ON DELETE CASCADE,
  target_entity_id TEXT NOT NULL REFERENCES brain_entities(id) ON DELETE CASCADE,
  relationship_type TEXT NOT NULL,
  source_memory_id TEXT REFERENCES brain_memories(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  stance TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brain_questions (
  id TEXT PRIMARY KEY,
  brain_id TEXT NOT NULL REFERENCES brain_profiles(id) ON DELETE CASCADE,
  question_text TEXT NOT NULL,
  status TEXT NOT NULL,
  motivation TEXT,
  gap_key TEXT,
  usefulness REAL NOT NULL DEFAULT 0,
  generator TEXT NOT NULL DEFAULT 'fallback',
  answer_text TEXT,
  created_at TEXT NOT NULL,
  answered_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brain_changes (
  id TEXT PRIMARY KEY,
  brain_id TEXT NOT NULL REFERENCES brain_profiles(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  detail TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_brain_memories_status ON brain_memories(brain_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_brain_questions_status ON brain_questions(brain_id, status);
CREATE INDEX IF NOT EXISTS idx_brain_changes_time ON brain_changes(brain_id, created_at);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS brain_memory_fts USING fts5(
  memory_id UNINDEXED,
  brain_id UNINDEXED,
  status UNINDEXED,
  content,
  category
);
"""

_fts = False

SOURCE_LABELS = {
    "manual": "Typed note",
    "upload": "Upload",
    "question_answer": "Answer to a Brain question",
    "conversation": "Conversation",
}


def fts_enabled() -> bool:
    return _fts


def ensure_schema() -> None:
    global _fts
    db.run_script(BRAIN_SCHEMA)
    try:
        db.run_script(FTS_SCHEMA)
        _fts = True
    except sqlite3.OperationalError:
        logger.warning("FTS5 is unavailable; Brain search will use token overlap.")
        _fts = False
    db.execute(
        "INSERT OR IGNORE INTO schema_migrations (id, applied_at) VALUES (?, ?)",
        ("brain_001", db.now()),
    )


def _rows(cursor: sqlite3.Cursor) -> list[dict]:
    return [dict(row) for row in cursor.fetchall()]


def _memory_from_row(row: dict) -> dict:
    raw = {}
    try:
        raw = json.loads(row.get("extraction_json") or "{}") or {}
    except json.JSONDecodeError:
        raw = {}
    source_id = row.get("source_id")
    source_type = row.get("source_type")
    filename = row.get("filename")
    label = SOURCE_LABELS.get(source_type or "", "Brain")
    if source_type == "upload" and filename:
        label = filename
    preview = row.get("original_text") or ""
    preview = " ".join(preview.split())
    if len(preview) > 240:
        preview = preview[:239].rstrip() + "…"
    return {
        "id": row["id"],
        "brain_id": row["brain_id"],
        "content": row["content"],
        "category": row["category"],
        "status": row["status"],
        "stance": row["stance"],
        "confidence": row["confidence"],
        "confidence_note": CONFIDENCE_NOTE,
        "source_id": source_id,
        "evidence_text": row.get("evidence_text"),
        "valid_from": row.get("valid_from"),
        "valid_to": row.get("valid_to"),
        "supersedes_id": row.get("supersedes_id"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "entities": raw.get("entities") or [],
        "relationships": raw.get("relationships") or [],
        "source": {
            "id": source_id,
            "source_type": source_type,
            "filename": filename,
            "label": label,
            "created_at": row.get("source_created_at"),
            "preview": preview,
        },
    }


_MEMORY_SELECT = """
SELECT m.*, s.source_type, s.filename,
       substr(s.original_text, 1, 240) AS original_text,
       s.created_at AS source_created_at
FROM brain_memories m
LEFT JOIN brain_sources s ON s.id = m.source_id
"""


def _profile_out(row: dict) -> dict:
    row["questioning_paused"] = bool(row["questioning_paused"])
    return row


def get_profile() -> dict | None:
    row = db.fetch_one("SELECT * FROM brain_profiles ORDER BY created_at ASC LIMIT 1")
    return _profile_out(row) if row else None


def ensure_profile() -> dict:
    current = get_profile()
    if current:
        return current
    profile_id = db.new_id()
    timestamp = db.now()
    db.execute(
        """
        INSERT INTO brain_profiles (
            id, name, questioning_paused, summary, summary_mode, summary_updated_at, created_at, updated_at
        ) VALUES (?, ?, 0, NULL, NULL, NULL, ?, ?)
        """,
        (profile_id, "You", timestamp, timestamp),
    )
    created = get_profile()
    assert created is not None
    return created


def update_profile(brain_id: str, **fields) -> dict | None:
    current = db.fetch_one("SELECT * FROM brain_profiles WHERE id = ?", (brain_id,))
    if current is None:
        return None
    allowed = ("name", "questioning_paused", "summary", "summary_mode", "summary_updated_at")
    for key in allowed:
        if key in fields:
            value = fields[key]
            if key == "questioning_paused" and value is not None:
                value = 1 if value else 0
            current[key] = value
    db.execute(
        """
        UPDATE brain_profiles
        SET name = ?, questioning_paused = ?, summary = ?, summary_mode = ?,
            summary_updated_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            current["name"],
            current["questioning_paused"],
            current["summary"],
            current["summary_mode"],
            current["summary_updated_at"],
            db.now(),
            brain_id,
        ),
    )
    row = db.fetch_one("SELECT * FROM brain_profiles WHERE id = ?", (brain_id,))
    return _profile_out(row) if row else None


def touch_profile(connection: sqlite3.Connection, brain_id: str) -> None:
    connection.execute(
        "UPDATE brain_profiles SET updated_at = ?, summary = NULL, summary_mode = NULL WHERE id = ?",
        (db.now(), brain_id),
    )


def add_source(
    brain_id: str,
    *,
    source_type: str,
    original_text: str,
    filename: str | None = None,
    mime_type: str | None = None,
    byte_size: int | None = None,
    extract_note: str | None = None,
    question_id: str | None = None,
) -> dict:
    source_id = db.new_id()
    timestamp = db.now()
    db.execute(
        """
        INSERT INTO brain_sources (
            id, brain_id, source_type, original_text, filename, mime_type,
            byte_size, extract_note, question_id, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            brain_id,
            source_type,
            original_text,
            filename,
            mime_type,
            byte_size,
            extract_note,
            question_id,
            timestamp,
        ),
    )
    row = get_source(source_id)
    assert row is not None
    return row


def get_source(source_id: str) -> dict | None:
    return db.fetch_one("SELECT * FROM brain_sources WHERE id = ?", (source_id,))


def list_sources(brain_id: str) -> list[dict]:
    return db.fetch_all(
        """
        SELECT id, brain_id, source_type, filename, mime_type, byte_size, extract_note,
               question_id, created_at, substr(original_text, 1, 240) AS preview
        FROM brain_sources
        WHERE brain_id = ?
        ORDER BY created_at DESC
        """,
        (brain_id,),
    )


def _fts_write(connection: sqlite3.Connection, memory_id: str, brain_id: str, status: str, content: str, category: str) -> None:
    if not _fts:
        return
    connection.execute("DELETE FROM brain_memory_fts WHERE memory_id = ?", (memory_id,))
    connection.execute(
        """
        INSERT INTO brain_memory_fts (memory_id, brain_id, status, content, category)
        VALUES (?, ?, ?, ?, ?)
        """,
        (memory_id, brain_id, status, content, category),
    )


def _entity_id(connection: sqlite3.Connection, brain_id: str, name: str, entity_type: str) -> str:
    found = connection.execute(
        """
        SELECT id FROM brain_entities
        WHERE brain_id = ? AND name = ? AND entity_type = ?
        """,
        (brain_id, name, entity_type),
    ).fetchone()
    if found:
        return found["id"]
    entity_id = db.new_id()
    connection.execute(
        """
        INSERT INTO brain_entities (id, brain_id, name, entity_type, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (entity_id, brain_id, name, entity_type, db.now()),
    )
    return entity_id


def add_memory(
    brain_id: str,
    *,
    content: str,
    category: str,
    stance: str,
    confidence: float,
    source_id: str | None,
    evidence_text: str | None,
    entities: list[dict],
    relationships: list[dict],
    status: str = "proposed",
) -> dict:
    memory_id = db.new_id()
    timestamp = db.now()
    payload = json.dumps({"entities": entities, "relationships": relationships})

    def work(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            INSERT INTO brain_memories (
                id, brain_id, content, category, status, stance, confidence, source_id,
                evidence_text, extraction_json, supersedes_id, valid_from, valid_to,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
            """,
            (
                memory_id,
                brain_id,
                content,
                category,
                status,
                stance,
                confidence,
                source_id,
                evidence_text,
                payload,
                timestamp,
                timestamp,
            ),
        )
        entity_ids: dict[tuple[str, str], str] = {}
        for entity in entities:
            key = (entity["name"], entity["entity_type"])
            entity_ids[key] = _entity_id(connection, brain_id, entity["name"], entity["entity_type"])
        by_name: dict[str, str] = {}
        for (name, _kind), entity_id in entity_ids.items():
            by_name.setdefault(name, entity_id)
        for rel in relationships:
            source_entity = by_name.get(rel["source"])
            target_entity = by_name.get(rel["target"])
            if not source_entity or not target_entity or source_entity == target_entity:
                continue
            connection.execute(
                """
                INSERT INTO brain_relationships (
                    id, brain_id, source_entity_id, target_entity_id, relationship_type,
                    source_memory_id, status, stance, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    db.new_id(),
                    brain_id,
                    source_entity,
                    target_entity,
                    rel["relationship_type"],
                    memory_id,
                    status,
                    rel.get("stance") or stance,
                    timestamp,
                ),
            )
        _fts_write(connection, memory_id, brain_id, status, content, category)
        touch_profile(connection, brain_id)

    db.transaction(work)
    memory = get_memory(memory_id)
    assert memory is not None
    return memory


def get_memory(memory_id: str) -> dict | None:
    row = db.fetch_one(_MEMORY_SELECT + " WHERE m.id = ?", (memory_id,))
    return _memory_from_row(row) if row else None


def list_memories(
    brain_id: str,
    *,
    status: str | None = None,
    category: str | None = None,
    limit: int = 300,
) -> list[dict]:
    clauses = ["m.brain_id = ?"]
    params: list = [brain_id]
    if status:
        clauses.append("m.status = ?")
        params.append(status)
    if category:
        clauses.append("m.category = ?")
        params.append(category)
    params.append(limit)
    rows = db.fetch_all(
        _MEMORY_SELECT + f" WHERE {' AND '.join(clauses)} ORDER BY m.created_at ASC, m.rowid ASC LIMIT ?",
        tuple(params),
    )
    return [_memory_from_row(row) for row in rows]


def find_similar(brain_id: str, content: str) -> dict | None:
    rows = list_memories(brain_id, limit=2000)
    for row in rows:
        if row["status"] == "superseded":
            continue
        if same_memory(content, row["content"]):
            return row
    return None


def update_memory(memory_id: str, **fields) -> dict | None:
    current = db.fetch_one("SELECT * FROM brain_memories WHERE id = ?", (memory_id,))
    if current is None:
        return None
    for key in ("content", "category", "status", "stance", "confidence", "valid_from", "valid_to", "supersedes_id"):
        if key in fields:
            current[key] = fields[key]
    timestamp = db.now()

    def work(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            UPDATE brain_memories
            SET content = ?, category = ?, status = ?, stance = ?, confidence = ?,
                valid_from = ?, valid_to = ?, supersedes_id = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                current["content"],
                current["category"],
                current["status"],
                current["stance"],
                current["confidence"],
                current["valid_from"],
                current["valid_to"],
                current["supersedes_id"],
                timestamp,
                memory_id,
            ),
        )
        if "status" in fields:
            connection.execute(
                "UPDATE brain_relationships SET status = ? WHERE source_memory_id = ?",
                (current["status"], memory_id),
            )
        _fts_write(
            connection,
            memory_id,
            current["brain_id"],
            current["status"],
            current["content"],
            current["category"],
        )
        touch_profile(connection, current["brain_id"])

    db.transaction(work)
    return get_memory(memory_id)


def delete_memory(memory_id: str) -> bool:
    current = db.fetch_one("SELECT id, brain_id FROM brain_memories WHERE id = ?", (memory_id,))
    if current is None:
        return False

    def work(connection: sqlite3.Connection) -> None:
        if _fts:
            connection.execute("DELETE FROM brain_memory_fts WHERE memory_id = ?", (memory_id,))
        connection.execute("DELETE FROM brain_memories WHERE id = ?", (memory_id,))
        connection.execute(
            """
            DELETE FROM brain_entities
            WHERE brain_id = ?
              AND id NOT IN (
                SELECT source_entity_id FROM brain_relationships WHERE brain_id = ?
                UNION
                SELECT target_entity_id FROM brain_relationships WHERE brain_id = ?
              )
            """,
            (current["brain_id"], current["brain_id"], current["brain_id"]),
        )
        touch_profile(connection, current["brain_id"])

    db.transaction(work)
    return True


def search_memory_ids(brain_id: str, match: str, status: str | None, limit: int) -> list[str] | None:
    if not _fts or not match:
        return None
    clauses = ["brain_memory_fts MATCH ?", "brain_id = ?"]
    params: list = [match, brain_id]
    if status:
        clauses.append("status = ?")
        params.append(status)
    params.append(limit)
    try:
        rows = db.fetch_all(
            f"""
            SELECT memory_id FROM brain_memory_fts
            WHERE {' AND '.join(clauses)}
            ORDER BY bm25(brain_memory_fts)
            LIMIT ?
            """,
            tuple(params),
        )
    except sqlite3.OperationalError:
        logger.warning("Brain full-text query failed")
        return None
    return [row["memory_id"] for row in rows]


def list_relationships(brain_id: str, *, status: str | None = "accepted") -> list[dict]:
    clauses = ["r.brain_id = ?"]
    params: list = [brain_id]
    if status:
        clauses.append("r.status = ?")
        params.append(status)
    return db.fetch_all(
        f"""
        SELECT r.id, r.relationship_type, r.stance, r.status, r.source_memory_id,
               r.source_entity_id, r.target_entity_id,
               se.name AS source_name, se.entity_type AS source_entity_type,
               te.name AS target_name, te.entity_type AS target_entity_type,
               m.category AS category, m.content AS memory_content, m.stance AS memory_stance,
               m.status AS memory_status
        FROM brain_relationships r
        JOIN brain_entities se ON se.id = r.source_entity_id
        JOIN brain_entities te ON te.id = r.target_entity_id
        LEFT JOIN brain_memories m ON m.id = r.source_memory_id
        WHERE {' AND '.join(clauses)}
        ORDER BY r.created_at ASC
        """,
        tuple(params),
    )


def counts(brain_id: str) -> dict:
    def _count(sql: str) -> int:
        row = db.fetch_one(sql, (brain_id,))
        return int(row["n"]) if row else 0

    return {
        "accepted": _count("SELECT COUNT(*) AS n FROM brain_memories WHERE brain_id = ? AND status = 'accepted'"),
        "proposed": _count("SELECT COUNT(*) AS n FROM brain_memories WHERE brain_id = ? AND status = 'proposed'"),
        "relationships": _count(
            "SELECT COUNT(*) AS n FROM brain_relationships WHERE brain_id = ? AND status = 'accepted'"
        ),
        "pending_questions": _count(
            "SELECT COUNT(*) AS n FROM brain_questions WHERE brain_id = ? AND status = 'pending'"
        ),
        "entities": _count("SELECT COUNT(*) AS n FROM brain_entities WHERE brain_id = ?"),
    }


def add_question(
    brain_id: str,
    *,
    question_text: str,
    motivation: str,
    gap_key: str,
    usefulness: float,
    generator: str,
) -> dict:
    question_id = db.new_id()
    timestamp = db.now()
    db.execute(
        """
        INSERT INTO brain_questions (
            id, brain_id, question_text, status, motivation, gap_key, usefulness,
            generator, answer_text, created_at, answered_at, updated_at
        ) VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, NULL, ?, NULL, ?)
        """,
        (question_id, brain_id, question_text, motivation, gap_key, usefulness, generator, timestamp, timestamp),
    )
    row = get_question(question_id)
    assert row is not None
    return row


def get_question(question_id: str) -> dict | None:
    return db.fetch_one("SELECT * FROM brain_questions WHERE id = ?", (question_id,))


def list_questions(brain_id: str, status: str | None = None) -> list[dict]:
    if status:
        return db.fetch_all(
            """
            SELECT * FROM brain_questions
            WHERE brain_id = ? AND status = ?
            ORDER BY usefulness DESC, created_at ASC
            """,
            (brain_id, status),
        )
    return db.fetch_all(
        """
        SELECT * FROM brain_questions
        WHERE brain_id = ?
        ORDER BY created_at DESC
        """,
        (brain_id,),
    )


def known_question(brain_id: str, gap_key: str, question_text: str) -> bool:
    row = db.fetch_one(
        """
        SELECT id FROM brain_questions
        WHERE brain_id = ? AND (gap_key = ? OR lower(question_text) = lower(?))
        LIMIT 1
        """,
        (brain_id, gap_key, question_text.strip()),
    )
    return row is not None


def update_question(question_id: str, **fields) -> dict | None:
    current = get_question(question_id)
    if current is None:
        return None
    for key in ("status", "answer_text", "answered_at"):
        if key in fields:
            current[key] = fields[key]
    db.execute(
        """
        UPDATE brain_questions
        SET status = ?, answer_text = ?, answered_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (current["status"], current["answer_text"], current["answered_at"], db.now(), question_id),
    )
    return get_question(question_id)


def add_change(brain_id: str, kind: str, detail: str) -> dict:
    change_id = db.new_id()
    timestamp = db.now()
    db.execute(
        "INSERT INTO brain_changes (id, brain_id, kind, detail, created_at) VALUES (?, ?, ?, ?, ?)",
        (change_id, brain_id, kind, detail, timestamp),
    )
    return {"id": change_id, "brain_id": brain_id, "kind": kind, "detail": detail, "created_at": timestamp}


def list_changes(brain_id: str, limit: int = 20) -> list[dict]:
    return db.fetch_all(
        """
        SELECT * FROM brain_changes
        WHERE brain_id = ?
        ORDER BY created_at DESC, rowid DESC
        LIMIT ?
        """,
        (brain_id, limit),
    )


def clear_brain(brain_id: str) -> dict:
    def work(connection: sqlite3.Connection) -> None:
        if _fts:
            connection.execute("DELETE FROM brain_memory_fts WHERE brain_id = ?", (brain_id,))
        connection.execute("DELETE FROM brain_profiles WHERE id = ?", (brain_id,))

    db.transaction(work)
    profile = ensure_profile()
    add_change(profile["id"], "clear", "Cleared the previous Brain. Conversation history was kept.")
    return profile


def export_brain(brain_id: str) -> dict:
    profile = db.fetch_one("SELECT * FROM brain_profiles WHERE id = ?", (brain_id,))
    return {
        "profile": _profile_out(profile) if profile else None,
        "sources": db.fetch_all("SELECT * FROM brain_sources WHERE brain_id = ? ORDER BY created_at", (brain_id,)),
        "memories": list_memories(brain_id, limit=5000),
        "entities": db.fetch_all(
            "SELECT * FROM brain_entities WHERE brain_id = ? ORDER BY name",
            (brain_id,),
        ),
        "relationships": list_relationships(brain_id, status=None),
        "questions": list_questions(brain_id),
        "changes": list_changes(brain_id, limit=500),
        "exported_at": db.now(),
    }
