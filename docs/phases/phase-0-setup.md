# Phase 0 — Setup (about 30 min, ₹0)

> **Status: already built in the starter project.** The folder layout, requirements.txt, .gitignore,
> .env.example, agent/config.py, README.md and tests/test_config.py exist. Every other module is a
> placeholder that names its phase. The developer only needs to run the commands below and check
> "Done when". Claude Code: do not rebuild this phase; start from Phase 1.

## Goal
An empty but runnable project: virtual environment, dependencies, `.env` handling and one config module.

## Steps
1. Create the folder layout from CLAUDE.md with empty `__init__.py` files in `agent/` and `agent/tools/`.
2. `requirements.txt`:
   ```
   strands-agents[gemini,openai]
   strands-agents-tools
   bedrock-agentcore
   boto3
   chromadb
   fastembed
   streamlit
   python-dotenv
   requests
   pytest
   ```
   (`openai` extra = Strands' OpenAI-compatible provider, used for Groq.)
3. `.gitignore`: `.env`, `.venv/`, `__pycache__/`, `data/hospital.db`, `data/chroma/`, `.bedrock_agentcore*`, `*.log`.
4. `.env.example` with every variable name from CLAUDE.md and NO values.
5. `agent/config.py`:
   - loads `.env` with python-dotenv
   - exposes a frozen dataclass `Settings` and a `get_settings()` function
   - `MODEL_CHAIN` parsed into a list
   - booleans parsed from `"true"/"false"`
   - a `masked()` helper that shows only whether a key is set (`set` / `missing`), never its value
   - `python -m agent.config` prints the settings using `masked()` for keys
6. `README.md` stub with the project one-liner and PowerShell setup commands.

## Developer runs (PowerShell)
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then the developer types their own keys into .env
python -m agent.config
```

## Done when
- [ ] `pip install -r requirements.txt` finishes without errors on Windows / Python 3.12
- [ ] `python -m agent.config` prints settings, keys shown only as `set` / `missing`
- [ ] `git status` does not show `.env`
