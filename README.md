# Hospital Front-Desk Agent

An AI front-desk assistant for a fake clinic: routes symptoms to a department, checks doctor
availability, books / views / cancels appointments, answers clinic FAQs with RAG, blocks medical
advice with **Amazon Bedrock Guardrails**, remembers returning patients with **AgentCore Memory**,
and runs on **Amazon Bedrock AgentCore Runtime**. Built with **Strands Agents** and **Streamlit**.

> Learning project. All data is fake. Not medical advice.

Repository: https://github.com/KARINGU-RAVI/hospital-assistant

## Status
- [x] Phase 0 — setup
- [x] Phase 1 — data + tools
- [ ] Phase 2 — agent + model fallback
- [ ] Phase 3 — RAG
- [ ] Phase 4 — Guardrails + Memory
- [ ] Phase 5 — AgentCore entrypoint + Streamlit UI
- [ ] Phase 6 — deploy to AgentCore Runtime
- [ ] Phase 7 — polish

Build guides: `docs/phases/`. Rules for Claude Code: `CLAUDE.md`.

## Cost and free-tier limits
Models run on free tiers (checked in the providers' consoles, November 2026; they change, so recheck):

| Model | Requests/min | Tokens/min | Per day |
|---|---|---|---|
| Gemini `gemini-3.5-flash-lite` (primary) | 15 | 250K | 500 requests |
| Groq `openai/gpt-oss-120b` (fallback) | 30 | **8K** (the tight one) | 1K requests, 200K tokens |

One chat turn makes several model calls (one per tool step), so each request is kept small:
- the model sees only the last `HISTORY_MAX_MESSAGES` (6) messages or about `HISTORY_MAX_TOKENS`
  (3K) tokens, whichever is smaller, cut at a patient message so a tool call always keeps its
  result. The full conversation is still kept for the UI;
- tool results are compact (short keys, at most 8 slots);
- the system prompt is under 300 words (about 300 tokens; tool descriptions add about 600).

AWS (Phases 4–6) costs a few rupees to about ₹60 in total; resources are torn down the same day.

## Setup (Windows PowerShell, Python 3.12)
```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env      # then type your own keys into .env
python -m agent.config      # keys show only as set / missing
pytest -q
```
