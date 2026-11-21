"""Model factory (gemini / groq / openrouter / bedrock) and run_with_fallback() that tries MODEL_CHAIN in order.

Retries: Strands' and the SDKs' own retries are switched off, so a failing model hands over to the
next one immediately. Only the LAST usable model in the chain gets one extra try after a short wait,
so a single network blip doesn't end the whole turn.
"""
from __future__ import annotations

import copy
import json
import logging
import time
from typing import Any

import httpx
import openai
from botocore.exceptions import ClientError as BotoClientError
from botocore.exceptions import NoCredentialsError
from strands import Agent
from strands.models import Model
from strands.types.exceptions import ModelThrottledException

from agent.config import Settings, get_settings

log = logging.getLogger(__name__)

BUSY_MESSAGE = "The assistant is busy, please try again in a minute."
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
TIMEOUT_S = 30
LAST_MODEL_RETRY_WAIT_S = 2.0
PARAMS = {"temperature": 0.2}
# Bedrock errors that mean "Bedrock can't serve us right now" (no access, throttled, model access
# not set up). Codes from the Bedrock troubleshooting page and the Converse API reference.
BEDROCK_UNAVAILABLE_CODES = {
    "AccessDeniedException", "ThrottlingException", "NotAuthorized",
    "MPAgreementBeingCreated", "FTUFormNotFilled", "ResourceNotFoundException",
}
# ValidationException falls back ONLY with these messages (model not usable by this account/region);
# any other ValidationException is likely our own request bug and must raise.
# Wording from installed strands-agents 1.65.0 (models/bedrock.py); AWS docs don't publish these strings.
# "isn" avoids the straight vs curly apostrophe in "isn't".
BEDROCK_MODEL_UNAVAILABLE_MESSAGES = ("with on-demand throughput isn", "the provided model identifier is invalid")


def _openai_compatible(api_key: str, base_url: str, model_id: str) -> Model:
    from strands.models.openai import OpenAIModel

    return OpenAIModel(
        client_args={"api_key": api_key, "base_url": base_url, "max_retries": 0, "timeout": TIMEOUT_S},
        model_id=model_id,
        params=PARAMS,
    )


def build_model(name: str) -> Model:
    """Return a Strands model object for one name in MODEL_CHAIN."""
    s = get_settings()
    if name == "gemini":
        from strands.models.gemini import GeminiModel

        return GeminiModel(
            client_args={"api_key": s.gemini_api_key, "http_options": {"timeout": TIMEOUT_S * 1000}},
            model_id=s.gemini_model_id,
            params=PARAMS,
        )
    if name == "groq":
        return _openai_compatible(s.groq_api_key, GROQ_BASE_URL, s.groq_model_id)
    if name == "openrouter":
        return _openai_compatible(s.openrouter_api_key, OPENROUTER_BASE_URL, s.openrouter_model_id)
    if name == "bedrock":
        from strands.models import BedrockModel

        return BedrockModel(model_id=s.bedrock_model_id, region_name=s.aws_region)
    raise ValueError(f"unknown model name in MODEL_CHAIN: {name!r}")


def is_configured(name: str, s: Settings) -> bool:
    """True if the model has what it needs (key + model ID). Bedrock uses AWS credentials instead."""
    return {
        "gemini": bool(s.gemini_api_key and s.gemini_model_id),
        "groq": bool(s.groq_api_key and s.groq_model_id),
        "openrouter": bool(s.openrouter_api_key and s.openrouter_model_id),
        "bedrock": bool(s.bedrock_model_id),
    }.get(name, True)  # unknown names reach build_model and raise there


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "code"):  # openai uses status_code, google-genai uses code
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def _causes(exc: BaseException) -> list[BaseException]:
    """The exception followed by its __cause__ chain (Strands wraps SDK errors)."""
    chain: list[BaseException] = []
    seen: BaseException | None = exc
    while seen is not None and seen not in chain:
        chain.append(seen)
        seen = seen.__cause__
    return chain


def _is_bedrock_unavailable(exc: BaseException) -> bool:
    if isinstance(exc, NoCredentialsError):
        return True
    if not isinstance(exc, BotoClientError):
        return False
    error = exc.response.get("Error", {})
    if error.get("Code") in BEDROCK_UNAVAILABLE_CODES:
        return True
    message = str(error.get("Message", "")).lower()
    return error.get("Code") == "ValidationException" and any(m in message for m in BEDROCK_MODEL_UNAVAILABLE_MESSAGES)


