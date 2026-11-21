"""Phase 4 tests: guardrails local fallback, phone masking, and Bedrock path with a fake client. Offline."""
import logging

import pytest

from agent import config, guardrails
from agent.guardrails import EMERGENCY_MESSAGE, SAFE_MESSAGE, check_input, check_output, mask_phones


@pytest.fixture
def switches(monkeypatch):
    def apply(enabled: bool, client=None):
        monkeypatch.setenv("GUARDRAILS_ENABLED", "true" if enabled else "false")
        monkeypatch.setenv("GUARDRAIL_ID", "gr-test" if enabled else "")
        config.get_settings.cache_clear()
        if client is not None:
            monkeypatch.setattr(guardrails, "_client", lambda region: client)
    yield apply
    config.get_settings.cache_clear()


class FakeBedrock:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def apply_guardrail(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.response


# --- local rules (switch off) ---
@pytest.mark.parametrize("text", [
    "what dose of paracetamol should I take",
    "How many tablets of ibuprofen can I take?",
    "which medicine should I take for a cold",
    "can you prescribe something for my back",
    "what disease do I have, my head hurts",
    "please diagnose my rash",
    "is 500 mg too much",
])
def test_local_blocks_medical_advice(switches, text):
    switches(False)
    assert check_input(text) == {"blocked": True, "text": SAFE_MESSAGE}


@pytest.mark.parametrize("text", [
    "book me a doctor",
    "my knee hurts, can I see someone tomorrow?",
    "I have a fever, which department should I see?",
    "should I bring my prescriptions and old reports?",
    "what are the visiting hours?",
    "how much does a consultation cost?",
    "cancel booking HSP-1006",
])
def test_local_passes_normal_requests(switches, text):
    switches(False)
    assert check_input(text) == {"blocked": False, "text": text}


@pytest.mark.parametrize("text", ["I have chest pain", "my father is unconscious", "she can't breathe"])
def test_emergency_always_local_first(switches, text):
    client = FakeBedrock(response={"action": "NONE", "outputs": []})
    switches(True, client)
    assert check_input(text) == {"blocked": True, "text": EMERGENCY_MESSAGE}
    assert client.calls == []  # never sent to AWS, never to the model


# --- phone masking ---
@pytest.mark.parametrize("text, masked", [
    ("call 98765 43210", "call [PHONE]"),
    ("call +91 98765 43210 now", "call [PHONE] now"),
    ("ring 9876543210.", "ring [PHONE]."),
    ("landline 040-2345 6789", "landline [PHONE]"),
    ("US office +1 415 555 0100", "US office [PHONE]"),
])
def test_phone_numbers_masked(text, masked):
    assert mask_phones(text) == masked


@pytest.mark.parametrize("text", [
    "Booked HSP-1006 with Dr. Rao on 2026-11-22 at 10:00.",
    "Consultation costs Rs 500; address 12 Lakeview Road, Hyderabad 500001.",
    "Visiting hours are 10:00 to 12:00 and 16:00 to 19:00.",
])
def test_non_phones_untouched(text):
    assert mask_phones(text) == text


def test_output_masked_locally_when_off(switches):
    switches(False)
    assert check_output("Call us on 98765 43210.") == {"blocked": False, "text": "Call us on [PHONE]."}


# --- Bedrock path (fake client) ---
def test_bedrock_input_intervened_blocks(switches):
    client = FakeBedrock(response={"action": "GUARDRAIL_INTERVENED", "outputs": [{"text": SAFE_MESSAGE}]})
    switches(True, client)
    assert check_input("what should I take for my migraine") == {"blocked": True, "text": SAFE_MESSAGE}
    call = client.calls[0]
    assert call["guardrailIdentifier"] == "gr-test" and call["guardrailVersion"] == "DRAFT"
    assert call["source"] == "INPUT" and call["content"] == [{"text": {"text": "what should I take for my migraine"}}]


def test_bedrock_input_none_passes(switches):
    switches(True, FakeBedrock(response={"action": "NONE", "outputs": []}))
    assert check_input("book me a doctor") == {"blocked": False, "text": "book me a doctor"}


def test_bedrock_output_masking_uses_returned_text(switches):
    client = FakeBedrock(response={"action": "GUARDRAIL_INTERVENED", "outputs": [{"text": "Call {PHONE}."}]})
    switches(True, client)
    assert check_output("Call 98765 43210.") == {"blocked": False, "text": "Call {PHONE}."}
    assert client.calls[0]["source"] == "OUTPUT"


def test_bedrock_output_blocked(switches):
    switches(True, FakeBedrock(response={"action": "GUARDRAIL_INTERVENED", "outputs": [{"text": SAFE_MESSAGE}]}))
    assert check_output("Take 2 tablets of X twice a day.") == {"blocked": True, "text": SAFE_MESSAGE}


@pytest.mark.parametrize("error", [RuntimeError("no credentials"), ConnectionError("offline")])
def test_aws_error_falls_back_to_local(switches, caplog, error):
    switches(True, FakeBedrock(error=error))
    with caplog.at_level(logging.WARNING, logger="agent.guardrails"):
        assert check_input("what dose of paracetamol should I take")["blocked"] is True
        assert check_output("Call 98765 43210.")["text"] == "Call [PHONE]."
    assert [r.getMessage().split(" (")[0] for r in caplog.records] == ["fallback: guardrails -> local"] * 2


def test_switch_on_without_id_uses_local_quietly(monkeypatch, caplog):
    monkeypatch.setenv("GUARDRAILS_ENABLED", "true")
    monkeypatch.setenv("GUARDRAIL_ID", "")
    config.get_settings.cache_clear()
    with caplog.at_level(logging.WARNING):
        assert check_input("what dose should I take")["blocked"] is True
    assert not caplog.records
    config.get_settings.cache_clear()
