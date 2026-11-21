"""check_availability, book_appointment (idempotent), manage_booking (view/cancel).

Every function returns a small dict / list and never raises: errors come back as {"error": "..."}.
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta

from agent.db import connect

log = logging.getLogger(__name__)
MAX_SLOTS = 8

SLOT_SELECT = """
    SELECT s.id AS slot_id, s.date, s.time, d.name AS doctor, dep.name AS department
    FROM slots s
    JOIN doctors d ON d.id = s.doctor_id
    JOIN departments dep ON dep.id = d.department_id
"""


def _now() -> datetime:
    """Current local date and time (a function so tests can pin it)."""
    return datetime.now()


def _today() -> date:
    """Today's date, from _now() (a function so tests can pin it)."""
    return _now().date()


def slot_has_passed(day: str, time: str) -> bool:
    """True if the slot's date AND time are already in the past."""
    return f"{day} {time}" <= _now().strftime("%Y-%m-%d %H:%M")


def _parse_date(value: str) -> date | str:
    """Turn 'today' / 'tomorrow' / 'YYYY-MM-DD' into a date, or return an error message."""
    text = (value or "").strip().lower()
    if text == "today":
        return _today()
    if text == "tomorrow":
        return _today() + timedelta(days=1)
    try:
        return date.fromisoformat(text)
    except ValueError:
        return "date must be YYYY-MM-DD, 'today' or 'tomorrow'"


def check_availability(
    date: str, department: str | None = None, doctor: str | None = None, limit: int = MAX_SLOTS
) -> list[dict] | dict:
    """List free appointment slots on a date, optionally for one department or doctor.
    For today, only slots later than the current time are listed.

    Args:
        date: The day to check, as YYYY-MM-DD, or "today" / "tomorrow".
        department: Department name, e.g. "Orthopedics" (optional).
        doctor: Doctor name or surname, e.g. "Rao" (optional).

    Returns:
        Up to 8 free slots as {doctor, department, date, time, slot_id}, or {"error": ...}.
    """
    day = _parse_date(date)
    if isinstance(day, str):
        return {"error": day}
    if day < _today():
        return {"error": "that date is in the past"}

    sql = SLOT_SELECT + """
        WHERE s.date = ?
          AND s.date || ' ' || s.time > ?
          AND NOT EXISTS (SELECT 1 FROM bookings b WHERE b.slot_id = s.id AND b.status = 'booked')
    """
    params: list = [day.isoformat(), _now().strftime("%Y-%m-%d %H:%M")]
    if department:
        sql += " AND lower(dep.name) LIKE ?"
        params.append(f"%{department.strip().lower()}%")
    if doctor:
        name = doctor.strip().lower().removeprefix("dr.").removeprefix("dr ").strip()
        sql += " AND lower(d.name) LIKE ?"
        params.append(f"%{name}%")
    sql += " ORDER BY s.time, d.name LIMIT ?"
    params.append(limit)

    try:
        with closing(connect()) as conn:
            return [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.Error as e:
        log.warning("check_availability failed: %s", e)
        return {"error": "could not check availability right now"}


def _next_booking_id(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(CAST(substr(id, 5) AS INTEGER)) FROM bookings").fetchone()
    return f"HSP-{(row[0] or 1000) + 1}"


def book_appointment(slot_id: int, patient_id: str, patient_name: str) -> dict:
    """Book a free slot for a patient. Safe to call twice: the same patient gets the same booking ID.

    Args:
        slot_id: The slot_id from check_availability.
        patient_id: The patient's ID.
        patient_name: The patient's full name.

    Returns:
        {"booking_id", "doctor", "date", "time"}, or {"error": ...} if the slot is taken or invalid.
    """
    if not (patient_id or "").strip() or not (patient_name or "").strip():
        return {"error": "patient_id and patient_name are required"}
    try:
        with closing(connect()) as conn, conn:
            slot = conn.execute(SLOT_SELECT + " WHERE s.id = ?", (slot_id,)).fetchone()
            if slot is None:
                return {"error": "no such slot"}
            if slot_has_passed(slot["date"], slot["time"]):
                return {"error": "that time has already passed"}
            details = {"doctor": slot["doctor"], "date": slot["date"], "time": slot["time"]}

            active = conn.execute(
                "SELECT id, patient_id FROM bookings WHERE slot_id = ? AND status = 'booked'", (slot_id,)
            ).fetchone()
            if active:
                if active["patient_id"] == patient_id:
                    return {"booking_id": active["id"], **details}
                return {"error": "slot already taken"}

            booking_id = _next_booking_id(conn)
            conn.execute(
                "INSERT INTO bookings VALUES (?, ?, ?, ?, 'booked', ?)",
                (booking_id, slot_id, patient_id, patient_name.strip(),
                 datetime.now().isoformat(timespec="seconds")),
            )
            return {"booking_id": booking_id, **details}
    except sqlite3.IntegrityError:
        return {"error": "slot already taken"}  # lost a race; the unique index protected us
    except sqlite3.Error as e:
        log.warning("book_appointment failed: %s", e)
        return {"error": "could not book right now"}


def manage_booking(booking_id: str, action: str = "view") -> dict:
    """View or cancel an existing booking.

    Args:
        booking_id: The booking ID, e.g. "HSP-1001".
        action: "view" to see the booking, or "cancel" to cancel it.

    Returns:
        The booking details (with status), or {"error": ...}.
    """
    action = (action or "").strip().lower()
    if action not in {"view", "cancel"}:
        return {"error": "action must be 'view' or 'cancel'"}
    booking_id = (booking_id or "").strip().upper()
    sql = """
        SELECT b.id AS booking_id, b.patient_name, b.status, s.date, s.time,
               d.name AS doctor, dep.name AS department
        FROM bookings b
        JOIN slots s ON s.id = b.slot_id
        JOIN doctors d ON d.id = s.doctor_id
        JOIN departments dep ON dep.id = d.department_id
        WHERE b.id = ?
    """
    try:
        with closing(connect()) as conn, conn:
            row = conn.execute(sql, (booking_id,)).fetchone()
            if row is None:
                return {"error": "booking not found"}
            booking = dict(row)
            if action == "view":
                return booking
            if booking["status"] == "cancelled":
                return {"error": "booking is already cancelled"}
            conn.execute("UPDATE bookings SET status = 'cancelled' WHERE id = ?", (booking_id,))
            return {**booking, "status": "cancelled", "message": "booking cancelled"}
    except sqlite3.Error as e:
        log.warning("manage_booking failed: %s", e)
        return {"error": "could not update the booking right now"}
