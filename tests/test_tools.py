"""Phase 1 tests: symptom routing and booking tools. Offline, temp DB, fixed 'today'."""
from contextlib import closing
from datetime import date, datetime

import pytest

from agent import config
from agent.db import connect
from agent.tools import booking
from agent.tools.booking import book_appointment, check_availability, manage_booking
from agent.tools.departments import find_department
from data.seed import seed

TODAY = date(2030, 1, 7)
NOW = datetime(2030, 1, 7, 9, 0)  # before the first slot of the day
TOMORROW = "2030-01-08"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """Fresh seeded DB in a temp folder; tools read its path from config."""
    path = tmp_path / "hospital.db"
    monkeypatch.setenv("DB_PATH", str(path))
    monkeypatch.setattr(booking, "_now", lambda: NOW)
    config.get_settings.cache_clear()
    seed(path, today=TODAY)
    yield path
    config.get_settings.cache_clear()


def _prebooked(path):
    with closing(connect(path)) as conn:
        return conn.execute(
            "SELECT s.id, s.date, b.patient_id FROM bookings b JOIN slots s ON s.id = b.slot_id LIMIT 1"
        ).fetchone()


def _free_slot(day=TOMORROW, **kw):
    return check_availability(day, **kw)[0]["slot_id"]


# --- seed ---
def test_seed_counts(db):
    assert seed(db, today=TODAY) == {"departments": 4, "doctors": 6, "slots": 168, "bookings": 5}


def test_seed_is_repeatable(db):
    first = _prebooked(db)["id"]
    seed(db, today=TODAY)
    assert _prebooked(db)["id"] == first


# --- routing ---
@pytest.mark.parametrize("text, dept", [
    ("knee pain", "Orthopedics"),
    ("ear ache", "ENT"),
    ("fever in my child", "Pediatrics"),
    ("sore throat", "ENT"),
    ("asdkjh qwe", "General Medicine"),
    ("", "General Medicine"),
])
def test_find_department(text, dept):
    assert find_department(text)["department"] == dept


# --- availability ---
def test_availability_only_free_slots(db):
    slot = _prebooked(db)
    with closing(connect(db)) as conn:
        all_ids = [r[0] for r in conn.execute("SELECT id FROM slots WHERE date = ?", (slot["date"],))]
    free = []
    for dept in ["General Medicine", "Orthopedics", "ENT", "Pediatrics"]:
        free += [s["slot_id"] for s in check_availability(slot["date"], department=dept)]
    assert slot["id"] not in free
    assert len(free) < len(all_ids)


def test_availability_filters_and_limit(db):
    slots = check_availability("tomorrow")
    assert 0 < len(slots) <= 8
    rao = check_availability(TOMORROW, doctor="rao")
    assert rao and all(s["doctor"] == "Dr. Rao" for s in rao)
    ent = check_availability(TOMORROW, department="ent")
    assert ent and all(s["department"] == "ENT" for s in ent)
    assert set(slots[0]) == {"doctor", "department", "date", "time", "slot_id"}


def test_availability_bad_and_past_dates(db):
    assert "error" in check_availability("2030-01-06")
    assert "error" in check_availability("next friday")


# --- booking ---
def test_booking_is_idempotent(db):
    slot_id = _free_slot()
    first = book_appointment(slot_id, "P-1", "Ravi")
    assert first["booking_id"].startswith("HSP-")
    again = book_appointment(slot_id, "P-1", "Ravi")
    assert again["booking_id"] == first["booking_id"]
    assert slot_id not in [s["slot_id"] for s in check_availability(TOMORROW)]


def test_other_patient_gets_error(db):
    slot_id = _free_slot()
    book_appointment(slot_id, "P-1", "Ravi")
    assert book_appointment(slot_id, "P-2", "Someone")["error"] == "slot already taken"


def test_prebooked_slot_is_taken(db):
    slot = _prebooked(db)
    assert book_appointment(slot["id"], "P-NEW", "New")["error"] == "slot already taken"
    assert "booking_id" in book_appointment(slot["id"], slot["patient_id"], "Same")


