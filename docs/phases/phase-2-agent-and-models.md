# Phase 2 — Agent and model fallback (about 2 h, ₹0)

## Official docs to check
Check these first (see CLAUDE.md "Verify before you code"). If a link is dead or moved,
find the current official page and update it here.
- Strands Agents: https://strandsagents.com
  - Google (Gemini) provider: https://strandsagents.com/docs/user-guide/sdk/model-providers/google/
  - OpenAI-compatible provider (Groq, OpenRouter): https://strandsagents.com/docs/user-guide/sdk/model-providers/openai/
  - Amazon Bedrock provider: https://strandsagents.com/docs/user-guide/sdk/model-providers/amazon-bedrock/
  - Custom tools / `@tool`: https://strandsagents.com/docs/user-guide/sdk/tools/custom-tools/
  - Agent loop: https://strandsagents.com/docs/user-guide/sdk/agents/agent-loop/
- Gemini API models: https://aistudio.google.com/docs/models (moved from ai.google.dev/gemini-api/docs/models)
- Groq models: https://console.groq.com/docs/models · tool use: https://console.groq.com/docs/tool-use/overview
- OpenRouter: https://openrouter.ai/docs · models list: https://openrouter.ai/models
  (tool-calling models: `https://openrouter.ai/api/v1/models?supported_parameters=tools`)
- boto3 reference: https://docs.aws.amazon.com/boto3/latest/index.html (moved from boto3.amazonaws.com/v1/documentation/api/latest/index.html)

boto3 is for the Bedrock fallback errors (`NoCredentialsError`, `ClientError` codes).

## Goal
A Strands agent that uses the Phase 1 tools, with Gemini as primary and Groq as automatic fallback.
Bedrock is wired in config but not used until quota exists.

## `agent/models.py`
1. `build_model(name: str)` returns a Strands model object:
   - `"gemini"` → `strands.models.gemini.GeminiModel(client_args={"api_key": ...}, model_id=GEMINI_MODEL_ID, params={"temperature": 0.2})`
   - `"groq"` → `strands.models.openai.OpenAIModel(client_args={"api_key": GROQ_API_KEY, "base_url": "https://api.groq.com/openai/v1"}, model_id=GROQ_MODEL_ID, params={"temperature": 0.2})`
   - `"bedrock"` → `strands.models.BedrockModel(model_id=BEDROCK_MODEL_ID, region_name=AWS_REGION)`
   Verify these constructors against the installed Strands version before using them.
2. `is_fallback_error(exc)` → True for rate limit / quota (429, "RESOURCE_EXHAUSTED", "ThrottlingException"),
   timeouts, connection errors and 5xx. False for programming errors (those must surface).
3. `run_with_fallback(prompt, messages, tools, system_prompt) -> (reply_text, new_messages, model_used)`
   - tries each name in `MODEL_CHAIN` in order
   - for each: builds a fresh `strands.Agent(model=..., tools=tools, system_prompt=..., messages=copy_of_messages)`
   - on a fallback error: `logging.warning("fallback: %s -> %s (%s)", ...)` and try the next
   - if all fail: return a polite "The assistant is busy, please try again in a minute." (no stack trace to the user)
   - skips a model whose key is missing

## `agent/agent.py`
- `SYSTEM_PROMPT`: friendly clinic front desk for "Sunrise Clinic" (fake). Must:
  - use tools for every fact about doctors, slots and bookings (never invent)
  - route symptoms with `find_department`, never diagnose, never suggest medicines or doses
  - emergencies (chest pain, breathing trouble, heavy bleeding, unconscious) → tell them to call 108 / go to the ER, do not book
  - confirm doctor, date and time with the patient BEFORE calling `book_appointment`
  - keep replies short
- wraps the Phase 1 functions with `strands.tool`
- `chat(prompt, session_messages, patient_id, patient_name, patient_facts) -> dict`
  adds patient name/facts into the prompt context, calls `run_with_fallback`, returns
  `{"reply", "messages", "model_used"}`. `model_used` is for logs only, never the UI.

## Terminal tester (`python -m agent.cli`)
A tiny loop: type a message, see the reply. Keeps `messages` between turns. Patient fixed as `P001 / Ravi`.

## Tests (`tests/test_models.py`) — offline, no real API calls
- fake model objects / monkeypatch `build_model`: first raises a 429-style error, second succeeds →
  reply comes from the second, a warning is logged
- non-fallback error (e.g. `ValueError`) is raised, not swallowed
- missing key → that model is skipped
- all fail → the polite busy message

## Done when
- [ ] `python -m agent.cli`: "my knee hurts, can I see someone tomorrow?" → shows Orthopedics slots →
      "book the 10:00" → asks to confirm → books and shows an `HSP-` ID
- [ ] Setting `GEMINI_API_KEY` to a wrong value in `.env` temporarily makes the same chat work via Groq
      (one `fallback:` line in the console)
- [ ] `pytest -q` passes
