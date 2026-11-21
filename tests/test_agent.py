"""Agent tests: confirm-before-booking enforced in code, grouped availability, department warnings,
preferred doctor with department, guard/memory wiring. Offline: scripted fake model, real Strands loop."""
import json
from contextlib import closing
from datetime import datetime

import pytest

from agent import config, memory, models
from agent.agent import NEEDS_CONFIRMATION, SYSTEM_PROMPT, chat, confirmation_requested, find_slot
from agent.db import connect
from agent.models import approx_tokens
from agent.tools import booking
from data.seed import seed
from tests.fakes import FakeModel, tool_call

NOW = datetime(2030, 1, 7, 9, 0)
TOMORROW = "2030-01-08"
PATIENT = ("P001", "Ravi")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """Temp DB, pinned clock, single fake model that the tests script turn by turn."""
    path = tmp_path / "hospital.db"
    for k, v in {"DB_PATH": str(path), "MODEL_CHAIN": "gemini", "GEMINI_API_KEY": "test", "GEMINI_MODEL_ID": "g",
                 "GUARDRAILS_ENABLED": "false", "MEMORY_ENABLED": "false"}.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    monkeypatch.setattr(booking, "_now", lambda: NOW)
    seed(path, today=NOW.date())
    fake = FakeModel()
    monkeypatch.setattr(models, "build_model", lambda name: fake)
    free = booking.check_availability(TOMORROW, limit=100)
    yield fake, path, free[0], next(s for s in free if s["doctor"] != free[0]["doctor"])
    config.get_settings.cache_clear()


def _bookings(path) -> int:
    with closing(connect(path)) as conn:
        return conn.execute("SELECT COUNT(*) FROM bookings WHERE patient_id = ?", (PATIENT[0],)).fetchone()[0]


def _turn(fake, messages, text, *script):
    fake.script = list(script)
    result = chat(text, messages, *PATIENT)
    assert result["model_used"] == "gemini"
    return result["messages"]


def _book(slot, **override):
    args = {"doctor": slot["doctor"], "date": slot["date"], "time": slot["time"],
            "patient_id": PATIENT[0], "patient_name": PATIENT[1], **override}
    return tool_call("book_appointment", **args)


def _results(messages) -> list[str]:
    return [c["text"] for m in messages for b in m["content"] if "toolResult" in b for c in b["toolResult"]["content"]]


def _last_json(messages) -> dict:
    return json.loads(_results(messages)[-1])


# --- two-step booking ---
def test_first_call_asks_to_confirm_and_does_not_book(setup):
    fake, path, slot, _ = setup
    messages = _turn(fake, [], "book it", _book(slot), "Please confirm?")
    assert _bookings(path) == 0
    result = _last_json(messages)
    assert result["result"] == NEEDS_CONFIRMATION
    assert set(result) == {"result", "doctor", "department", "date", "time"}  # no warning without routing


def test_same_slot_in_later_turn_books(setup):
    fake, path, slot, _ = setup
    messages = _turn(fake, [], "book it", _book(slot), "Please confirm?")
    messages = _turn(fake, messages, "yes", _book(slot, time=slot["time"].lstrip("0")), "Booked!")  # "9:00"-style ok
    assert _bookings(path) == 1
    result = _last_json(messages)
    assert result["booking_id"].startswith("HSP-") and result["department"] == slot["department"]


def test_two_calls_in_same_turn_do_not_book(setup):
    fake, path, slot, _ = setup
    _turn(fake, [], "book it, no need to ask me", _book(slot), _book(slot), "Please confirm?")
    assert _bookings(path) == 0


def test_confirming_a_different_slot_does_not_book(setup):
    fake, path, slot, other = setup
    messages = _turn(fake, [], "book it", _book(slot), "Please confirm?")
    messages = _turn(fake, messages, "yes", _book(other), "Please confirm the other one?")
    assert _bookings(path) == 0 and _last_json(messages)["result"] == NEEDS_CONFIRMATION


