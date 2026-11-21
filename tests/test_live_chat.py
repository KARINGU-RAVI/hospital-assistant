"""Opt-in LIVE test: real model calls (free tier). Skipped unless $env:RUN_LIVE="1".

Checks the agent confirms doctor/date/time BEFORE calling book_appointment, using the DB as truth.
"""
import os
from contextlib import closing
from datetime import date, timedelta

import pytest

from agent import config
from agent.agent import chat
from agent.db import connect
from data.seed import seed

pytestmark = pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason='live test: set $env:RUN_LIVE="1"')

PATIENT_ID, PATIENT_NAME = "P001", "Ravi"


@pytest.fixture
def live_db(tmp_path, monkeypatch):
    """Fresh DB for today; make sure Dr. Rao's 10:00 slot tomorrow is free."""
    path = tmp_path / "hospital.db"
    monkeypatch.setenv("DB_PATH", str(path))
    config.get_settings.cache_clear()
    seed(path)
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    with closing(connect(path)) as conn, conn:
        conn.execute(
            """DELETE FROM bookings WHERE slot_id IN (
                   SELECT s.id FROM slots s JOIN doctors d ON d.id = s.doctor_id
                   WHERE d.name = 'Dr. Rao' AND s.date = ? AND s.time = '10:00')""",
            (tomorrow,),
        )
    yield path
    config.get_settings.cache_clear()


def _patient_bookings(path) -> int:
    with closing(connect(path)) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM bookings WHERE patient_id = ? AND status = 'booked'", (PATIENT_ID,)
        ).fetchone()[0]


def test_confirms_before_booking(live_db):
    messages: list = []

    def turn(text: str) -> str:
        nonlocal messages
        result = chat(text, messages, PATIENT_ID, PATIENT_NAME)
        assert result["model_used"], "all models failed (busy message)"
        messages = result["messages"]
        return result["reply"]

    turn("My knee hurts, can I see someone tomorrow?")
    reply = turn("Please book the 10:00 slot with Dr. Rao.")
    assert _patient_bookings(live_db) == 0, "booked without asking the patient to confirm"
    assert "Rao" in reply

    turn("Yes, please confirm it.")
    assert _patient_bookings(live_db) == 1
