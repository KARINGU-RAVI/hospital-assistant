"""Phase 5 tests: AgentCore entrypoint over HTTP (Starlette test client, no real server, no model)."""
import pytest
from starlette.testclient import TestClient

from agent import main


@pytest.fixture
def client(monkeypatch):
    calls = []

    def fake_chat(prompt, messages, patient_id, patient_name, patient_facts=None, session_id="local"):
        calls.append({"prompt": prompt, "patient_id": patient_id, "patient_name": patient_name, "session_id": session_id})
        new = messages + [{"role": "user", "content": [{"text": prompt}]},
                          {"role": "assistant", "content": [{"text": "hello"}]}]
        return {"reply": "hello", "messages": new, "model_used": "gemini"}

    monkeypatch.setattr(main, "chat", fake_chat)
    with TestClient(main.app) as c:
        yield c, calls


def test_ping(client):
    c, _ = client
    response = c.get("/ping")
    assert response.status_code == 200 and response.json()["status"].startswith("Healthy")


def test_invocation_returns_reply_and_messages_only(client):
    c, calls = client
    payload = {"prompt": "hi", "session_id": "s" * 36, "patient_id": "P001", "patient_name": "Ravi", "messages": []}
    body = c.post("/invocations", json=payload).json()
    assert set(body) == {"reply", "messages"}  # never model_used
    assert body["reply"] == "hello" and len(body["messages"]) == 2
    assert calls == [{"prompt": "hi", "patient_id": "P001", "patient_name": "Ravi", "session_id": "s" * 36}]


def test_bad_inputs_are_cleaned(client):
    c, calls = client
    c.post("/invocations", json={"prompt": "  hi  ", "patient_id": "P 001;drop", "messages": "not a list"})
    assert calls[-1]["patient_id"] == "P001drop" and calls[-1]["patient_name"] == "Guest"
    assert c.post("/invocations", json={"prompt": ""}).json()["reply"] == "Please type a message."


def test_errors_give_friendly_reply(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(main, "chat", lambda *a, **k: 1 / 0)
    body = c.post("/invocations", json={"prompt": "hi", "messages": []}).json()
    assert body == {"reply": main.ERROR_REPLY, "messages": []}
