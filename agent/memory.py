"""AgentCore Memory load/save with local SQLite (patient_facts) fallback.

API checked: bedrock-agentcore 1.27.0 MemoryClient.create_event(memory_id, actor_id, session_id,
messages=[(text, "USER"|"ASSISTANT")]) and retrieve_memories(memory_id, namespace=..., query=..., top_k=...)
(records have content.text); namespace template "/patients/{actorId}/preferences/" (AgentCore devguide,
namespaceTemplates). Local facts are always kept: they are the fallback AND the fast path, because
long-term extraction on AWS is asynchronous.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from functools import lru_cache

from agent.config import get_settings
from agent.db import connect

log = logging.getLogger(__name__)

NAMESPACE_TEMPLATE = "/patients/{actorId}/preferences/"
PREFERENCE_QUERY = "patient preferences for clinic appointments, doctors and times"
MAX_PREFERENCES = 3


def namespace_for(patient_id: str) -> str:
    return NAMESPACE_TEMPLATE.replace("{actorId}", patient_id)


def safe_session_id(session_id: str) -> str:
    """AgentCore sessionId pattern is [a-zA-Z0-9][a-zA-Z0-9-_]*, max 100 chars."""
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", session_id or "local").lstrip("-_")[:100]
    return cleaned or "local"


# --- local SQLite facts ---------------------------------------------------------------------------

def load_local_facts(patient_id: str) -> dict[str, str]:
    try:
        with closing(connect()) as conn:
            rows = conn.execute("SELECT key, value FROM patient_facts WHERE patient_id = ?", (patient_id,))
            return {r["key"]: r["value"] for r in rows}
    except sqlite3.Error as e:
        log.warning("local facts unavailable: %s", type(e).__name__)
        return {}


def save_local_facts(patient_id: str, facts: dict[str, str]) -> None:
    if not facts:
        return
    now = datetime.now().isoformat(timespec="seconds")
    try:
        with closing(connect()) as conn, conn:
            conn.executemany(
                "INSERT INTO patient_facts VALUES (?, ?, ?, ?) "
                "ON CONFLICT(patient_id, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                [(patient_id, k, str(v), now) for k, v in facts.items()],
            )
    except sqlite3.Error as e:
        log.warning("local facts not saved: %s", type(e).__name__)


# --- AgentCore Memory -----------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _client(region: str):
    from bedrock_agentcore.memory import MemoryClient

    return MemoryClient(region_name=region)


def _enabled() -> bool:
    s = get_settings()
    return s.memory_enabled and bool(s.memory_id)


def load_facts(patient_id: str) -> dict[str, str]:
    """Local facts, plus long-term preferences from AgentCore Memory when enabled."""
    facts = load_local_facts(patient_id)
    if not _enabled():
        return facts
    s = get_settings()
    try:
        records = _client(s.aws_region).retrieve_memories(
            memory_id=s.memory_id, namespace=namespace_for(patient_id), query=PREFERENCE_QUERY, top_k=MAX_PREFERENCES
        )
        texts = [r.get("content", {}).get("text", "").strip() for r in records]
        if any(texts):
            facts["preferences"] = "; ".join(t for t in texts if t)
    except Exception as e:  # any AWS / network / credentials error
        log.warning("fallback: memory -> local (%s)", type(e).__name__)
    return facts


def save_turn(patient_id: str, session_id: str, user_text: str, reply_text: str, facts: dict[str, str]) -> None:
    """Save facts locally (always) and the turn as a conversational event (when enabled)."""
    save_local_facts(patient_id, facts)
    if not _enabled():
        return
    s = get_settings()
    try:
        _client(s.aws_region).create_event(
            memory_id=s.memory_id,
            actor_id=patient_id,
            session_id=safe_session_id(session_id),
            messages=[(user_text, "USER"), (reply_text, "ASSISTANT")],
        )
    except Exception as e:
        log.warning("fallback: memory -> local (%s)", type(e).__name__)
