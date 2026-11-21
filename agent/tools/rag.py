"""search_hospital_info(question): ChromaDB + FastEmbed, keyword fallback.

APIs checked: chromadb 1.5.9 (PersistentClient, create_collection with configuration={"hnsw": {"space":
"cosine"}}, add/query with our own embeddings) and fastembed 0.9.1 (TextEmbedding(model_name, cache_dir,
local_files_only), passage_embed / query_embed: no prefix added for this model).
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

from agent.config import get_settings

log = logging.getLogger(__name__)

MODEL_NAME = "BAAI/bge-small-en-v1.5"
COLLECTION = "faq"
TOP_K = 3
MAX_CHARS = 600
CHUNK_CHARS = 500  # only for files without "##" headings
# Optional BGE v1.5 query instruction, from the BAAI/bge-small-en-v1.5 model card. Passages never get it.
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
USE_QUERY_INSTRUCTION = False  # measured: gap 0.201 without vs 0.197 with, so off (simpler)
NO_MATCH = {"answers": [], "note": "no matching clinic information"}


# --- chunks -------------------------------------------------------------------------------------

def load_chunks(faq_path: Path) -> list[dict]:
    """Split each FAQ file into chunks by "##" heading: {"id", "text", "source" (file), "title" (page title)}.

    Each chunk starts with the file title and heading so it makes sense on its own.
    Text before the first "##" (title + disclaimer) is not a chunk. Files without headings are
    split into ~500-character pieces.
    """
    chunks: list[dict] = []
    for path in sorted(Path(faq_path).glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title_match = re.search(r"^# (.+)$", text, flags=re.M)
        title = title_match.group(1).strip() if title_match else path.stem
        sections = re.split(r"^## ", text, flags=re.M)[1:]
        if sections:
            pieces = [f"{title} - {s.strip()}" for s in sections if s.strip()]
        else:
            pieces = [text[i : i + CHUNK_CHARS].strip() for i in range(0, len(text), CHUNK_CHARS)]
        for n, piece in enumerate(pieces):
            chunks.append({"id": f"{path.name}#{n}", "text": piece, "source": path.name, "title": title})
    return chunks


# --- vector search ------------------------------------------------------------------------------

def get_embedder(models_path: Path, local_only: bool):
    """FastEmbed model with its files in models_path. local_only=True never downloads."""
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=MODEL_NAME, cache_dir=str(models_path), local_files_only=local_only)


def embed_query(embedder, question: str, use_instruction: bool = USE_QUERY_INSTRUCTION) -> list[float]:
    text = QUERY_INSTRUCTION + question if use_instruction else question
    return next(iter(embedder.query_embed(text))).tolist()


def build_index(faq_path: Path, chroma_path: Path, models_path: Path, local_only: bool = False) -> int:
    """Rebuild the FAQ collection from scratch. Returns the number of chunks indexed."""
    import chromadb

    chunks = load_chunks(faq_path)
    embedder = get_embedder(models_path, local_only)
    vectors = [v.tolist() for v in embedder.passage_embed([c["text"] for c in chunks])]
    client = chromadb.PersistentClient(path=str(chroma_path))
    if COLLECTION in [c.name for c in client.list_collections()]:
        client.delete_collection(COLLECTION)
    collection = client.create_collection(
        COLLECTION, configuration={"hnsw": {"space": "cosine"}}, embedding_function=None
    )
    collection.add(
        ids=[c["id"] for c in chunks],
        embeddings=vectors,
        documents=[c["text"] for c in chunks],
        metadatas=[{"source": c["source"], "title": c["title"]} for c in chunks],
    )
    _load_vector.cache_clear()
    return len(chunks)


@lru_cache(maxsize=2)
def _load_vector(chroma_path: str, models_path: str):
    """Load the embedding model and collection once. Raises if the index or model is missing."""
    if not Path(chroma_path).is_dir():  # PersistentClient would silently create an empty DB
        raise FileNotFoundError(f"no index folder at {chroma_path}")
    import chromadb

    collection = chromadb.PersistentClient(path=chroma_path).get_collection(COLLECTION, embedding_function=None)
    return get_embedder(Path(models_path), local_only=True), collection


def vector_search(question: str, chroma_path: Path, models_path: Path, k: int = TOP_K) -> list[dict]:
    """Top-k chunks with their cosine distance: [{"text", "source", "title", "distance"}]."""
    embedder, collection = _load_vector(str(chroma_path), str(models_path))
    result = collection.query(
        query_embeddings=[embed_query(embedder, question)], n_results=k, include=["documents", "metadatas", "distances"]
    )
    return [
        {"text": doc, "source": meta["source"], "title": meta.get("title", meta["source"]), "distance": dist}
        for doc, meta, dist in zip(result["documents"][0], result["metadatas"][0], result["distances"][0])
    ]


# --- keyword fallback ---------------------------------------------------------------------------

STOPWORDS = set(
    "a an the and or of to in on at for from by with is are was be can could do does did i you we my your "
    "our me us it this that what when where which who how why there here any some have has please tell about "
    "s whats".split()
)


def _stems(text: str) -> list[str]:
    """Lower-case words without stopwords, with a simple ending cut (visiting -> visit, hours -> hour)."""
    out = []
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if w in STOPWORDS or len(w) < 3:
            continue
        for suffix in ("ing", "ed", "s"):
            if w.endswith(suffix) and len(w) - len(suffix) >= 4:
                w = w[: -len(suffix)]
                break
        out.append(w)
    return out


def _same(a: str, b: str) -> bool:
    """Same word, or one is a prefix of the other (visit / visitor, cancel / cancellation)."""
    return a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))


def keyword_search(question: str, faq_path: Path, k: int = TOP_K) -> list[dict]:
    """Chunks sharing words with the question, best first.

    Score per question word: 2 if it is in the chunk's heading line, plus how often it appears.
    """
    query = set(_stems(question))
    scored = []
    for chunk in load_chunks(faq_path):
        heading, words = _stems(chunk["text"].split("\n", 1)[0]), _stems(chunk["text"])
        score = sum(2 * any(_same(q, h) for h in heading) + sum(_same(q, w) for w in words) for q in query)
        if score:
            scored.append((score, chunk))
    scored.sort(key=lambda pair: -pair[0])
    return [{"text": c["text"], "source": c["source"], "title": c["title"]} for _, c in scored[:k]]


# --- tool ---------------------------------------------------------------------------------------

def _compact(hits: list[dict]) -> list[dict]:
    """What the model sees: the text and a friendly page name (never a file name)."""
    return [{"text": h["text"][:MAX_CHARS], "source": h["title"]} for h in hits]


def search_hospital_info(question: str) -> dict:
    """Search Sunrise Clinic's information pages (visiting hours, departments, insurance and fees,
    parking and location, documents to bring, cancellation policy, emergencies and contact, opening hours).

    Args:
        question: The patient's question, e.g. "what are the visiting hours?".

    Returns:
        {"answers": [{"text", "source" (page name)}], "method"}; answers is empty (with a "note") if
        nothing matches.
    """
    s = get_settings()
    if s.rag_mode != "keyword":
        try:
            hits = [h for h in vector_search(question, s.chroma_path, s.models_path) if h["distance"] <= s.rag_max_distance]
            return {"answers": _compact(hits), "method": "vector"} if hits else {**NO_MATCH, "method": "vector"}
        except Exception as e:  # missing index/model, Chroma or FastEmbed load errors
            log.warning("fallback: rag vector -> keyword (%s)", type(e).__name__)
    hits = keyword_search(question, s.faq_path)
    return {"answers": _compact(hits), "method": "keyword"} if hits else {**NO_MATCH, "method": "keyword"}
