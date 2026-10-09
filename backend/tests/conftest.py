import pytest

from app import workflow
from app.config import settings
from app.seed import seed


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    seed(str(path))
    monkeypatch.setattr(settings, "database_path", str(path))
    return path


class FakeLLM:
    """Replaces the two model seams in workflow so tests run offline and deterministically."""

    def __init__(self):
        self.queue: dict[type, list] = {}
        self.calls: list[str] = []
        self.messages: list[list] = []

    def push(self, schema, **fields):
        self.queue.setdefault(schema, []).append(schema(**fields))

    def structured(self, schema, messages):
        self.calls.append(schema.__name__)
        self.messages.append(messages)
        return self.queue[schema].pop(0)


@pytest.fixture
def llm(seeded_db, monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(workflow, "_call_structured", fake.structured)
    monkeypatch.setattr(workflow, "_call_text", lambda messages: "Plain English explanation.")
    return fake
