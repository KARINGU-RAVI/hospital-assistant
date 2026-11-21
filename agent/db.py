"""SQLite connection helper (path from config)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from agent.config import get_settings


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open the clinic DB with foreign keys on and rows readable by column name.

    Callers must close the connection (use `contextlib.closing`), or Windows keeps the file locked.
    """
    conn = sqlite3.connect(db_path or get_settings().db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn
