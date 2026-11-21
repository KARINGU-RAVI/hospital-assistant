# Phase 7 — README and resume (about 1 h, ₹0)

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

Use these to state the current model IDs and providers accurately in the README tech stack table.

## README.md
1. One-line pitch + 3 bullet capabilities
2. Architecture diagram (copy the ASCII diagram from the design doc)
3. Tech stack table (honest: Gemini/Groq now, Bedrock-ready via `MODEL_CHAIN`)
4. Fallback table (from the design doc)
5. Setup on Windows (PowerShell commands from Phases 0–5)
6. Demo GIF or video link
7. Cost notes: what was billed, and the teardown script
8. "What I would add next": Bedrock models once quota is granted, AgentCore Gateway + Identity,
   Bedrock Knowledge Bases

Never include account IDs, ARNs, keys or screenshots that show them.

## Resume lines (only what was actually built and run)
- Built a hospital front-desk AI agent with **Strands Agents**, deployed on **Amazon Bedrock AgentCore
  Runtime**, with **AgentCore Memory** for returning-patient context and **Amazon Bedrock Guardrails**
  (ApplyGuardrail) blocking medical advice and masking phone numbers.
- Designed a multi-provider model layer (Gemini → Groq, Bedrock-ready) with automatic failover and
  idempotent booking tools; RAG over clinic FAQs with ChromaDB + FastEmbed; Streamlit UI; offline pytest suite.
- Add plain "Amazon Bedrock (models)" ONLY after a Bedrock model actually ran in this project.

## Done when
- [ ] README renders correctly on GitHub
- [ ] Repo public, `.env` not in history (`git log -p | findstr GEMINI` shows nothing)
- [ ] Resume updated