def test_unknown_slot_and_bad_time_return_errors(setup):
    fake, _, slot, _ = setup
    messages = _turn(fake, [], "book", _book(slot, doctor="Dr. Nobody"), "Sorry.")
    assert "no such slot" in _last_json(messages)["error"]
    messages = _turn(fake, [], "book", _book(slot, time="noon-ish"), "Sorry.")
    assert "time must look like" in _last_json(messages)["error"]


def test_confirmation_step_rejects_past_time(setup, monkeypatch):
    fake, path, slot, _ = setup
    monkeypatch.setattr(booking, "_now", lambda: datetime(2030, 1, 9, 9, 0))
    messages = _turn(fake, [], "book it", _book(slot), "Sorry.")
    assert _last_json(messages) == {"error": "that time has already passed"} and _bookings(path) == 0


def test_confirmation_check_with_no_history():
    assert confirmation_requested([], 1) is False


def test_find_slot_normalizes_names_and_times(setup):
    _, _, slot, _ = setup
    surname = slot["doctor"].split()[-1].lower()
    for time in (slot["time"], slot["time"].lstrip("0")):
        assert find_slot(surname, slot["date"], time)["slot_id"] == slot["slot_id"]
    assert find_slot("dr", slot["date"], slot["time"])["error"].startswith("more than one doctor")


# --- fix 4: department mismatch warning ---
def test_booking_outside_routed_department_warns(setup):
    fake, _, _, _ = setup
    meena = booking.check_availability(TOMORROW, doctor="Meena", limit=10)[0]
    messages = _turn(fake, [], "I have a headache, book Dr. Meena",
                     tool_call("find_department", symptom="headache"), _book(meena), "Note: ...")
    result = _last_json(messages)
    assert result["department"] == "ENT"
    assert result["warning"].startswith("Dr. Meena is ENT. For the patient's concern we suggested General Medicine")


def test_matching_department_has_no_warning(setup):
    fake, _, _, _ = setup
    rao = booking.check_availability(TOMORROW, doctor="Rao", limit=10)[0]
    messages = _turn(fake, [], "knee pain, Dr. Rao", tool_call("find_department", symptom="knee pain"), _book(rao), "OK?")
    assert "warning" not in _last_json(messages)


# --- fix 3: grouped availability ---
def test_availability_grouped_by_doctor_with_all_times(setup):
    fake, _, _, _ = setup
    messages = _turn(fake, [], "free tomorrow?", tool_call("check_availability", date="tomorrow"), "Here.")
    result = _last_json(messages)
    assert set(result) == {"date", "doctors"} and result["date"] == TOMORROW
    assert len(result["doctors"]) == 6  # every doctor, not cut at 8 slots
    assert all(set(d) == {"doctor", "department", "times"} and d["times"] for d in result["doctors"])
    assert sum(len(d["times"]) for d in result["doctors"]) == len(booking.check_availability(TOMORROW, limit=100))
    assert "slot_id" not in json.dumps(result)


def test_availability_department_filter_and_empty_note(setup, monkeypatch):
    fake, _, _, _ = setup
    messages = _turn(fake, [], "ENT?", tool_call("check_availability", date=TOMORROW, department="ENT"), "Here.")
    assert {d["department"] for d in _last_json(messages)["doctors"]} == {"ENT"}
    monkeypatch.setattr(booking, "_now", lambda: datetime(2030, 1, 7, 23, 0))
    messages = _turn(fake, [], "today?", tool_call("check_availability", date="today"), "None left.")
    assert _last_json(messages)["doctors"] == [] and "no free times" in _last_json(messages)["note"]


def test_availability_cut_off_adds_note(setup, monkeypatch):
    fake, _, _, _ = setup
    import agent.agent as agent_module
    monkeypatch.setattr(agent_module, "MAX_TIMES", 3)
    messages = _turn(fake, [], "free tomorrow?", tool_call("check_availability", date="tomorrow"), "Here.")
    result = _last_json(messages)
    assert sum(len(d["times"]) for d in result["doctors"]) == 3 and "only some free times" in result["note"]


