# CLAUDE.md — Hospital Front-Desk Agent

Read this file first, every session. Then read only the phase file you were asked to build
(`docs/phases/phase-N-*.md`). Build ONE phase at a time and stop when its "Done when" checks pass.

## What this project is
A small learning project: an AI front-desk agent for a fake clinic. It routes symptoms to a
department, checks doctor availability, books / views / cancels appointments, answers FAQs with RAG,
blocks medical advice with Amazon Bedrock Guardrails, and remembers returning patients with
Amazon Bedrock AgentCore Memory. It is hosted on Amazon Bedrock AgentCore Runtime.
Keep it SMALL. Fake data only. No login, payments, SMS or email.

## Stack (do not add to it without asking)
- Python 3.12, Windows + PowerShell on the developer's laptop
- Strands Agents (`strands-agents`) for the agent
- Models: Gemini (primary) -> Groq -> OpenRouter (automatic fallbacks; OpenRouter via Strands' OpenAIModel)
  -> Bedrock Nova/Claude (later, via config)
- SQLite for doctors, slots, bookings, and local fallbacks
- ChromaDB + FastEmbed for RAG, keyword search as fallback
- boto3 for Bedrock Guardrails and AgentCore Memory
- `bedrock-agentcore` SDK (`BedrockAgentCoreApp`) for the entrypoint
- Streamlit for the UI. NO FastAPI, NO React.
- pytest for tests

## Hard rules
1. **Secrets:** API keys live ONLY in `.env` (git-ignored). Never print, log, echo or commit a key.
   `.env.example` lists names only. Never ask the developer to paste a key, access key or raw
   terminal output into chat; they describe errors in words.
2. **AWS costs money on this account.** Before running ANY command that creates, updates or deletes an
   AWS resource (guardrail, memory, runtime, ECR, IAM role, CodeBuild), stop and ask the developer.
   Read-only AWS calls in tests are also off by default — tests must pass with no AWS access.
3. **Region:** `ap-south-1` unless config says otherwise. AgentCore Runtime uses PUBLIC network mode.
   Never create a VPC, NAT gateway or load balancer.
4. **One config file:** every setting (model IDs, chain order, region, guardrail ID, memory ID,
   feature switches) is read in `agent/config.py` from environment variables. No hard-coded IDs elsewhere.
5. **Fallbacks are silent:** when a fallback triggers, log one line with `logging.warning`
   (e.g. `fallback: gemini -> groq (429)`). Never show fallbacks in the UI.
6. **Safety:** the agent never diagnoses or suggests medicines/doses. Emergencies -> "call 108 or go to
   the nearest emergency room". This is in the system prompt AND enforced by Guardrails / local fallback.
7. **Booking is idempotent:** same patient + doctor + slot returns the existing booking ID.
   A slot can be booked only once (UNIQUE constraint). This protects against model-fallback retries.
8. **Keep it simple:** no new frameworks, no class hierarchies where a function will do, no premature
   abstraction. Prefer small functions with type hints and short docstrings.
9. **Verify APIs, don't guess:** for Strands, bedrock-agentcore, Guardrails and AgentCore Memory,
   check the installed package / current AWS docs before writing calls. If an API differs from the
   phase file, follow the real API and tell the developer what changed.
10. **Windows commands:** when you show commands, write them for PowerShell
    (e.g. `.venv\Scripts\activate`, `$env:VAR="x"`).

## Verify before you code
Official docs first, no guessing.
- Before using any library API, model ID, CLI command or AWS parameter, check in this order:
  1. the official documentation (links below, and each phase file's "Official docs to check" section);
  2. the installed package's source or `help()` in `.venv`;
  3. for model IDs, the provider's live model list (the list for the developer's key).
- Never write an API call, parameter name or model ID from memory alone.
- In every phase plan, list what you checked: the page or package and version, plus what it confirmed.
- If the docs and the installed version disagree, the installed version wins. Tell the developer.
- If you can't confirm something, say "unverified" and ask. Don't guess.

Official docs (if a link is dead or moved, find the current official page and update it here):
- Strands Agents: https://strandsagents.com (model providers: Google/Gemini, OpenAI-compatible,
  Amazon Bedrock; custom tools; agent loop; deploying to AgentCore)
- Gemini API models: https://aistudio.google.com/docs/models (was ai.google.dev/gemini-api/docs/models)
- Groq models: https://console.groq.com/docs/models · tool use: https://console.groq.com/docs/tool-use/overview
- OpenRouter: https://openrouter.ai/docs · models list: https://openrouter.ai/models
  (API: `https://openrouter.ai/api/v1/models?supported_parameters=tools` for tool-calling models)
- SQLite partial indexes: https://www.sqlite.org/partialindex.html
- ChromaDB: https://docs.trychroma.com
- FastEmbed: https://qdrant.github.io/fastembed/
- Bedrock Guardrails ApplyGuardrail:
  https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html
- AgentCore developer guide: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/
  (Runtime: .../devguide/agents-tools-runtime.md · Memory: .../devguide/memory.md)
- boto3 reference: https://docs.aws.amazon.com/boto3/latest/index.html
  (was boto3.amazonaws.com/v1/documentation/api/latest/index.html)
- Streamlit chat elements: https://docs.streamlit.io/develop/api-reference/chat

## Environment variables (names only — values live in .env)
The full, current list is `.env.example`; `agent/config.py` reads every one of them. Main ones:
```
GEMINI_API_KEY=
GROQ_API_KEY=
OPENROUTER_API_KEY=
MODEL_CHAIN=gemini,groq,openrouter # later: bedrock,gemini,groq,openrouter
GEMINI_MODEL_ID=gemini-3.5-flash-lite   # check AI Studio for the current free model
GROQ_MODEL_ID=openai/gpt-oss-120b      # check Groq console + your key's model list
OPENROUTER_MODEL_ID=               # must support tool calling; empty = skipped
BEDROCK_MODEL_ID=apac.amazon.nova-lite-v1:0
AWS_REGION=ap-south-1
GUARDRAILS_ENABLED=false
GUARDRAIL_ID=
GUARDRAIL_VERSION=DRAFT
MEMORY_ENABLED=false
MEMORY_ID=
APP_MODE=local                     # local | cloud
AGENT_RUNTIME_ARN=                 # only for cloud mode
```

## Folder layout
```
agent/  main.py agent.py config.py models.py guardrails.py memory.py tools/{departments,booking,rag}.py
data/   seed.py build_index.py faq/*.md   (hospital.db and chroma/ are generated, git-ignored)
scripts/ aws_setup.py aws_teardown.py
ui/app.py
tests/
docs/phases/
```

## Definition of done for every phase
- `pytest -q` passes with no network access to AWS.
- The phase's "Done when" checks pass.
- You tell the developer exactly which commands to run to see it working.
- You list any file you changed outside the phase's scope, and why.
