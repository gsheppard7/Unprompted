import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("UNPROMPTED_DB", str(tmp_path / "test.db"))
    monkeypatch.setenv("UNPROMPTED_SEED", "0")
    monkeypatch.setenv("UNPROMPTED_SPEAK_DELAY_S", "0.01")
    monkeypatch.setenv("UNPROMPTED_IDLE_POLL_S", "3600")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from app import db
    from app.main import app

    db.close_db()
    with TestClient(app) as test_client:
        yield test_client
    db.close_db()