def fallback_reason(exc: BaseException) -> str | None:
    """Short reason if this error should move on to the next model, else None (the error must surface).

    Checks the exception and its causes, because Strands sometimes wraps the SDK error.
    Bedrock errors are checked first: Strands turns a Bedrock ThrottlingException into
    ModelThrottledException, and we want it logged as "bedrock-unavailable".
    """
    causes = _causes(exc)
    if any(_is_bedrock_unavailable(e) for e in causes):
        return "bedrock-unavailable"
    for seen in causes:
        if isinstance(seen, ModelThrottledException):
            return "429"
        code = _status_code(seen)
        message = str(seen).lower()
        if code in (401, 403) or (code == 400 and "api key" in message and "valid" in message):
            return "auth"
        if code == 404:
            return "model"  # model ID retired or not available to this key
        if code == 429:
            return "429"
        if code is not None and 500 <= code < 600:
            return str(code)
        if isinstance(seen, (openai.APITimeoutError, httpx.TimeoutException, TimeoutError)):
            return "timeout"
        if isinstance(seen, (openai.APIConnectionError, httpx.TransportError, ConnectionError)):
            return "connection"
    return None


def is_fallback_error(exc: BaseException) -> bool:
    """True for rate limits, auth, model-not-found, Bedrock-unavailable, timeouts, connection errors, 5xx."""
    return fallback_reason(exc) is not None


def _is_patient_message(message: dict) -> bool:
    """A message the patient typed (tool results are also role 'user', so exclude them)."""
    return message.get("role") == "user" and not any("toolResult" in b for b in message.get("content", []))


def approx_tokens(messages: list[dict]) -> int:
    """Rough token count (characters / 4). No tokenizer needed; good enough for a budget."""
    return len(json.dumps(messages, ensure_ascii=False, default=str)) // 4


def _window(messages: list[dict], max_messages: int, max_tokens: int) -> list[dict]:
    """The longest recent part within both limits that starts at a patient message (or [])."""
    for start in range(len(messages)):
        tail = messages[start:]
        if len(tail) <= max_messages and _is_patient_message(tail[0]) and approx_tokens(tail) <= max_tokens:
            return tail
    return []


def text_only(messages: list[dict]) -> list[dict]:
    """Each turn as two messages: the patient's text and the assistant's last text reply.

    Tool calls and their results are dropped together, so no tool call loses its result.
    """
    out: list[dict] = []
    reply: dict | None = None
    for message in messages:
        if _is_patient_message(message):
            if reply:
                out.append(reply)
            out.append(message)
            reply = None
        elif message.get("role") == "assistant":
            texts = [b for b in message.get("content", []) if "text" in b and b["text"].strip()]
            if texts:
                reply = {"role": "assistant", "content": texts}
    if reply:
        out.append(reply)
    return out


def trim_history(messages: list[dict], max_messages: int, max_tokens: int) -> list[dict]:
    """Recent history within both limits for the model, never cutting a tool call from its result.

    First the raw recent turns (starting at a patient message). If even the last turn is too big
    (several tool calls), fall back to a text-only version (patient text + assistant reply per turn),
    so the model still knows what the patient is answering, e.g. "yes" to a booking question.
    """
    return _window(messages, max_messages, max_tokens) or _window(text_only(messages), max_messages, max_tokens)


def run_with_fallback(
    prompt: str, messages: list[dict], tools: list[Any], system_prompt: str
) -> tuple[str, list[dict], str | None]:
    """Run one chat turn, trying each model in MODEL_CHAIN until one answers.

    The model sees only a trimmed history (free-tier token limits); the returned messages are the
    FULL history plus this turn. Tools get the full history as invocation_state["session_history"].

    Returns (reply_text, new_messages, model_used). If every model fails, returns the polite busy
    message, the original messages unchanged, and model_used=None.
    """
    s = get_settings()
    trimmed = trim_history(messages, s.history_max_messages, s.history_max_tokens)
    chain = [name for name in s.model_chain if is_configured(name, s)]
    for skipped in set(s.model_chain) - set(chain):
        log.info("model %s skipped: not configured", skipped)

    for i, name in enumerate(chain):
        is_last = i == len(chain) - 1
        attempts = 2 if is_last else 1
        for attempt in range(1, attempts + 1):
            try:
                agent = Agent(
                    model=build_model(name),
                    tools=tools,
                    system_prompt=system_prompt,
                    messages=copy.deepcopy(trimmed),
                    callback_handler=None,
                    retry_strategy=None,  # Strands: max_attempts=1, we handle fallback ourselves
                )
                result = agent(prompt, invocation_state={"session_history": copy.deepcopy(messages)})
                return str(result).strip(), messages + agent.messages[len(trimmed):], name
            except Exception as exc:
                reason = fallback_reason(exc)
                if reason is None:
                    raise
                if attempt < attempts:
                    log.warning("retry: %s (%s)", name, reason)
                    time.sleep(LAST_MODEL_RETRY_WAIT_S)
                    continue
                log.warning("fallback: %s -> %s (%s)", name, "none" if is_last else chain[i + 1], reason)
    return BUSY_MESSAGE, messages, None
