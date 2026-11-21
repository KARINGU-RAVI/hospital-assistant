"""All settings in one place, read from environment variables / .env.

Run `python -m agent.config` to print the current settings (keys are never shown).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

SECRET_FIELDS = {"gemini_api_key", "groq_api_key", "openrouter_api_key"}


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _str(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int) -> int:
    try:
        return int(_str(name, str(default)))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_str(name, str(default)))
    except ValueError:
        return default


def _path(name: str, default: str) -> Path:
    p = Path(_str(name, default))
    return p if p.is_absolute() else PROJECT_ROOT / p


@dataclass(frozen=True)
class Settings:
    # model keys
    gemini_api_key: str
    groq_api_key: str
    openrouter_api_key: str
    # model chain
    model_chain: tuple[str, ...]
    gemini_model_id: str
    groq_model_id: str
    openrouter_model_id: str
    bedrock_model_id: str
    # history sent to the model (free-tier token limits)
    history_max_messages: int
    history_max_tokens: int
    # aws
    aws_region: str
    # guardrails
    guardrails_enabled: bool
    guardrail_id: str
    guardrail_version: str
    # memory
    memory_enabled: bool
    memory_id: str
    # rag
    rag_mode: str
    rag_max_distance: float
    # app
    app_mode: str
    local_agent_url: str
    agent_runtime_arn: str
    # paths
    db_path: Path
    chroma_path: Path
    faq_path: Path
    models_path: Path


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    chain = tuple(m.strip().lower() for m in _str("MODEL_CHAIN", "gemini,groq,openrouter").split(",") if m.strip())
    return Settings(
        gemini_api_key=_str("GEMINI_API_KEY"),
        groq_api_key=_str("GROQ_API_KEY"),
        openrouter_api_key=_str("OPENROUTER_API_KEY"),
        model_chain=chain,
        gemini_model_id=_str("GEMINI_MODEL_ID", "gemini-3.5-flash-lite"),
        groq_model_id=_str("GROQ_MODEL_ID", "openai/gpt-oss-120b"),
        openrouter_model_id=_str("OPENROUTER_MODEL_ID"),
        bedrock_model_id=_str("BEDROCK_MODEL_ID", "apac.amazon.nova-lite-v1:0"),
        history_max_messages=_int("HISTORY_MAX_MESSAGES", 6),
        history_max_tokens=_int("HISTORY_MAX_TOKENS", 3000),
        aws_region=_str("AWS_REGION", "ap-south-1"),
        guardrails_enabled=_bool("GUARDRAILS_ENABLED"),
        guardrail_id=_str("GUARDRAIL_ID"),
        guardrail_version=_str("GUARDRAIL_VERSION", "DRAFT"),
        memory_enabled=_bool("MEMORY_ENABLED"),
        memory_id=_str("MEMORY_ID"),
        rag_mode=_str("RAG_MODE", "vector").lower(),
        rag_max_distance=_float("RAG_MAX_DISTANCE", 0.40),
        app_mode=_str("APP_MODE", "local").lower(),
        local_agent_url=_str("LOCAL_AGENT_URL", "http://localhost:8080/invocations"),
        agent_runtime_arn=_str("AGENT_RUNTIME_ARN"),
        db_path=_path("DB_PATH", "data/hospital.db"),
        chroma_path=_path("CHROMA_PATH", "data/chroma"),
        faq_path=_path("FAQ_PATH", "data/faq"),
        models_path=_path("MODELS_PATH", "data/models"),
    )


def masked(settings: Settings) -> dict[str, str]:
    """Settings as display strings. Secrets show only 'set' / 'missing', never the value."""
    out: dict[str, str] = {}
    for f in fields(settings):
        value = getattr(settings, f.name)
        if f.name in SECRET_FIELDS:
            out[f.name] = "set" if value else "missing"
        elif f.name == "agent_runtime_arn":
            out[f.name] = "set" if value else "missing"  # ARNs contain the account ID
        elif isinstance(value, tuple):
            out[f.name] = " -> ".join(value)
        else:
            out[f.name] = str(value)
    return out


if __name__ == "__main__":
    for key, val in masked(get_settings()).items():
        print(f"{key:20} {val}")
