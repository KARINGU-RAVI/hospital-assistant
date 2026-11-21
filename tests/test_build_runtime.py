"""Phase 6 tests: build_runtime assembles the CodeZip folder and agentcore.json correctly. Offline:
temp project folder, fake ARNs (no AWS calls)."""
import json
import shutil
from datetime import date

import pytest

from agent import config
from agent.config import PROJECT_ROOT
from data.seed import seed
from scripts import build_runtime

GUARDRAIL_ARN = "arn:aws:bedrock:ap-south-1:111122223333:guardrail/gr123"
MEMORY_ARN = "arn:aws:bedrock-agentcore:ap-south-1:111122223333:memory/mem-123"


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A temp project root with data/ (db, faq, chroma, models) and the real agentcore template."""
    data = tmp_path / "data"
    data.mkdir()
    seed(data / "hospital.db", today=date(2030, 1, 7))
    shutil.copytree(PROJECT_ROOT / "data" / "faq", data / "faq")
    (data / "chroma").mkdir()
    (data / "chroma" / "chroma.sqlite3").write_bytes(b"x")
    (data / "models").mkdir()
    (data / "models" / "model.onnx").write_bytes(b"x")
    (tmp_path / "agentcore").mkdir()
    monkeypatch.setattr(build_runtime, "ROOT", tmp_path)
    monkeypatch.setattr(build_runtime, "BUILD", tmp_path / "build" / "runtime")
    monkeypatch.setattr(build_runtime, "CONFIG", tmp_path / "agentcore" / "agentcore.json")
    for k, v in {"CHROMA_PATH": str(data / "chroma"), "MODELS_PATH": str(data / "models"), "RAG_MODE": "vector",
                 "GEMINI_API_KEY": "gem-secret", "GROQ_API_KEY": "groq-secret", "OPENROUTER_API_KEY": "",
                 "GUARDRAILS_ENABLED": "true", "GUARDRAIL_ID": "gr123", "MEMORY_ENABLED": "true",
                 "MEMORY_ID": "mem-123"}.items():
        monkeypatch.setenv(k, v)
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()


def test_build_layout_and_entry(project):
    summary = build_runtime.build(config.get_settings(), GUARDRAIL_ARN, MEMORY_ARN)
    out = project / "build" / "runtime"
    for rel in ("main.py", "pyproject.toml", "iam-policy.json", "agent/agent.py", "agent/tools/rag.py",
                "data/hospital.db", "data/faq/visiting-hours.md", "data/chroma/chroma.sqlite3", "data/models/model.onnx"):
        assert (out / rel).exists(), rel
    assert not list(out.rglob("__pycache__"))
    entry = (out / "main.py").read_text(encoding="utf-8")
    assert "/tmp/hospital-data" in entry and "from agent.main import app" in entry and "app.run()" in entry
    assert summary["code_and_data_mb"] < 750


def test_pyproject_pins_tested_versions(project):
    build_runtime.build(config.get_settings(), None, None)
    text = (project / "build" / "runtime" / "pyproject.toml").read_text(encoding="utf-8")
    import importlib.metadata as m
    assert f'"strands-agents[gemini,openai]=={m.version("strands-agents")}"' in text
    assert f'"bedrock-agentcore=={m.version("bedrock-agentcore")}"' in text
    assert "chromadb==" in text and "fastembed==" in text
    assert "streamlit" not in text and "pytest" not in text


def test_least_privilege_policy(project):
    build_runtime.build(config.get_settings(), GUARDRAIL_ARN, MEMORY_ARN)
    policy = json.loads((project / "build" / "runtime" / "iam-policy.json").read_text(encoding="utf-8"))
    assert policy["Statement"] == [
        {"Sid": "ApplyClinicGuardrail", "Effect": "Allow", "Action": ["bedrock:ApplyGuardrail"], "Resource": [GUARDRAIL_ARN]},
        {"Sid": "UseClinicMemory", "Effect": "Allow",
         "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:RetrieveMemoryRecords"], "Resource": [MEMORY_ARN]},
    ]
    assert "*" not in json.dumps(policy)


def test_agentcore_json_from_template_with_env_vars(project):
    build_runtime.build(config.get_settings(), GUARDRAIL_ARN, MEMORY_ARN)
    cfg = json.loads((project / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
    runtime = cfg["runtimes"][0]
    assert runtime["codeLocation"] == "build/runtime" and runtime["build"] == "CodeZip"
    assert runtime["runtimeVersion"] == "PYTHON_3_12" and runtime["entrypoint"] == "main.py"
    assert runtime["additionalPolicies"] == ["iam-policy.json"]
    envs = {e["name"]: e["value"] for e in runtime["envVars"]}
    assert envs["GEMINI_API_KEY"] == "gem-secret" and envs["MEMORY_ID"] == "mem-123"
    assert "OPENROUTER_API_KEY" not in envs  # empty values are left out
    assert not any(name.endswith("_PATH") for name in envs)  # paths are set by the entry, not .env
    template = json.loads(build_runtime.TEMPLATE.read_text(encoding="utf-8"))
    assert "envVars" not in json.dumps(template) and "secret" not in json.dumps(template)  # committed file stays clean


def test_unusable_model_keys_are_not_shipped(project, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-secret")
    monkeypatch.setenv("OPENROUTER_MODEL_ID", "")  # key set but no model: can never be used
    config.get_settings.cache_clear()
    build_runtime.build(config.get_settings(), None, None)
    cfg = json.loads((project / "agentcore" / "agentcore.json").read_text(encoding="utf-8"))
    assert "OPENROUTER_API_KEY" not in {e["name"] for e in cfg["runtimes"][0]["envVars"]}


def test_keyword_mode_leaves_out_vector_search(project, monkeypatch):
    monkeypatch.setenv("RAG_MODE", "keyword")
    config.get_settings.cache_clear()
    build_runtime.build(config.get_settings(), None, None)
    out = project / "build" / "runtime"
    assert not (out / "data" / "chroma").exists() and not (out / "data" / "models").exists()
    text = (out / "pyproject.toml").read_text(encoding="utf-8")
    assert "chromadb" not in text and "fastembed" not in text
    assert not (out / "iam-policy.json").exists()


def test_missing_data_stops_with_hint(project):
    (project / "data" / "hospital.db").unlink()
    with pytest.raises(SystemExit, match="seed.py"):
        build_runtime.build(config.get_settings(), None, None)


def test_generated_files_are_git_ignored():
    import subprocess
    for path in ("build/runtime/main.py", "agentcore/agentcore.json", "agentcore/aws-targets.json"):
        result = subprocess.run(["git", "check-ignore", "-q", path], cwd=PROJECT_ROOT)
        assert result.returncode == 0, f"{path} is not git-ignored"
