"""Builds the Strands agent: system prompt, tools, chat() flow.

Phase 2: model only. Phase 4 adds guardrail -> memory -> model -> guardrail -> memory around it.
Tool wrappers here keep results compact (free-tier token limits); agent/tools/*.py stay plain functions.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing

from strands import ToolContext, tool

from agent import guardrails, memory
from agent.db import connect
from agent.models import run_with_fallback
from agent.tools import booking
from agent.tools.booking import SLOT_SELECT, manage_booking
from agent.tools.departments import find_department, list_doctors
from agent.tools.rag import search_hospital_info

NEEDS_CONFIRMATION = "needs_confirmation"
MAX_TIMES = 30  # free times returned per call (a full day is 6 doctors x 4 times = 24)

SYSTEM_PROMPT = """You are the front-desk assistant of Sunrise Clinic (fictional): departments, doctors, free
times, and booking, viewing or cancelling appointments.

Rules:
- Use the tools for every fact about departments, doctors, times and bookings; never invent any.
  list_doctors is the only source of doctor names.
- For symptoms, call find_department and name the department, then check availability in THAT
  department unless the patient asks for another. Never diagnose, guess a condition, or suggest
  medicines or doses; a clinic doctor will advise them.
- Emergencies: tell them to call 108 or go to the nearest emergency room now. Do not book.
- Show free times grouped by doctor. If a result has a "note", tell the patient.
- Booking has two steps. The first book_appointment call returns "needs_confirmation" with doctor,
  department, date and time: repeat them, say any "warning", and ask the patient to confirm. Only
  after they say yes in their next message, call book_appointment again with the same doctor, date
  and time. Do this even if the patient already named the time.
- If the patient gives a time but no date, ask which day, or offer the next day with free times.
  Never assume today.
- Suggest preferred_doctor ("usual doctor") only if they work in the department for the current
  concern; otherwise say so, e.g. "Dr. Meena is ENT; for a headache General Medicine is better".
- For clinic questions (hours, departments, fees, insurance, parking, documents, cancellations,
  emergencies, contact), call search_hospital_info and answer only from its answers, naming the page,
  e.g. "from our Visiting hours page". If there are no answers, say "I don't have that information,
  please contact the front desk".