# --- fix 2: list_doctors ---
def test_list_doctors_reads_all_six_from_db(setup):
    fake, _, _, _ = setup
    messages = _turn(fake, [], "list doctors", tool_call("list_doctors"), "Here.")
    doctors = _last_json(messages)["doctors"]
    assert len(doctors) == 6 and {"doctor": "Dr. Meena", "department": "ENT"} in doctors
    messages = _turn(fake, [], "ortho doctors", tool_call("list_doctors", department="ortho"), "Here.")
    assert {d["doctor"] for d in _last_json(messages)["doctors"]} == {"Dr. Rao", "Dr. Kapoor"}


# --- history trimming ---
def test_confirmation_survives_history_trimming(setup):
    """A big confirm turn gets trimmed out of what the model sees, but 'yes' still books."""
    fake, path, slot, _ = setup
    look = tool_call("check_availability", date=TOMORROW)
    messages = _turn(fake, [], "anything tomorrow? book the first one", look, look, look, _book(slot), "Confirm?")
    assert len(messages) > 6
    messages = _turn(fake, messages, "yes", _book(slot), "Booked!")
    assert _bookings(path) == 1
    assert len(fake.received[-2]) <= 6 + 1  # trimmed history + the new patient message


def test_model_gets_trimmed_history_and_chat_returns_full(setup):
    fake, _, _, _ = setup
    messages: list = []
    for i in range(5):
        messages = _turn(fake, messages, f"hello {i}", f"hi {i}")
    assert len(messages) == 10
    sent = fake.received[-1]
    assert len(sent) <= 7 and approx_tokens(sent) <= 3000
    assert sent[-1]["content"][0]["text"] == "hello 4"


# --- guard / memory wiring ---
def test_blocked_input_never_calls_the_model(setup):
    fake, _, _, _ = setup
    result = chat("what dose of paracetamol should I take?", [], *PATIENT)
    assert result["model_used"] is None and "medical advice" in result["reply"] and fake.calls == 0


@pytest.mark.parametrize("text, expected", [
    ("I have chest pain", "call 108"),
    ("my child had a seizure", "call 108"),
    ("I fell and I'm bleeding a lot", "call 108"),
    ("I feel like ending my life", "14416"),
])
def test_emergencies_answered_without_model(setup, text, expected):
    fake, _, _, _ = setup
    result = chat(text, [], *PATIENT)
    assert expected in result["reply"] and result["model_used"] is None and fake.calls == 0


def test_phone_number_masked_in_reply_and_history(setup):
    fake, _, _, _ = setup
    fake.script = ["Call the desk on 98765 43210."]
    result = chat("what's the desk number?", [], *PATIENT)
    assert result["reply"] == "Call the desk on [PHONE]."
    assert result["messages"][-1]["content"][0]["text"] == "Call the desk on [PHONE]."


def test_booking_remembers_usual_doctor_with_department(setup):
    fake, _, slot, _ = setup
    messages = _turn(fake, [], "book it", _book(slot), "Confirm?")
    _turn(fake, messages, "yes", _book(slot), "Booked!")
    facts = memory.load_facts(PATIENT[0])
    assert facts["preferred_doctor"] == slot["doctor"]
    assert facts["preferred_doctor_department"] == slot["department"]
    fake.script = ["Sure."]
    chat("book with my usual doctor", [], *PATIENT, session_id="new-session")
    assert f"preferred_doctor: {slot['doctor']}; preferred_doctor_department: {slot['department']}" in fake.system_prompts[-1]


def test_prompt_rules():
    assert "Never assume today" in SYSTEM_PROMPT and "list_doctors is the only source" in SYSTEM_PROMPT
    assert "Dr. Meena is ENT; for a headache General Medicine is better" in SYSTEM_PROMPT
    assert len(SYSTEM_PROMPT.split()) < 300
