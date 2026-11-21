"""Phase 2 tests: confirm-before-booking is enforced in code. Offline: scripted fake model, real Strands loop."""
from contextlib import closing
from datetime import date, datetime

import pytest

from agent import config, models
from agent.agent import NEEDS_CONFIRMATION, chat, confirmation_requested
from agent.models import approx_tokens
from agent.db import connect
from agent.tools import booking
from data.seed import seed
from tests.fakes import FakeModel, tool_call

TODAY = date(2030, 1, 7)
TOMORROW = "2030-01-08"
PATIENT = ("P001", "Ravi")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Temp DB, pinned date, single fake model that the tests script turn by turn."""
    path = tmp_path / "hospital.db"
    for k, v in {"DB_PATH": str(path), "MODEL_CHAIN": "gemini", "GEMINI_API_KEY": "test",
                 "GEMINI_MODEL_ID": "g"}.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    monkeypatch.setattr(booking, "_now", lambda: datetime(2030, 1, 7, 9, 0))
    seed(path, today=TODAY)
    fake = FakeModel()
    monkeypatch.setattr(models, "build_model", lambda name: fake)
    slots = booking.check_availability(TOMORROW)
    yield fake, path, slots[0]["slot_id"], slots[1]["slot_id"]
    config.get_settings.cache_clear()


def _bookings(path) -> int:
    with closing(connect(path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM bookings WHERE patient_id = ?", (PATIENT[0],)).fetchone()[0]


def _turn(fake, messages, text, *script):
    fake.script = list(script)
    result = chat(text, messages, *PATIENT)
    assert result["model_used"] == "gemini"
    return result["messages"]


def _book(slot_id):
    return tool_call("book_appointment", slot_id=slot_id, patient_id=PATIENT[0], patient_name=PATIENT[1])


def _tool_results(messages) -> list[str]:
    return [c["text"] for m in messages for b in m["content"] if "toolResult" in b for c in b["toolResult"]["content"]]


def test_first_call_asks_to_confirm_and_does_not_book(setup):
    fake, path, slot, _ = setup
    messages = _turn(fake, [], "book the 10:00 with Dr. Rao", _book(slot), "Please confirm Dr. Rao at 10:00?")
    assert _bookings(path) == 0
    assert NEEDS_CONFIRMATION in _tool_results(messages)[-1]


def test_same_slot_in_later_turn_books(setup):
    fake, path, slot, _ = setup
    messages = _turn(fake, [], "book the 10:00", _book(slot), "Please confirm?")
    messages = _turn(fake, messages, "yes", _book(slot), "Booked!")
    assert _bookings(path) == 1
    assert '"booking_id": "HSP-' in _tool_results(messages)[-1]


def test_two_calls_in_same_turn_do_not_book(setup):
    fake, path, slot, _ = setup
    _turn(fake, [], "book the 10:00, no need to ask me", _book(slot), _book(slot), "Please confirm?")
    assert _bookings(path) == 0


def test_confirming_a_different_slot_does_not_book(setup):
    fake, path, slot, other = setup
    messages = _turn(fake, [], "book the 10:00", _book(slot), "Please confirm?")
    messages = _turn(fake, messages, "yes", _book(other), "Please confirm the other one?")
    assert _bookings(path) == 0
    assert NEEDS_CONFIRMATION in _tool_results(messages)[-1]


def test_unknown_slot_returns_error(setup):
    fake, path, _, _ = setup
    messages = _turn(fake, [], "book slot 999999", _book(999999), "That slot doesn't exist.")
    assert "no such slot" in _tool_results(messages)[-1]


def test_confirmation_check_with_no_history():
    assert confirmation_requested([], 1) is False


def test_confirmation_survives_history_trimming(setup):
    """A big confirm turn gets trimmed out of what the model sees, but 'yes' still books."""
    fake, path, slot, _ = setup
    look = tool_call("check_availability", date=TOMORROW)
    messages = _turn(fake, [], "anything tomorrow? book the first one", look, look, look, _book(slot), "Confirm?")
    assert len(messages) > 6
    messages = _turn(fake, messages, "yes", _book(slot), "Booked!")
    assert _bookings(path) == 1
    first_call_of_turn = fake.received[-2]
    assert len(first_call_of_turn) <= 6 + 1  # trimmed history + the new patient message


def test_model_gets_trimmed_history_and_chat_returns_full(setup):
    fake, _, _, _ = setup
    messages: list = []
    for i in range(5):
        messages = _turn(fake, messages, f"hello {i}", f"hi {i}")
    assert len(messages) == 10  # full history kept for the UI
    sent = fake.received[-1]
    assert len(sent) <= 7 and approx_tokens(sent) <= 3000
    assert sent[-1]["content"][0]["text"] == "hello 4"


def test_check_availability_result_is_compact(setup):
    fake, _, _, _ = setup
    messages = _turn(fake, [], "slots tomorrow?", tool_call("check_availability", date="tomorrow"), "Here.")
    text = _tool_results(messages)[-1]
    assert '"slots"' in text and '"department"' not in text and '"slot_id"' not in text
    assert text.count('"id"') <= 8


def test_confirmation_step_rejects_past_time(setup, monkeypatch):
    fake, path, slot, _ = setup  # slot is tomorrow (2030-01-08)
    monkeypatch.setattr(booking, "_now", lambda: datetime(2030, 1, 9, 9, 0))
    messages = _turn(fake, [], "book it", _book(slot), "Sorry.")
    assert "that time has already passed" in _tool_results(messages)[-1]
    assert _bookings(path) == 0


def test_prompt_says_never_assume_today():
    from agent.agent import SYSTEM_PROMPT
    assert "Never assume today" in SYSTEM_PROMPT and len(SYSTEM_PROMPT.split()) < 300