- After booking, give the booking ID (starts with HSP-). Never show other internal IDs.
- Keep replies short.
"""


def _patient_context(patient_id: str, patient_name: str, patient_facts: dict[str, str] | None) -> str:
    lines = [
        f"Now: {booking._now().strftime('%A %Y-%m-%d %H:%M')} (clinic local time).",
        f"Patient: {patient_name} (patient_id {patient_id}). Use exactly these when booking; don't ask for them.",
    ]
    if patient_facts:
        lines.append("Known about this patient: " + "; ".join(f"{k}: {v}" for k, v in patient_facts.items()))
    return "\n".join(lines)


# --- availability -------------------------------------------------------------------------------

@tool
def check_availability(date: str, department: str | None = None, doctor: str | None = None) -> dict:
    """List ALL free appointment times on a date, grouped by doctor, optionally for one department or doctor.

    Args:
        date: YYYY-MM-DD, or "today" / "tomorrow".
        department: Department name, e.g. "Orthopedics" (optional).
        doctor: Doctor name or surname, e.g. "Rao" (optional).

    Returns:
        {"date", "doctors": [{"doctor", "department", "times"}]} (+ "note" if cut off or empty), or {"error"}.
    """
    result = booking.check_availability(date, department=department, doctor=doctor, limit=MAX_TIMES + 1)
    if isinstance(result, dict):
        return result
    if not result:
        return {"date": date, "doctors": [], "note": "no free times on this date for this search"}
    cut_off = len(result) > MAX_TIMES
    grouped: dict[str, dict] = {}
    for slot in result[:MAX_TIMES]:
        entry = grouped.setdefault(slot["doctor"], {"doctor": slot["doctor"], "department": slot["department"], "times": []})
        entry["times"].append(slot["time"])
    out = {"date": result[0]["date"], "doctors": sorted(grouped.values(), key=lambda d: (d["department"], d["doctor"]))}
    if cut_off:
        out["note"] = "only some free times are shown; ask for a department or doctor to see all of them"
    return out


# --- booking ------------------------------------------------------------------------------------

def _normalize_time(value: str) -> str | None:
    """"10", "10:00", "4:30 pm", "16.30" -> "HH:MM"."""
    match = re.fullmatch(r"\s*(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm)?\s*", str(value or "").lower())
    if not match:
        return None
    hour, minute, ampm = int(match.group(1)), int(match.group(2) or 0), match.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    if ampm == "am" and hour == 12:
        hour = 0
    return f"{hour:02d}:{minute:02d}" if hour < 24 and minute < 60 else None


def find_slot(doctor: str, date: str, time: str) -> dict:
    """The slot for doctor + date + time: {"slot_id", "doctor", "department", "date", "time"} or {"error"}."""
    day = booking._parse_date(date)
    if isinstance(day, str):
        return {"error": day}
    hhmm = _normalize_time(time)
    if not hhmm:
        return {"error": "time must look like 10:00 or 4:30 pm"}
    name = str(doctor or "").strip().lower().removeprefix("dr.").removeprefix("dr ").strip()
    if not name:
        return {"error": "which doctor?"}
    try:
        with closing(connect()) as conn:
            rows = conn.execute(
                SLOT_SELECT + " WHERE lower(d.name) LIKE ? AND s.date = ? AND s.time = ?",
                (f"%{name}%", day.isoformat(), hhmm),
            ).fetchall()
    except sqlite3.Error:
        return {"error": "could not look up that time right now"}
    if not rows:
        return {"error": "no such slot: check the doctor, date and time with check_availability"}
    if len(rows) > 1:
        return {"error": "more than one doctor matches: " + ", ".join(r["doctor"] for r in rows)}
    return dict(rows[0])


def _slot_of(tool_input: dict) -> int | None:
    """Slot id a book_appointment call referred to (doctor/date/time, or an older slot_id input)."""
    if "slot_id" in tool_input:
        return tool_input["slot_id"]
    found = find_slot(tool_input.get("doctor", ""), tool_input.get("date", ""), tool_input.get("time", ""))
    return found.get("slot_id")


def _blocks(messages: list[dict]):
    for message in messages:
        yield from message.get("content", [])


def _tool_results(messages: list[dict], tool_name: str):
    """(input, parsed result) for each call of `tool_name` in `messages`, oldest first."""
    inputs: dict[str, dict] = {}
    for block in _blocks(messages):
        use = block.get("toolUse")
        if use and use.get("name") == tool_name:
            inputs[use.get("toolUseId")] = use.get("input", {})
        result = block.get("toolResult")
        if result and result.get("toolUseId") in inputs:
            for item in result.get("content", []):
                try:
                    data = json.loads(item.get("text", ""))
                except (TypeError, ValueError):
                    data = {"text": item.get("text", "")}
                yield inputs[result["toolUseId"]], data


def confirmation_requested(history: list[dict], slot_id: int) -> bool:
    """True if book_appointment asked the patient to confirm this slot anywhere in `history`.

    `history` must hold only EARLIER turns, so the patient has replied since the request.
    """
    return any(
        (data.get("result") == NEEDS_CONFIRMATION or NEEDS_CONFIRMATION in data.get("text", ""))
        and str(_slot_of(tool_input)) == str(slot_id)
        for tool_input, data in _tool_results(history, "book_appointment")
    )


def routed_department(messages: list[dict]) -> str | None:
    """The department find_department chose most recently in `messages`, if any."""
    department = None
    for _, data in _tool_results(messages, "find_department"):
        department = data.get("department") or department
    return department


def _earlier_turns(tool_context: ToolContext) -> list[dict]:
    """Full history before this turn (untrimmed, from chat()); else the agent's messages before the
    patient's latest message."""
    history = (tool_context.invocation_state or {}).get("session_history")
    if history is not None:
        return history
    messages = tool_context.agent.messages
    return messages[: max(_last_patient_message(messages), 0)]


def _last_patient_message(messages: list[dict]) -> int:
    """Index of the patient's latest typed message (tool results are also role 'user'; skip them)."""
    for i in range(len(messages) - 1, -1, -1):
        content = messages[i].get("content", [])
        if messages[i].get("role") == "user" and not any("toolResult" in block for block in content):
            return i
    return -1


