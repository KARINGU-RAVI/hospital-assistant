"""Phase 3 tests: FAQ chunking, keyword fallback (always offline) and vector search (temp index built
from the cached model with local_files_only=True; skipped only if the model isn't downloaded)."""
import logging

import pytest

from agent import config
from agent.config import PROJECT_ROOT
from agent.tools import rag

FAQ = PROJECT_ROOT / "data" / "faq"
MODELS = PROJECT_ROOT / "data" / "models"
OFF_TOPIC = ["do you sell cars?", "what's the weather today?", "what is the cricket score?", "recipe for biryani"]


@pytest.fixture
def settings(monkeypatch, tmp_path):
    """Point config at a temp index folder (missing until a test builds it)."""
    def apply(**env):
        for k, v in {"FAQ_PATH": str(FAQ), "MODELS_PATH": str(MODELS), "CHROMA_PATH": str(tmp_path / "chroma"),
                     "RAG_MODE": "vector", "RAG_MAX_DISTANCE": "0.40", **env}.items():
            monkeypatch.setenv(k, v)
        config.get_settings.cache_clear()
        rag._load_vector.cache_clear()
        return config.get_settings()
    yield apply
    config.get_settings.cache_clear()
    rag._load_vector.cache_clear()


def test_six_faq_files_are_chunked_by_heading():
    chunks = rag.load_chunks(FAQ)
    assert {c["source"] for c in chunks} == {
        "visiting-hours.md", "departments.md", "insurance.md", "parking-and-location.md",
        "documents-to-bring.md", "cancellation-policy.md", "emergencies-and-contact.md"}
    assert {c["title"] for c in chunks} >= {"Visiting hours", "Departments", "Emergencies and contact"}
    assert all(c["text"].count("\n## ") == 0 and " - " in c["text"].split("\n")[0] for c in chunks)
    assert not any("fictional clinic" in c["text"] and len(c["text"]) < 120 for c in chunks)  # no intro chunk


# --- keyword fallback (no index needed) ---
def test_keyword_finds_visiting_hours():
    hits = rag.keyword_search("when can I visit", FAQ)
    assert hits[0]["source"] == "visiting-hours.md"


@pytest.mark.parametrize("question", OFF_TOPIC)
def test_keyword_off_topic_finds_nothing(question):
    assert rag.keyword_search(question, FAQ) == []


def test_missing_index_falls_back_to_keyword(settings, caplog):
    s = settings()
    with caplog.at_level(logging.WARNING, logger="agent.tools.rag"):
        result = rag.search_hospital_info("when can I visit")
    assert result["method"] == "keyword" and result["answers"][0]["source"] == "Visiting hours"
    assert [r.getMessage() for r in caplog.records] == ["fallback: rag vector -> keyword (FileNotFoundError)"]
    assert not s.chroma_path.exists()  # we never let Chroma create an empty DB


def test_keyword_mode_skips_vector_quietly(settings, caplog):
    settings(RAG_MODE="keyword")
    with caplog.at_level(logging.WARNING):
        result = rag.search_hospital_info("is there parking")
    assert result["method"] == "keyword" and not caplog.records


def test_no_match_shape(settings):
    settings(RAG_MODE="keyword")
    assert rag.search_hospital_info("do you sell cars?") == {
        "answers": [], "note": "no matching clinic information", "method": "keyword"}


def test_answers_are_compact(settings):
    settings(RAG_MODE="keyword")
    result = rag.search_hospital_info("visiting hours for children and visitors")
    assert 0 < len(result["answers"]) <= 3
    assert all(set(a) == {"text", "source"} and len(a["text"]) <= rag.MAX_CHARS for a in result["answers"])


# --- vector search on a temp index ---
def _model_cached() -> bool:
    try:
        rag.get_embedder(MODELS, local_only=True)
        return True
    except Exception:
        return False


@pytest.fixture
def vector_index(settings):
    if not _model_cached():
        pytest.skip("FastEmbed model not downloaded yet: run python data\\build_index.py once")
    s = settings()
    assert rag.build_index(s.faq_path, s.chroma_path, s.models_path, local_only=True) > 0
    return s


@pytest.mark.parametrize("question, source", [
    ("what time can family come", "Visiting hours"),
    ("which insurance do you accept", "Insurance and payment"),
    ("is there parking at the clinic", "Parking and location"),
    ("do you have an emergency department?", "Emergencies and contact"),
    ("how can I contact the clinic?", "Emergencies and contact"),
])
def test_vector_finds_right_file(vector_index, question, source):
    result = rag.search_hospital_info(question)
    assert result["method"] == "vector" and result["answers"][0]["source"] == source


@pytest.mark.parametrize("question", OFF_TOPIC)
def test_vector_off_topic_is_cut_off(vector_index, question):
    assert rag.search_hospital_info(question)["answers"] == []


def test_rebuild_replaces_collection(vector_index):
    s = vector_index
    first = rag.build_index(s.faq_path, s.chroma_path, s.models_path, local_only=True)
    assert rag.build_index(s.faq_path, s.chroma_path, s.models_path, local_only=True) == first
    _, collection = rag._load_vector(str(s.chroma_path), str(s.models_path))
    assert collection.count() == first


def test_sources_are_friendly_names_not_files(settings):
    settings(RAG_MODE="keyword")
    for question in ("visiting hours", "parking", "insurance", "cancel"):
        for answer in rag.search_hospital_info(question)["answers"]:
            assert not answer["source"].endswith(".md")


def test_faq_has_no_phone_numbers_or_doctor_names():
    from agent.guardrails import mask_phones
    for path in FAQ.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        assert mask_phones(text) == text, path.name
        assert "Dr." not in text, path.name
