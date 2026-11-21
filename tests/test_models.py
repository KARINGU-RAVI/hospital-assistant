"""Phase 2 tests: model fallback chain. Offline: fake Strands models, no real API calls."""
import logging

import httpx
import openai
import pytest
from botocore.exceptions import ClientError as BotoClientError
from botocore.exceptions import NoCredentialsError
from strands.types.exceptions import ModelThrottledException

from agent import config, models
from tests.fakes import FakeModel


class GenaiStyleError(Exception):
    """Looks like google.genai.errors.ClientError: an int `code` attribute."""

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _openai_error(cls, status: int):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test"))
    return cls("error", response=response, body=None)


@pytest.fixture
def chain(monkeypatch):
    """Configure all three models with dummy keys and plug in fake models by name."""
    env = {
        "MODEL_CHAIN": "gemini,groq,openrouter",
        "BEDROCK_MODEL_ID": "b",
        "GEMINI_API_KEY": "test", "GEMINI_MODEL_ID": "g",
        "GROQ_API_KEY": "test", "GROQ_MODEL_ID": "q",
        "OPENROUTER_API_KEY": "test", "OPENROUTER_MODEL_ID": "o",
    }
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    fakes = {name: FakeModel(reply=f"hello from {name}") for name in ("bedrock", "gemini", "groq", "openrouter")}
    built: list[str] = []

    def fake_build(name):
        built.append(name)
        return fakes[name]

    sleeps: list[float] = []
    monkeypatch.setattr(models, "build_model", fake_build)
    monkeypatch.setattr(models.time, "sleep", sleeps.append)
    yield fakes, built, sleeps
    config.get_settings.cache_clear()


def _run():
    return models.run_with_fallback("hi", [], [], "system")


def test_first_model_answers(chain):
    fakes, built, _ = chain
    reply, messages, used = _run()
    assert (reply, used, built) == ("hello from gemini", "gemini", ["gemini"])
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_429_falls_back_with_one_warning(chain, caplog):
    fakes, _, _ = chain
    fakes["gemini"].errors = [ModelThrottledException("RESOURCE_EXHAUSTED")]
    with caplog.at_level(logging.WARNING, logger="agent.models"):
        reply, _, used = _run()
    assert (reply, used) == ("hello from groq", "groq")
    assert [r.getMessage() for r in caplog.records] == ["fallback: gemini -> groq (429)"]


@pytest.mark.parametrize("error", [
    GenaiStyleError(400, "API key not valid. Please pass a valid API key."),
    _openai_error(openai.AuthenticationError, 401),
    _openai_error(openai.PermissionDeniedError, 403),
])
def test_auth_errors_fall_back(chain, caplog, error):
    fakes, _, _ = chain
    fakes["gemini"].errors = [error]
    with caplog.at_level(logging.WARNING, logger="agent.models"):
        _, _, used = _run()
    assert used == "groq"
    assert "fallback: gemini -> groq (auth)" in caplog.text


@pytest.mark.parametrize("error, reason", [
    (_openai_error(openai.InternalServerError, 503), "503"),
    (openai.APITimeoutError(request=httpx.Request("POST", "https://example.test")), "timeout"),
    (httpx.ConnectError("boom"), "connection"),
])
def test_server_and_network_errors_fall_back(chain, caplog, error, reason):
    fakes, _, _ = chain
    fakes["gemini"].errors = [error]
    with caplog.at_level(logging.WARNING, logger="agent.models"):
        assert _run()[2] == "groq"
    assert f"fallback: gemini -> groq ({reason})" in caplog.text


@pytest.mark.parametrize("error", [ValueError("bug"), GenaiStyleError(400, "Invalid JSON payload")])
def test_non_fallback_error_is_raised(chain, error):
    fakes, built, _ = chain
    fakes["gemini"].errors = [error]
    with pytest.raises(type(error)):
        _run()
    assert built == ["gemini"]


