"""find_department(symptom) -> department via keyword map. Never diagnoses."""
from __future__ import annotations

import re
import sqlite3
from contextlib import closing

from agent.db import connect

DEFAULT_DEPARTMENT = "General Medicine"

# Checked in order: Pediatrics first so "fever in my child" goes to Pediatrics, not General Medicine.
KEYWORDS: list[tuple[str, list[str], str]] = [
    (
        "Pediatrics",
        ["child", "children", "kid", "kids", "baby", "infant", "toddler", "son", "daughter", "newborn"],
        "Patients under 14 are seen by Pediatrics.",
    ),
    (
        "ENT",
        ["ear", "ears", "earache", "nose", "nasal", "sinus", "throat", "tonsil", "hearing", "voice"],
        "Ear, nose and throat concerns are seen by ENT.",
    ),
    (
        "Orthopedics",
        ["knee", "joint", "joints", "bone", "bones", "back", "spine", "shoulder", "hip", "ankle",
         "wrist", "elbow", "neck", "fracture", "sprain", "muscle", "sports injury"],
        "Bone, joint and muscle concerns are seen by Orthopedics.",
    ),
]


def find_department(symptom: str) -> dict:
    """Suggest which clinic department a patient should book with, based on what they describe.

    This only routes to a department; it never diagnoses or names a condition.

    Args:
        symptom: The patient's own words, e.g. "knee pain" or "fever in my child".

    Returns:
        {"department": name, "reason": short routing reason}. Unknown symptoms go to General Medicine.
    """
    text = (symptom or "").lower()
    words = set(re.findall(r"[a-z]+", text))
    for department, keywords, reason in KEYWORDS:
        if any((k in text) if " " in k else (k in words) for k in keywords):
            return {"department": department, "reason": reason}
    return {
        "department": DEFAULT_DEPARTMENT,
        "reason": "General Medicine can see you first and refer you if needed.",
    }


def list_doctors(department: str | None = None) -> dict:
    """List the clinic's doctors and their departments (the only source of truth for doctor names).

    Args:
        department: Only doctors in this department, e.g. "ENT" (optional).

    Returns:
        {"doctors": [{"doctor", "department"}]} or {"error": ...}.
    """
    sql = "SELECT d.name AS doctor, dep.name AS department FROM doctors d JOIN departments dep ON dep.id = d.department_id"
    params: list = []
    if department:
        sql += " WHERE lower(dep.name) LIKE ?"
        params.append(f"%{department.strip().lower()}%")
    sql += " ORDER BY dep.name, d.name"
    try:
        with closing(connect()) as conn:
            doctors = [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.Error:
        return {"error": "could not list doctors right now"}
    return {"doctors": doctors} if doctors else {"error": f"no department matching {department!r}"}
