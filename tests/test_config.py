"""Phase 0 tests: config parsing and secret masking. Offline, no keys needed."""
from agent import config


def _fresh(monkeypatch, **env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    return config.get_settings()


def test_model_chain_parsed(monkeypatch):
    s = _fresh(monkeypatch, MODEL_CHAIN=" Gemini , groq ,")
    assert s.model_chain == ("gemini", "groq")


def test_booleans(monkeypatch):
    s = _fresh(monkeypatch, GUARDRAILS_ENABLED="TRUE", MEMORY_ENABLED="no")
    assert s.guardrails_enabled is True
    assert s.memory_enabled is False


def test_secrets_never_shown(monkeypatch):
    s = _fresh(monkeypatch, GEMINI_API_KEY="super-secret-value", GROQ_API_KEY="")
    shown = config.masked(s)
    assert shown["gemini_api_key"] == "set"
    assert shown["groq_api_key"] == "missing"
    assert "super-secret-value" not in str(shown)


def test_openrouter_key_never_shown(monkeypatch):
    s = _fresh(monkeypatch, OPENROUTER_API_KEY="another-secret", OPENROUTER_MODEL_ID="")
    shown = config.masked(s)
    assert shown["openrouter_api_key"] == "set"
    assert "another-secret" not in str(shown)


def test_default_chain_includes_openrouter(monkeypatch):
    monkeypatch.delenv("MODEL_CHAIN", raising=False)
    config.get_settings.cache_clear()
    assert config.get_settings().model_chain == ("gemini", "groq", "openrouter")


def test_paths_are_absolute(monkeypatch):
    s = _fresh(monkeypatch, DB_PATH="data/hospital.db")
    assert s.db_path.is_absolute()
