"""Creates data/hospital.db with fake departments, doctors, slots, bookings, patient_facts.

Run: python data/seed.py   (deletes and recreates the DB every time)
"""
from __future__ import annotations

import random
import sys
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow `python data/seed.py`

from agent.config import get_settings  # noqa: E402
from agent.db import connect  # noqa: E402

SCHEMA = """
CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL
);
CREATE TABLE doctors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    department_id INTEGER NOT NULL REFERENCES departments(id)
);
CREATE TABLE slots (
    id INTEGER PRIMARY KEY,
    doctor_id INTEGER NOT NULL REFERENCES doctors(id),
    date TEXT NOT NULL,
    time TEXT NOT NULL,
    UNIQUE (doctor_id, date, time)
);
CREATE TABLE bookings (
    id TEXT PRIMARY KEY,
    slot_id INTEGER NOT NULL REFERENCES slots(id),
    patient_id TEXT NOT NULL,
    patient_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('booked', 'cancelled')),
    created_at TEXT NOT NULL
);
-- A slot can have only ONE active booking; cancelled rows are kept as history.
CREATE UNIQUE INDEX one_active_booking_per_slot ON bookings(slot_id) WHERE status = 'booked';
CREATE TABLE patient_facts (
    patient_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (patient_id, key)
);
"""

DEPARTMENTS = [
    ("General Medicine", "Fever, cold, general check-ups and anything not listed elsewhere."),
    ("Orthopedics", "Bones, joints, muscles, back and sports injuries."),
    ("ENT", "Ear, nose and throat."),
    ("Pediatrics", "Children up to 14 years."),
]
DOCTORS = [
    ("Dr. Sharma", "General Medicine"),
    ("Dr. Iyer", "General Medicine"),
    ("Dr. Rao", "Orthopedics"),
    ("Dr. Kapoor", "Orthopedics"),
    ("Dr. Meena", "ENT"),
    ("Dr. Fernandes", "Pediatrics"),
]
TIMES = ["10:00", "11:30", "15:00", "16:30"]
DAYS = 7
PREBOOKED = 5
FAKE_PATIENTS = [("P-9001", "Asha K"), ("P-9002", "Vikram S"), ("P-9003", "Lata M")]


def seed(db_path: Path | str | None = None, today: date | None = None) -> dict[str, int]:
    """Delete and recreate the DB. Same data every run (seeded random). Returns table counts."""
    path = Path(db_path or get_settings().db_path)
    today = today or date.today()
    rng = random.Random(42)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)

    with closing(connect(path)) as conn, conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT INTO departments(name, description) VALUES (?, ?)", DEPARTMENTS)
        dept_ids = {name: i for i, name in conn.execute("SELECT id, name FROM departments")}
        conn.executemany(
            "INSERT INTO doctors(name, department_id) VALUES (?, ?)",
            [(name, dept_ids[dept]) for name, dept in DOCTORS],
        )
        doctor_ids = [r[0] for r in conn.execute("SELECT id FROM doctors ORDER BY id")]
        conn.executemany(
            "INSERT INTO slots(doctor_id, date, time) VALUES (?, ?, ?)",
            [
                (doc, (today + timedelta(days=d)).isoformat(), t)
                for doc in doctor_ids
                for d in range(DAYS)
                for t in TIMES
            ],
        )
        slot_ids = [r[0] for r in conn.execute("SELECT id FROM slots ORDER BY id")]
        now = datetime.now().isoformat(timespec="seconds")
        for n, slot_id in enumerate(sorted(rng.sample(slot_ids, PREBOOKED))):
            pid, pname = rng.choice(FAKE_PATIENTS)
            conn.execute(
                "INSERT INTO bookings VALUES (?, ?, ?, ?, 'booked', ?)",
                (f"HSP-{1001 + n}", slot_id, pid, pname, now),
            )
        return {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("departments", "doctors", "slots", "bookings")
        }


if __name__ == "__main__":
    counts = seed()
    print(f"Created {get_settings().db_path}")
    for table, count in counts.items():
        print(f"  {table:12} {count}")
