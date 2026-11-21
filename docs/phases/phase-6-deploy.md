# Phase 6 — Deploy to AgentCore Runtime, demo, teardown (about 2–3 h, ₹20–60)

## Official docs to check
Check these first (see CLAUDE.md "Verify before you code"). If a link is dead or moved,
find the current official page and update it here.
- AgentCore Runtime: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.md
  (developer guide: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/)
- Strands, deploying to AgentCore Runtime: https://strandsagents.com/docs/user-guide/sdk/deploy/deploy_to_bedrock_agentcore/
- boto3 reference: https://docs.aws.amazon.com/boto3/latest/index.html (moved from boto3.amazonaws.com/v1/documentation/api/latest/index.html)

## ⚠ ASK before every AWS command in this phase
Creates an IAM role, an ECR repository, a CodeBuild build and an AgentCore runtime — all billed.
Show the developer each command first and wait for "go".

## Where to deploy from
Prefer **AWS CloudShell** (no access keys on the laptop): the developer pushes the repo to GitHub,
clones it in CloudShell, and runs the deploy there. CodeBuild builds the ARM64 container, so Docker is
not needed.

## Prepare the container contents
- `data/hospital.db`, `data/chroma/` and the FastEmbed model folder (`data/models/`) must be inside the
  deployed bundle (build them in CloudShell with `seed.py` + `build_index.py` before deploying, or
  generate them on container start if missing — choose the simpler one and explain why).
- If the image becomes too large or ChromaDB fails on ARM64, set `RAG_MODE=keyword` for the cloud
  (the Phase 3 fallback) and tell the developer.
- Secrets: pass `GEMINI_API_KEY` and `GROQ_API_KEY` as runtime environment variables at deploy time,
  typed by the developer in CloudShell. Never commit them or bake them into the image.

## Deploy (starter toolkit — verify current command names in the AgentCore quickstart)
```
pip install bedrock-agentcore-starter-toolkit
agentcore configure --entrypoint agent/main.py --region ap-south-1     # public network mode
agentcore launch --env GEMINI_API_KEY=... --env GROQ_API_KEY=... --env GUARDRAILS_ENABLED=true ...
agentcore invoke '{"prompt": "what are the visiting hours?", "session_id": "...", "patient_id": "P001", "patient_name": "Ravi"}'
```
The runtime's IAM role also needs permission for `bedrock:ApplyGuardrail` on the guardrail and the
AgentCore Memory data-plane actions on the memory. Add only those (least privilege) and show the policy.

## Cloud mode + demo
- Developer sets `APP_MODE=cloud` and `AGENT_RUNTIME_ARN` in `.env` (if their laptop has AWS access),
  or records the demo with `agentcore invoke` in CloudShell + the local Streamlit run.
- Record a 2–3 minute video: the demo script from Phase 5, then the CloudWatch logs / traces of one
  request (shows Runtime + observability).

## Teardown (same day)
- `python scripts/aws_teardown.py` (+ `agentcore destroy` if the toolkit provides it)
- Developer checks in the console: no AgentCore runtime, memory or guardrail left; ECR repo empty or deleted.

## Done when
- [ ] `agentcore invoke` returns a correct answer from the cloud
- [ ] Demo video recorded
- [ ] Teardown done, console shows nothing left running
