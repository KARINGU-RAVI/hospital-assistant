"""Builds the Strands agent: system prompt, tools, chat() flow.

Phase 2: model only. Phase 4 adds guardrail -> memory -> model -> guardrail -> memory around it.
Tool wrappers here keep results compact (free-tier token limits); agent/tools/*.py stay plain functions.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from strands import ToolContext, tool

from agent import guardrails, memory
from agent.db import connect
from agent.models import run_with_fallback
from agent.tools import booking
from agent.tools.booking import SLOT_SELECT, manage_booking
from agent.tools.departments import find_department
from agent.tools.rag import search_hospital_info

NEEDS_CONFIRMATION = "needs_confirmation"

SYSTEM_PROMPT = """You are the front-desk assistant of Sunrise Clinic (a fictional clinic). You help patients
find the right department, check doctor availability, and book, view or cancel appointments.

Rules:
- Use the tools for every fact about departments, doctors, slots and bookings. Never invent any.
- For symptoms, call find_department and name the department. Never diagnose, never guess a
  condition, never suggest medicines or doses. For medical questions, say a clinic doctor will
  advise them and offer to book.
- EMERGENCY (chest pain, trouble breathing, heavy bleeding, someone unconscious, anything
  life-threatening): tell them to call 108 or go to the nearest emergency room now. Do not book.
- Booking has two steps. The first book_appointment call returns "needs_confirmation" with the
  doctor, date and time: repeat them and ask the patient to confirm. Only after they say yes in
  their next message, call book_appointment again with the same slot id. Do this even if the
  patient already named the slot.
- If the patient gives a time but no date, ask which day, or offer the next day with free slots.
  Never assume today.
- For clinic questions (hours, departments, insurance, fees, parking, documents, cancellations), call
  search_hospital_info and answer only from its answers, naming the source file. If it returns no
  answers, say "I don't have that information, please contact the front desk".
- After booking, give the booking ID (starts with HSP-).
- Never show slot ids or other internal IDs (booking IDs are fine).
- If the patient asks for their usual doctor, use preferred_doctor from what is known about them.
- Keep replies short: two or three sentences, or a short list of slots.
"""


def _patient_context(patient_id: str, patient_name: str, patient_facts: dict[str, str] | None) -> str:
    lines = [
        f"Now: {booking._now().strftime('%A %Y-%m-%d %H:%M')} (clinic local time).",
        f"Patient: {patient_name} (patient_id {patient_id}). Use exactly these when booking; don't ask for them.",
    ]
    if patient_facts:
        lines.append("Known about this patient: " + "; ".join(f"{k}: {v}" for k, v in patient_facts.items()))
    return "\n".join(lines)


@tool
def check_availability(date: str, department: str | None = None, doctor: str | None = None) -> dict:
    """List free appointment slots on a date, optionally for one department or doctor.

    Args:
        date: YYYY-MM-DD, or "today" / "tomorrow".
        department: Department name, e.g. "Orthopedics" (optional).
        doctor: Doctor name or surname, e.g. "Rao" (optional).

    Returns:
        {"date", "slots": [{"id", "doctor", "time"}]} (max 8), or {"error": ...}.
    """
    result = booking.check_availability(date, department=department, doctor=doctor)
    if isinstance(result, dict):
        return result
    day = result[0]["date"] if result else date
    return {"date": day, "slots": [{"id": s["slot_id"], "doctor": s["doctor"], "time": s["time"]} for s in result]}


def _last_patient_message(messages: list[dict]) -> int:
    """Index of the patient's latest typed message (tool results are also role 'user'; skip them)."""
    for i in range(len(messages) - 1, -1, -1):
        content = messages[i].get("content", [])
        if messages[i].get("role") == "user" and not any("toolResult" in block for block in content):
            return i
    return -1


def confirmation_requested(history: list[dict], slot_id: int) -> bool:
    """True if book_appointment asked the patient to confirm this slot anywhere in `history`.

    `history` must hold only EARLIER turns, so the patient has replied since the request.
    """
    asked: set[str] = set()
    for message in history:
        for block in message.get("content", []):
            use = block.get("toolUse")
            if use and use.get("name") == "book_appointment" and str(use.get("input", {}).get("slot_id")) == str(slot_id):
                asked.add(use.get("toolUseId"))
            result = block.get("toolResult")
            if result and result.get("toolUseId") in asked:
                if any(NEEDS_CONFIRMATION in c.get("text", "") for c in result.get("content", [])):
                    return True
    return False


def _earlier_turns(tool_context: ToolContext) -> list[dict]:
    """Full history before this turn (untrimmed, from chat()); else the agent's messages before the
    patient's latest message."""
    history = (tool_context.invocation_state or {}).get("session_history")
    if history is not None:
        return history
    messages = tool_context.agent.messages
    return messages[: max(_last_patient_message(messages), 0)]


def _slot_details(slot_id: int) -> dict:
    try:
        with closing(connect()) as conn:
            row = conn.execute(SLOT_SELECT + " WHERE s.id = ?", (slot_id,)).fetchone()
    except sqlite3.Error:
        return {"error": "could not look up that slot right now"}
    if row is None:
        return {"error": "no such slot"}
    if booking.slot_has_passed(row["date"], row["time"]):
        return {"error": "that time has already passed"}
    return {"doctor": row["doctor"], "date": row["date"], "time": row["time"]}


@tool(context=True)
def book_appointment(slot_id: int, patient_id: str, patient_name: str, tool_context: ToolContext) -> dict:
    """Book a slot in two steps. First call: returns needs_confirmation with doctor, date, time (not
    booked). After the patient says yes in their next message, call again with the same slot_id to book.

    Args:
        slot_id: The slot id from check_availability.
        patient_id: The patient's ID.
        patient_name: The patient's full name.

    Returns:
        {"result": "needs_confirmation", ...}, {"booking_id", "doctor", "date", "time"}, or {"error": ...}.
    """
    if confirmation_requested(_earlier_turns(tool_context), slot_id):
        return booking.book_appointment(slot_id, patient_id, patient_name)
    details = _slot_details(slot_id)
    if "error" in details:
        return details
    return {"result": NEEDS_CONFIRMATION, **details}


TOOLS = [tool(find_department), check_availability, book_appointment, tool(manage_booking), tool(search_hospital_info)]


def booked_doctor(new_messages: list[dict]) -> str | None:
    """Doctor of a booking that succeeded in this turn (from book_appointment tool results), if any."""
    doctor = None
    for message in new_messages:
        for block in message.get("content", []):
            for item in block.get("toolResult", {}).get("content", []):
                try:
                    data = json.loads(item.get("text", ""))
                except (TypeError, ValueError):
                    continue
                if isinstance(data, dict) and data.get("booking_id") and data.get("doctor"):
                    doctor = data["doctor"]
    return doctor


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
    doctor = booked_doctor(messages[len(history):])
    if doctor:
        new_facts["preferred_doctor"] = doctor
    memory.save_turn(patient_id, session_id, prompt, guarded["text"], new_facts)
    return {"reply": guarded["text"], "messages": messages, "model_used": model_used}
