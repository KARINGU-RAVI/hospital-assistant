"""AgentCore entrypoint (BedrockAgentCoreApp). Same file runs locally (port 8080) and on AgentCore Runtime.

Run locally: python -m agent.main   -> POST http://localhost:8080/invocations, GET /ping

API checked: bedrock-agentcore 1.27.0 (from bedrock_agentcore.runtime import BedrockAgentCoreApp;
@app.entrypoint handler(payload); routes /invocations and /ping; app.run(port=8080)) and the AgentCore
HTTP protocol contract (port 8080, JSON in/out).

Payload: {"prompt", "session_id", "patient_id", "patient_name", "messages"?}
Response: {"reply", "messages"}  (never model or fallback details)
"""
from __future__ import annotations

import logging
import re

from bedrock_agentcore.runtime import BedrockAgentCoreApp

from agent.agent import chat
from agent.cli import setup_logging

log = logging.getLogger(__name__)
app = BedrockAgentCoreApp()

ERROR_REPLY = "Sorry, something went wrong on our side. Please try again in a minute."
MAX_PROMPT_CHARS = 2000


def _clean_id(value, default: str) -> str:
    """Patient IDs become AgentCore actor IDs: letters, digits, - and _ only."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))[:64]
    return cleaned or default


def handle(payload: dict) -> dict:
    """Validate the payload and run one chat turn."""
    if not isinstance(payload, dict):
        return {"reply": "Please send a JSON object with a prompt.", "messages": []}
    prompt = str(payload.get("prompt") or "").strip()[:MAX_PROMPT_CHARS]
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    if not prompt:
        return {"reply": "Please type a message.", "messages": messages}
    result = chat(
        prompt,
        messages,
        patient_id=_clean_id(payload.get("patient_id"), "guest"),
        patient_name=str(payload.get("patient_name") or "Guest").strip()[:80] or "Guest",
        session_id=str(payload.get("session_id") or "local"),
    )
    return {"reply": result["reply"], "messages": result["messages"]}


@app.entrypoint
def invoke(payload):
    """AgentCore entrypoint: never leaks stack traces or model details to the caller."""
    try:
        return handle(payload)
    except Exception:
        log.exception("invoke failed")
        messages = payload.get("messages", []) if isinstance(payload, dict) else []
        return {"reply": ERROR_REPLY, "messages": messages if isinstance(messages, list) else []}


if __name__ == "__main__":
    setup_logging()
    app.run()
