# Phase 3 — RAG over hospital FAQs (about 1.5 h, ₹0)

## Official docs to check
Check these first (see CLAUDE.md "Verify before you code"). If a link is dead or moved,
find the current official page and update it here.
- ChromaDB: https://docs.trychroma.com
- FastEmbed: https://qdrant.github.io/fastembed/

## Goal
A `search_hospital_info` tool that answers clinic questions from small markdown files.

## FAQ files (`data/faq/`)
Six short made-up files (150–300 words each), clearly fake "Sunrise Clinic" content:
`visiting-hours.md`, `departments.md`, `insurance.md`, `parking-and-location.md`,
`documents-to-bring.md`, `cancellation-policy.md`.

## Index (`data/build_index.py`)
- split each file into chunks by `##` heading (fallback: ~500 characters)
- embed with FastEmbed (`BAAI/bge-small-en-v1.5`); set FastEmbed's cache folder inside the project
  (`data/models/`, git-ignored) so the model can be copied into the container later
- store in a persistent ChromaDB collection at `data/chroma/`, with `source` file name in metadata
- re-running rebuilds the collection from scratch

## Tool (`agent/tools/rag.py`)
`search_hospital_info(question: str) -> {"answers": [{"text", "source"}], "method": "vector"|"keyword"}`
- top 3 chunks from Chroma
- **fallback:** if the index folder is missing or FastEmbed/Chroma fails to load, do a simple keyword
  overlap search over the same markdown files and log `fallback: rag vector -> keyword`
- load the embedding model and collection once (module-level cache), not on every call

Register it as the 5th tool in `agent/agent.py`, and add to the system prompt: answer clinic questions
only from this tool's results and mention the source file name.

## Tests (`tests/test_rag.py`)
- keyword fallback finds `visiting-hours.md` for "when can I visit" (works with no index)
- if the index exists, vector search returns visiting hours for "what time can family come"
  (skip the test if the index folder is missing)

## Done when
- [ ] `python data/build_index.py` prints the number of chunks indexed
- [ ] In `python -m agent.cli`: "what are the visiting hours?" answers from the FAQ and names the source
- [ ] Renaming `data/chroma/` temporarily still gives an answer (keyword fallback, one log line)
- [ ] `pytest -q` passes

**End of Day 1. Commit and push.**