@tool(context=True)
def book_appointment(doctor: str, date: str, time: str, patient_id: str, patient_name: str,
                     tool_context: ToolContext) -> dict:
    """Book an appointment in two steps. First call: returns needs_confirmation with doctor, department,
    date, time (and a warning if the department differs from the one chosen for the symptom); nothing is
    booked. After the patient says yes in their next message, call again with the same values to book.

    Args:
        doctor: Doctor name, e.g. "Dr. Rao".
        date: YYYY-MM-DD, or "today" / "tomorrow".
        time: Time from check_availability, e.g. "10:00".
        patient_id: The patient's ID.
        patient_name: The patient's full name.

    Returns:
        {"result": "needs_confirmation", ...}, {"booking_id", "doctor", "department", "date", "time"},
        or {"error": ...}.
    """
    slot = find_slot(doctor, date, time)
    if "error" in slot:
        return slot
    if booking.slot_has_passed(slot["date"], slot["time"]):
        return {"error": "that time has already passed"}
    earlier = _earlier_turns(tool_context)
    if confirmation_requested(earlier, slot["slot_id"]):
        booked = booking.book_appointment(slot["slot_id"], patient_id, patient_name)
        return {**booked, "department": slot["department"]} if "booking_id" in booked else booked

    details = {k: slot[k] for k in ("doctor", "department", "date", "time")}
    routed = routed_department(earlier + tool_context.agent.messages)
    if routed and routed != slot["department"]:
        details["warning"] = (
            f"{slot['doctor']} is {slot['department']}. For the patient's concern we suggested {routed}. "
            "Ask if they want to book anyway."
        )
    return {"result": NEEDS_CONFIRMATION, **details}


TOOLS = [tool(find_department), tool(list_doctors), check_availability, book_appointment,
         tool(manage_booking), tool(search_hospital_info)]


# --- chat ---------------------------------------------------------------------------------------

def booked_doctor(new_messages: list[dict]) -> tuple[str, str] | None:
    """(doctor, department) of a booking that succeeded in these messages, if any."""
    found = None
    for _, data in _tool_results(new_messages, "book_appointment"):
        if data.get("booking_id") and data.get("doctor"):
            found = (data["doctor"], data.get("department", ""))
    return found


def _replace_last_reply(messages: list[dict], text: str) -> list[dict]:
    """Store the guarded reply (e.g. masked phone numbers) in the history too."""
    if messages and messages[-1].get("role") == "assistant":
        messages = messages[:-1] + [{"role": "assistant", "content": [{"text": text}]}]
    return messages


def chat(
    prompt: str,
    session_messages: list[dict] | None,
    patient_id: str,
    patient_name: str,
    patient_facts: dict[str, str] | None = None,
    session_id: str = "local",
) -> dict:
    """Run one chat turn: guardrail -> memory -> model -> guardrail -> memory.

    Returns {"reply", "messages" (full history), "model_used" (logs only, never the UI)}.
    """
    history = session_messages or []
    checked = guardrails.check_input(prompt)
    if checked["blocked"]:
        return {"reply": checked["text"], "messages": history, "model_used": None}

    facts = {**memory.load_facts(patient_id), **(patient_facts or {})}
    system_prompt = SYSTEM_PROMPT + "\n" + _patient_context(patient_id, patient_name, facts)
    reply, messages, model_used = run_with_fallback(prompt, history, TOOLS, system_prompt)
    if model_used is None:  # every model failed: busy message, nothing to guard or remember
        return {"reply": reply, "messages": messages, "model_used": None}

    guarded = guardrails.check_output(reply)
    if guarded["text"] != reply:
        messages = _replace_last_reply(messages, guarded["text"])

    new_facts = {"name": patient_name}
    booked = booked_doctor(messages[len(history):])
    if booked:
        new_facts["preferred_doctor"], new_facts["preferred_doctor_department"] = booked
    memory.save_turn(patient_id, session_id, prompt, guarded["text"], new_facts)
    return {"reply": guarded["text"], "messages": messages, "model_used": model_used}
