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
# Tele-MANAS (Government of India, 24x7): "The public can access the Tele MANAS helpline by dialing
# toll-free helpline number 1800-89-14416 or short code 14416." Source (checked 2026-11-21):
# https://dghs.mohfw.gov.in/national-mental-health-programme.php
# 108 = national emergency ambulance number (National Health Mission / PIB pages on Dial 108).
SELF_HARM_MESSAGE = (
    "I'm really sorry you're feeling this way. You don't have to go through this alone. "
    "If you are in immediate danger, call 108 now or go to the nearest emergency room. "
    "You can also talk to someone at Tele-MANAS, the free 24x7 mental health helpline: "
    "dial 14416 or 1800-89-14416."
)

# Checked first: self-harm gets its own message with a helpline.
SELF_HARM = re.compile(
    r"suicid|kill(ing)? myself|end(ing)? (my|it) (life|all)|take my (own )?life|self[- ]?harm|"
    r"hurt(ing)? myself|don'?t want to (live|be alive)|want to die|better off dead",
    re.I,
)

# Always checked locally first (CLAUDE.md rule 6): emergencies never reach the model.
EMERGENCY = re.compile(
    r"chest pain|pain in my chest|can'?t breathe|cannot breathe|trouble breathing|difficulty breathing|"
    r"not breathing|choking|heavy bleeding|bleeding (a lot|heavily|badly|won'?t stop|non[- ]?stop)|"
    r"lot of blood|unconscious|not responding|fainted|passed out|collapsed|heart attack|stroke|"
    r"seizures?|\bfits?\b|convulsions?|overdose|poison(ed|ing)|severe burns?|"
    r"(head|serious) injury|hit by a (car|bike|bus|vehicle)|(bad|serious|major) accident",
    re.I,
)

# Local fallback for medical advice (the Bedrock guardrail's denied topics do this when enabled).
_MEDICINE = (
    r"(medicines?|medications?|tablets?|pills?|capsules?|syrups?|injections?|drugs?|antibiotics?|painkillers?|"
    r"paracetamol|ibuprofen|aspirin|crocin|dolo|\d+\s?mg|doses?|dosage)"
)
MEDICAL_ADVICE = re.compile(
    r"\bdoses?\b|\bdosage\b|\b\d+\s?mg\b|how (many|much) (tablets?|pills?|mg|medicine)|"
    r"(what|which) (medicine|medication|tablet|drug|antibiotic|painkiller|pill)s? (should|can|do) i|"
    # "should/can I take" only near a medicine word ("which bus should I take" is fine)
    rf"\b(should|can|could|do) i (take|have|use)\b[^.?!]{{0,40}}\b{_MEDICINE}\b|"
    rf"\b{_MEDICINE}\b[^.?!]{{0,40}}\b(should|can|could) i (take|have|use)\b|"
    r"\bprescribe\b|prescription for|"
    r"(what|which) (disease|illness|condition|infection) (do|might|could) i have|do i have (cancer|diabetes|covid|an? )|"
    # the verb "diagnose" and "my diagnosis", but not "diagnostic tests"
    r"what'?s wrong with me|\bdiagnose\b|\bmy diagnosis\b|\bdiagnosis (for|of) (me|my)\b",
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


def urgent_reply(text: str) -> str | None:
    """Self-harm or emergency message for this text, else None. Always checked locally first."""
    if SELF_HARM.search(text):
        return SELF_HARM_MESSAGE
    if EMERGENCY.search(text):
        return EMERGENCY_MESSAGE
    return None


def local_input_check(text: str) -> dict:
    """Local rules: self-harm / emergency messages, or the safe message for medical-advice questions."""
    urgent = urgent_reply(text)
    if urgent:
        return _result(True, urgent)
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
    if urgent_reply(text) or not _enabled():
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
