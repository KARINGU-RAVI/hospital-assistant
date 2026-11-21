"""Bedrock ApplyGuardrail wrapper with local keyword/regex fallback.

API checked: boto3 1.44.9 bedrock-runtime.apply_guardrail(guardrailIdentifier, guardrailVersion,
source="INPUT"|"OUTPUT", content=[{"text": {"text": ...}}]) -> {"action": "NONE"|"GUARDRAIL_INTERVENED",
"outputs": [{"text": ...}]}; "DRAFT" is a valid version (ApplyGuardrail API reference).
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from agent.config import get_settings

log = logging.getLogger(__name__)

SAFE_MESSAGE = "I can't give medical advice. Please ask the doctor at your visit. In an emergency call 108."
EMERGENCY_MESSAGE = (
    "This sounds like an emergency. Please call 108 or go to the nearest emergency room right now. "
    "I can't book appointments for emergencies."
)

# Always checked locally first (CLAUDE.md rule 6): emergencies never reach the model.
EMERGENCY = re.compile(
    r"chest pain|can'?t breathe|cannot breathe|trouble breathing|difficulty breathing|not breathing|"
    r"heavy bleeding|bleeding (a lot|heavily|won'?t stop)|unconscious|fainted|collapsed|heart attack|"
    r"stroke|seizure|overdose|suicid|kill myself",
    re.I,
)

# Local fallback for medical advice (the Bedrock guardrail's denied topics do this when enabled).
MEDICAL_ADVICE = re.compile(
    r"\bdose\b|\bdosage\b|\bdoses\b|\b\d+\s?mg\b|how (many|much) (tablets?|pills?|mg|medicine)|"
    r"(what|which) (medicine|medication|tablet|drug|antibiotic|painkiller|pill)s? (should|can|do) i|"
    r"should i take|can i take .*(medicine|tablet|pill|paracetamol|ibuprofen|antibiotic)|"
    r"prescribe|prescription for|"
    r"(what|which) (disease|illness|condition|infection) (do|might|could) i have|do i have (cancer|diabetes|covid|an? )|"
    r"what'?s wrong with me|\bdiagnos(e|is)\b",
    re.I,
)

# Phone numbers to mask in output: Indian mobile (6-9 + 9 digits, optional +91/91),
# Indian landline (0 + STD code + number), international (+country code ...).
PHONE = re.compile(
    r"(?<![\w-])(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"
    r"|(?<![\w-])0\d{2,4}[\s-]?\d{3,4}[\s-]?\d{4}(?!\d)"
    r"|\+\d{1,3}[\s-]?\d(?:[\s-]?\d){6,13}(?!\d)"
)


def _result(blocked: bool, text: str) -> dict:
    return {"blocked": blocked, "text": text}


def local_input_check(text: str) -> dict:
    """Local rules: emergency message, or safe message for medical-advice questions."""
    if EMERGENCY.search(text):
        return _result(True, EMERGENCY_MESSAGE)
    if MEDICAL_ADVICE.search(text):
        return _result(True, SAFE_MESSAGE)
    return _result(False, text)


def mask_phones(text: str) -> str:
    """Replace phone numbers with [PHONE]."""
    return PHONE.sub("[PHONE]", text)


@lru_cache(maxsize=1)
def _client(region: str):
    import boto3

    return boto3.client("bedrock-runtime", region_name=region)


def _apply(text: str, source: str) -> tuple[bool, str]:
    """Call ApplyGuardrail. Returns (intervened, text to use)."""
    s = get_settings()
    response = _client(s.aws_region).apply_guardrail(
        guardrailIdentifier=s.guardrail_id,
        guardrailVersion=s.guardrail_version,
        source=source,
        content=[{"text": {"text": text}}],
    )
    intervened = response.get("action") == "GUARDRAIL_INTERVENED"
    out = "".join(o.get("text", "") for o in response.get("outputs", []))
    return intervened, (out or text) if intervened else text


def _enabled() -> bool:
    s = get_settings()
    return s.guardrails_enabled and bool(s.guardrail_id)


def check_input(text: str) -> dict:
    """{"blocked", "text"}: blocked=True means reply with `text` and don't call the model."""
    emergency = EMERGENCY.search(text)
    if emergency or not _enabled():
        return local_input_check(text)
    try:
        intervened, _ = _apply(text, "INPUT")
        return _result(True, SAFE_MESSAGE) if intervened else _result(False, text)
    except Exception as e:  # any AWS / network / credentials error
        log.warning("fallback: guardrails -> local (%s)", type(e).__name__)
        return local_input_check(text)


def check_output(text: str) -> dict:
    """{"blocked", "text"}: text is the reply to show (phone numbers masked, or the safe message)."""
    if not _enabled():
        return _result(False, mask_phones(text))
    try:
        intervened, out = _apply(text, "OUTPUT")
        blocked = intervened and out.strip() == SAFE_MESSAGE
        return _result(blocked, out)
    except Exception as e:
        log.warning("fallback: guardrails -> local (%s)", type(e).__name__)
        return _result(False, mask_phones(text))
