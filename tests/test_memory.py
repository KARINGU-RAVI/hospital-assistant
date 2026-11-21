"""Phase 4 tests: local patient facts, AgentCore Memory path with a fake client, fallbacks. Offline."""
import logging
from datetime import date

import pytest

from agent import config, memory
from data.seed import seed


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "hospital.db"
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setenv("MEMORY_ENABLED", "false")
    monkeypatch.setenv("MEMORY_ID", "")
    config.get_settings.cache_clear()
    seed(path, today=date(2030, 1, 7))
    yield path
    config.get_settings.cache_clear()


class FakeMemory:
    def __init__(self, records=None, error=None):
        self.records, self.error, self.events, self.queries = records or [], error, [], []

    def create_event(self, **kwargs):
        if self.error:
            raise self.error
        self.events.append(kwargs)

    def retrieve_memories(self, **kwargs):
        if self.error:
            raise self.error
        self.queries.append(kwargs)
        return self.records


def _switch_on(monkeypatch, client):
    monkeypatch.setenv("MEMORY_ENABLED", "true")
    monkeypatch.setenv("MEMORY_ID", "mem-test")
    config.get_settings.cache_clear()
    monkeypatch.setattr(memory, "_client", lambda region: client)


def test_local_save_and_load_preferred_doctor(db):
    memory.save_turn("P001", "s1", "book Dr. Rao", "Booked", {"name": "Ravi", "preferred_doctor": "Dr. Rao"})
    assert memory.load_facts("P001") == {"name": "Ravi", "preferred_doctor": "Dr. Rao"}
    memory.save_turn("P001", "s2", "book Dr. Kapoor", "Booked", {"preferred_doctor": "Dr. Kapoor"})
    assert memory.load_facts("P001")["preferred_doctor"] == "Dr. Kapoor"  # updated, not duplicated
    assert memory.load_facts("P999") == {}


def test_enabled_saves_event_and_loads_preferences(db, monkeypatch):
    client = FakeMemory(records=[{"content": {"text": "Prefers morning appointments"}},
                                 {"content": {"text": "Likes Dr. Rao"}}])
    _switch_on(monkeypatch, client)
    memory.save_turn("P001", "session 1!", "hi", "hello", {"name": "Ravi"})
    assert client.events == [{"memory_id": "mem-test", "actor_id": "P001", "session_id": "session-1-",
                              "messages": [("hi", "USER"), ("hello", "ASSISTANT")]}]
    facts = memory.load_facts("P001")
    assert facts == {"name": "Ravi", "preferences": "Prefers morning appointments; Likes Dr. Rao"}
    assert client.queries[0]["namespace"] == "/patients/P001/preferences/"


def test_aws_errors_fall_back_to_local(db, monkeypatch, caplog):
    _switch_on(monkeypatch, FakeMemory(error=RuntimeError("no credentials")))
    with caplog.at_level(logging.WARNING, logger="agent.memory"):
        memory.save_turn("P001", "s1", "hi", "hello", {"preferred_doctor": "Dr. Rao"})
        assert memory.load_facts("P001") == {"preferred_doctor": "Dr. Rao"}
    assert [r.getMessage().split(" (")[0] for r in caplog.records] == ["fallback: memory -> local"] * 2


@pytest.mark.parametrize("raw, expected", [("abc-123_X", "abc-123_X"), ("", "local"), ("--x y", "x-y"), ("a" * 150, "a" * 100)])
def test_session_ids_fit_agentcore_pattern(raw, expected):
    assert memory.safe_session_id(raw) == expected
