# Phase 1 — Data and tools (about 2 h, ₹0)

## Official docs to check
Check these first (see CLAUDE.md "Verify before you code"). If a link is dead or moved,
find the current official page and update it here.
- SQLite partial indexes: https://www.sqlite.org/partialindex.html

## Goal
Fake clinic data in SQLite and four plain-Python tools, fully tested. No model yet.

## Data (`data/seed.py`)
Creates `data/hospital.db` (delete and recreate on every run).

Tables:
- `departments(id, name, description)` — General Medicine, Orthopedics, ENT, Pediatrics
- `doctors(id, name, department_id)` — 6 doctors, e.g. Dr. Rao (Orthopedics), Dr. Meena (ENT)…
- `slots(id, doctor_id, date, time, UNIQUE(doctor_id, date, time))` — next 7 days,
  4 slots a day per doctor (10:00, 11:30, 15:00, 16:30)
- `bookings(id TEXT PRIMARY KEY, slot_id UNIQUE, patient_id, patient_name, status, created_at)`
  — booking IDs look like `HSP-1001`; status `booked` / `cancelled`
- `patient_facts(patient_id, key, value, updated_at, PRIMARY KEY(patient_id, key))`
  — used later as the local Memory fallback

Use a seeded random generator so data is the same on every run. A few slots are pre-booked so
"no slots free" can be tested.

## Tools (`agent/tools/`)
Write each as a plain function with type hints and a clear docstring (the docstring becomes the tool
description for the model). Phase 2 wraps them with Strands' `@tool`. All return small dicts / lists,
never raise to the model — errors come back as `{"error": "..."}`.

| Function | Inputs | Returns |
|---|---|---|
| `find_department(symptom: str)` | free text | `{"department": "...", "reason": "..."}` from a keyword map; unknown → General Medicine. NEVER a diagnosis |
| `check_availability(department: str \| None, doctor: str \| None, date: str)` | date as `YYYY-MM-DD`; also accept "today"/"tomorrow" | list of `{doctor, department, date, time, slot_id}` free slots, max 8 |
| `book_appointment(slot_id: int, patient_id: str, patient_name: str)` | | `{"booking_id", "doctor", "date", "time"}` |
| `manage_booking(booking_id: str, action: "view" \| "cancel")` | | booking details, or confirmation of cancel |

Rules:
- `book_appointment` is **idempotent**: if this patient already has a `booked` booking for that slot,
  return the same booking ID. If another patient holds it → `{"error": "slot already taken"}`.
- Cancelling frees the slot (status `cancelled`, slot can be booked again).
- Dates in the past → error.
- A tiny `db.py` helper (or function in booking.py) opens the connection; DB path comes from config.

## Tests (`tests/test_tools.py`) — offline, use a temp DB via a fixture
- symptom routing: "knee pain" → Orthopedics, "ear ache" → ENT, "fever in my child" → Pediatrics, gibberish → General Medicine
- availability returns only free slots; pre-booked slot not listed
- booking returns an ID; booking the same slot again by the same patient returns the SAME ID
- another patient booking that slot gets an error
- view and cancel work; cancelled slot shows as free again
- past date returns an error

## Done when
- [ ] `python data/seed.py` creates the DB and prints counts (departments, doctors, slots)
- [ ] `pytest -q` — all tool tests pass
