# Phase 4 — Bedrock Guardrails and AgentCore Memory (about 2–3 h, a few rupees)

## Official docs to check
Check these first (see CLAUDE.md "Verify before you code"). If a link is dead or moved,
find the current official page and update it here.
- Bedrock Guardrails ApplyGuardrail:
  https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use-independent-api.html
- AgentCore Memory: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.md
  (developer guide: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/)
- boto3 reference: https://docs.aws.amazon.com/boto3/latest/index.html (moved from boto3.amazonaws.com/v1/documentation/api/latest/index.html)

## ⚠ Before starting — ASK the developer
This phase creates paid AWS resources. Do not run `scripts/aws_setup.py` until the developer confirms:
1. Budget alerts are set on the AWS account.
2. Their laptop has AWS access configured by THEM (short-lived credentials such as `aws login`, or
   `aws configure` with a small IAM policy). You never see or handle these credentials.
3. `aws sts get-caller-identity` works for them (they report "works" in words — no output pasted).

Write all code with `GUARDRAILS_ENABLED=false` and `MEMORY_ENABLED=false` first, so everything works
locally on the fallbacks. Then switch on.

## `scripts/aws_setup.py` (developer runs it once, after confirming)
Creates, in `AWS_REGION`:
- **Guardrail** `hospital-frontdesk-guardrail`:
  - denied topics: `MedicalDiagnosis` (diagnosing illnesses), `MedicationAdvice` (medicines, doses, prescriptions)
  - sensitive info: `PHONE` → `ANONYMIZE` (masks phone numbers in output)
  - blocked messages: "I can't give medical advice. Please ask the doctor at your visit. In an emergency call 108."
- **AgentCore Memory** `hospital_frontdesk_memory`, event expiry 7 days, with ONE built-in strategy:
  user preference, namespace `/patients/{actorId}/preferences` (extraction runs on AWS's side, not on
  your Bedrock model quota). Use the `bedrock-agentcore` SDK's memory client if available
  (e.g. create-and-wait helpers), else boto3 `bedrock-agentcore-control`.
- waits until both are ready, then PRINTS the IDs and tells the developer to put them in `.env`
  (`GUARDRAIL_ID`, `MEMORY_ID`). It does not edit `.env` itself.

`scripts/aws_teardown.py`: deletes the guardrail, the memory and (from Phase 6) the runtime and its ECR
repo. Asks "type DELETE to continue" first. Safe to run twice.

## `agent/guardrails.py`
- `check_input(text) -> {"blocked": bool, "text": str}` and `check_output(text) -> same`
- Enabled: `bedrock-runtime.apply_guardrail(guardrailIdentifier, guardrailVersion, source="INPUT"|"OUTPUT", content=[{"text": {"text": ...}}])`.
  `action == "GUARDRAIL_INTERVENED"` → blocked on input; on output use the returned (masked) text.
- **Fallback** (switch off, or any AWS error → log `fallback: guardrails -> local`):
  - input: keyword/regex list for dose / medicine / "what disease do I have" / "diagnose"
  - output: regex that masks Indian / international phone numbers as `[PHONE]`

## `agent/memory.py`
- `load_facts(patient_id) -> dict` and `save_turn(patient_id, session_id, user_text, reply_text, facts)`
- Enabled: write each turn as a conversational event (actor = patient_id, session = session_id);
  read long-term preference records for the patient's namespace.
- Also always keep simple facts (`name`, `preferred_doctor` = last doctor booked) in the local
  `patient_facts` table — this is both the fallback and a fast path, because long-term extraction on AWS
  is asynchronous and can take a minute.
- Any AWS error → log `fallback: memory -> local` and use SQLite only.

## Wire into `agent.chat()` — the order from the design doc
1. `check_input` → if blocked, return the safe message (no model call)
2. `load_facts`
3. `run_with_fallback`
4. `check_output`
5. `save_turn` (+ update `preferred_doctor` when a booking succeeded)

## Tests (offline)
- guardrails fallback blocks "what dose of paracetamol should I take" and passes "book me a doctor"
- phone masking: "call 98765 43210" → `[PHONE]`
- memory fallback saves and loads `preferred_doctor`
- with switches on but boto3 calls monkeypatched to raise → fallbacks used, no crash

## Done when
- [ ] With both switches OFF: dose question blocked locally; new session "book with my usual doctor" → Dr. Rao (local facts)
- [ ] Developer runs `aws_setup.py`, adds IDs, turns both switches ON
- [ ] Dose question blocked by Bedrock Guardrails (log shows no fallback line)
- [ ] Memory events appear for the patient (developer checks the AgentCore Memory console)
- [ ] `pytest -q` passes with no AWS access