def test_booking_errors(db):
    assert "error" in book_appointment(999999, "P-1", "Ravi")
    assert "error" in book_appointment(_free_slot(), "", "Ravi")


def test_past_slot_cannot_be_booked(db, monkeypatch):
    slot_id = _free_slot()
    monkeypatch.setattr(booking, "_now", lambda: datetime(2030, 2, 1, 9, 0))
    assert book_appointment(slot_id, "P-1", "Ravi") == {"error": "that time has already passed"}


# --- view / cancel ---
def test_view_and_cancel_frees_slot(db):
    slot_id = _free_slot()
    bid = book_appointment(slot_id, "P-1", "Ravi")["booking_id"]

    viewed = manage_booking(bid.lower(), "view")
    assert viewed["status"] == "booked" and viewed["patient_name"] == "Ravi"

    assert manage_booking(bid, "cancel")["status"] == "cancelled"
    assert manage_booking(bid, "view")["status"] == "cancelled"
    assert "error" in manage_booking(bid, "cancel")
    assert slot_id in [s["slot_id"] for s in check_availability(TOMORROW)]

    rebook = book_appointment(slot_id, "P-2", "Other")  # partial unique index allows this
    assert rebook["booking_id"] != bid


def test_manage_booking_errors(db):
    assert "error" in manage_booking("HSP-0000", "view")
    assert "error" in manage_booking("HSP-1001", "delete")


# --- past times today (bug: at 22:44 it booked today's 10:00) ---
LATE = datetime(2026, 11, 21, 22, 44)
LATE_TODAY, LATE_TOMORROW = "2026-11-21", "2026-11-22"


@pytest.fixture
def late_evening(tmp_path, monkeypatch):
    """DB seeded for 2026-11-21, clock pinned at 22:44, plus one extra slot today at 23:30."""
    path = tmp_path / "hospital.db"
    monkeypatch.setenv("DB_PATH", str(path))
    config.get_settings.cache_clear()
    seed(path, today=LATE.date())
    monkeypatch.setattr(booking, "_now", lambda: LATE)
    with closing(connect(path)) as conn, conn:
        conn.execute("INSERT INTO slots(doctor_id, date, time) VALUES (1, ?, '23:30')", (LATE_TODAY,))
    yield path
    config.get_settings.cache_clear()


def _slot_id(path, day, time, doctor="Dr. Sharma"):
    with closing(connect(path)) as conn:
        return conn.execute(
            "SELECT s.id FROM slots s JOIN doctors d ON d.id = s.doctor_id WHERE s.date = ? AND s.time = ? AND d.name = ?",
            (day, time, doctor),
        ).fetchone()[0]


def test_today_lists_only_later_slots(late_evening):
    today = check_availability("today", doctor="Sharma")
    assert [s["time"] for s in today] == ["23:30"]
    assert all(s["time"] > "22:44" for s in check_availability(LATE_TODAY))


def test_past_time_today_cannot_be_booked(late_evening):
    slot = _slot_id(late_evening, LATE_TODAY, "10:00")
    assert book_appointment(slot, "P-1", "Ravi") == {"error": "that time has already passed"}


def test_later_today_and_tomorrow_can_be_booked(late_evening):
    assert "booking_id" in book_appointment(_slot_id(late_evening, LATE_TODAY, "23:30"), "P-1", "Ravi")
    assert "10:00" in [s["time"] for s in check_availability("tomorrow", doctor="Sharma")]
    assert "booking_id" in book_appointment(_slot_id(late_evening, LATE_TOMORROW, "10:00"), "P-1", "Ravi")


def test_slot_at_exactly_now_has_passed(late_evening, monkeypatch):
    monkeypatch.setattr(booking, "_now", lambda: datetime(2026, 11, 21, 23, 30))
    assert booking.slot_has_passed(LATE_TODAY, "23:30")
    assert not booking.slot_has_passed(LATE_TOMORROW, "10:00")
