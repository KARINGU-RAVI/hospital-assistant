"""Phase 5 tests: Streamlit UI with Streamlit's AppTest (no browser, no server, no AWS)."""
import json

import pytest
import requests
from streamlit.testing.v1 import AppTest

from agent import config
from agent.config import PROJECT_ROOT

APP = str(PROJECT_ROOT / "ui" / "app.py")


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


@pytest.fixture
def local_mode(monkeypatch):
    monkeypatch.setenv("APP_MODE", "local")
    config.get_settings.cache_clear()
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append(json)
        return FakeResponse({"reply": f"echo: {json['prompt']}", "messages": json["messages"] + [{"role": "user"}]})

    monkeypatch.setattr(requests, "post", fake_post)
    yield sent
    config.get_settings.cache_clear()


def _run(at=None):
    at = at or AppTest.from_file(APP, default_timeout=30)
    return at.run()


def test_page_renders_title_disclaimer_sidebar(local_mode):
    at = _run()
    assert at.title[0].value == "Sunrise Clinic — Front Desk Assistant"
    assert "fake" in at.caption[0].value
    assert [t.value for t in at.sidebar.text_input] == ["Ravi", "P001"]
    assert len(at.session_state.session_id) == 36


def test_chat_round_trip_sends_history_and_session(local_mode):
    at = _run()
    at.chat_input[0].set_value("hello").run()
    at.chat_input[0].set_value("again").run()
    sent = local_mode
    assert [p["prompt"] for p in sent] == ["hello", "again"]
    assert sent[1]["messages"] == [{"role": "user"}]  # history from the first reply is sent back
    assert sent[0]["session_id"] == sent[1]["session_id"] and sent[0]["patient_id"] == "P001"
    texts = [m.markdown[0].value for m in at.chat_message]
    assert texts == ["hello", "echo: hello", "again", "echo: again"]
    assert not any("gemini" in t or "groq" in t or "fallback" in t for t in texts)


def test_example_button_sends_prompt(local_mode):
    at = _run()
    next(b for b in at.button if b.label == "Book a knee appointment").click().run()
    assert local_mode[-1]["prompt"] == "My knee hurts, can I see someone tomorrow?"


def test_new_session_clears_chat(local_mode):
    at = _run()
    at.chat_input[0].set_value("hello").run()
    old = at.session_state.session_id
    at.sidebar.button[0].click().run()
    assert at.session_state.session_id != old and at.session_state.chat == []


def test_server_down_shows_friendly_message(monkeypatch):
    monkeypatch.setenv("APP_MODE", "local")
    config.get_settings.cache_clear()

    def down(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", down)
    at = _run()
    at.chat_input[0].set_value("hello").run()
    assert at.chat_message[-1].markdown[0].value == "The assistant isn't available right now. Please try again in a minute."
    assert not at.exception
    config.get_settings.cache_clear()


def test_cloud_mode_reads_json_chunks(monkeypatch):
    monkeypatch.setenv("APP_MODE", "cloud")
    monkeypatch.setenv("AGENT_RUNTIME_ARN", "arn:test")
    config.get_settings.cache_clear()
    calls = []

    class FakeAgentCore:
        def invoke_agent_runtime(self, **kwargs):
            calls.append(kwargs)
            body = json.dumps({"reply": "from cloud", "messages": []}).encode()
            return {"contentType": "application/json", "response": [body[:10], body[10:]]}

    import boto3
    monkeypatch.setattr(boto3, "client", lambda *a, **k: FakeAgentCore())
    at = _run()
    at.chat_input[0].set_value("hi").run()
    assert at.chat_message[-1].markdown[0].value == "from cloud"
    assert calls[0]["agentRuntimeArn"] == "arn:test" and len(calls[0]["runtimeSessionId"]) >= 33
    assert json.loads(calls[0]["payload"])["prompt"] == "hi"
    config.get_settings.cache_clear()