def test_missing_key_is_skipped(chain, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    config.get_settings.cache_clear()
    _, built, _ = chain
    assert _run()[2] == "groq"
    assert built == ["groq"]


def test_openrouter_skipped_without_model_id(chain, monkeypatch):
    monkeypatch.setenv("OPENROUTER_MODEL_ID", "")
    config.get_settings.cache_clear()
    fakes, built, _ = chain
    fakes["gemini"].errors = [ModelThrottledException("429")]
    fakes["groq"].errors = [ModelThrottledException("429"), ModelThrottledException("429")]
    reply, _, used = _run()
    assert used is None and "openrouter" not in built
    assert fakes["groq"].calls == 2  # groq is now the last usable model, so it gets the retry


def test_last_model_retries_once_after_short_wait(chain, caplog):
    fakes, _, sleeps = chain
    fakes["gemini"].errors = [ModelThrottledException("429")]
    fakes["groq"].errors = [ModelThrottledException("429")]
    fakes["openrouter"].errors = [httpx.ConnectError("blip")]
    with caplog.at_level(logging.WARNING, logger="agent.models"):
        reply, _, used = _run()
    assert (reply, used) == ("hello from openrouter", "openrouter")
    assert sleeps == [models.LAST_MODEL_RETRY_WAIT_S]
    assert fakes["gemini"].calls == 1 and fakes["groq"].calls == 1  # no retries before the last model


def test_all_fail_returns_busy_message(chain, caplog):
    fakes, _, sleeps = chain
    for fake in fakes.values():
        fake.errors = [ModelThrottledException("429")] * 2
    history = [{"role": "user", "content": [{"text": "earlier"}]}]
    with caplog.at_level(logging.WARNING, logger="agent.models"):
        reply, messages, used = models.run_with_fallback("hi", history, [], "system")
    assert (reply, messages, used) == (models.BUSY_MESSAGE, history, None)
    assert len(sleeps) == 1
    assert "fallback: openrouter -> none (429)" in caplog.text


def test_history_is_not_mutated_on_failure(chain):
    fakes, _, _ = chain
    fakes["gemini"].errors = [ModelThrottledException("429")]
    history = [{"role": "user", "content": [{"text": "earlier"}]},
               {"role": "assistant", "content": [{"text": "hello"}]}]
    _, messages, _ = models.run_with_fallback("hi", history, [], "system")
    assert len(history) == 2 and len(messages) == 4


def test_model_not_found_falls_back(chain, caplog):
    fakes, _, _ = chain
    fakes["gemini"].errors = [_openai_error(openai.NotFoundError, 404)]
    with caplog.at_level(logging.WARNING, logger="agent.models"):
        assert _run()[2] == "groq"
    assert "fallback: gemini -> groq (model)" in caplog.text


def _boto_error(code: str, message: str = "test") -> BotoClientError:
    return BotoClientError({"Error": {"Code": code, "Message": message}}, "ConverseStream")


@pytest.mark.parametrize("error", [
    NoCredentialsError(),
    _boto_error("AccessDeniedException"),
    _boto_error("ValidationException", "Invocation of model ID x with on-demand throughput isn’t supported. "
                "Retry your request with the ID or ARN of an inference profile that contains this model."),
    _boto_error("ValidationException", "Invocation of model ID x with on-demand throughput isn't supported."),
    _boto_error("ValidationException", "The provided model identifier is invalid."),
    _boto_error("ThrottlingException"),
    ModelThrottledException("throttled"),  # how Strands wraps Bedrock throttling (cause set below)
])
def test_bedrock_unavailable_falls_back(chain, caplog, monkeypatch, error):
    monkeypatch.setenv("MODEL_CHAIN", "bedrock,gemini")
    config.get_settings.cache_clear()
    if isinstance(error, ModelThrottledException):
        error.__cause__ = _boto_error("ThrottlingException")
    fakes, _, _ = chain
    fakes["bedrock"].errors = [error]
    with caplog.at_level(logging.WARNING, logger="agent.models"):
        assert _run()[2] == "gemini"
    assert "fallback: bedrock -> gemini (bedrock-unavailable)" in caplog.text


@pytest.mark.parametrize("error", [
    _boto_error("SomethingElseException"),
    _boto_error("ValidationException", "The input fails to satisfy the constraints specified by Amazon Bedrock."),
    _boto_error("ValidationException", "A conversation must start with a user message."),
])
def test_other_bedrock_errors_are_raised(chain, monkeypatch, error):
    monkeypatch.setenv("MODEL_CHAIN", "bedrock,gemini")
    config.get_settings.cache_clear()
    fakes, built, _ = chain
    fakes["bedrock"].errors = [error]
    with pytest.raises(BotoClientError):
        _run()
    assert built == ["bedrock"]  # no fallback to gemini
