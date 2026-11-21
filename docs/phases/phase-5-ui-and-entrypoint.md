# Phase 5 — AgentCore entrypoint and Streamlit UI (about 2 h, a few rupees)

## Official docs to check
Check these first (see CLAUDE.md "Verify before you code"). If a link is dead or moved,
find the current official page and update it here.
- AgentCore Runtime: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.md
  (developer guide: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/)
- Strands, deploying to AgentCore Runtime: https://strandsagents.com/docs/user-guide/sdk/deploy/deploy_to_bedrock_agentcore/
- boto3 reference: https://docs.aws.amazon.com/boto3/latest/index.html (moved from boto3.amazonaws.com/v1/documentation/api/latest/index.html)
- Streamlit chat elements: https://docs.streamlit.io/develop/api-reference/chat

## Goal
The same `agent/main.py` runs on the laptop (AgentCore SDK local server) and later on AgentCore Runtime.
A Streamlit chat page talks to it.

## `agent/main.py`
```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
app = BedrockAgentCoreApp()

@app.entrypoint
def invoke(payload, context):
    # payload: {"prompt", "session_id", "patient_id", "patient_name", "messages"?}
    ...
    return {"reply": ..., "messages": ...}

if __name__ == "__main__":
    app.run()   # local server on http://localhost:8080 (POST /invocations)
```
Verify the import path and decorator against the installed `bedrock-agentcore` version.
Keep conversation state simple: Streamlit sends the previous `messages` with each request
(the runtime is stateless for us; AgentCore Memory holds the long-term facts).
Never return `model_used` or fallback details in the response.

## `ui/app.py` (Streamlit)
- title "Sunrise Clinic — Front Desk Assistant" and a one-line fake-data disclaimer
- sidebar: patient name + patient ID inputs (default `Ravi` / `P001`), "New session" button
- chat history with `st.chat_message`, input with `st.chat_input`
- `session_id` and `messages` in `st.session_state` (session_id: a uuid4 string, 36 chars)
- **APP_MODE=local** → `requests.post("http://localhost:8080/invocations", json=payload)`
- **APP_MODE=cloud** → boto3 `bedrock-agentcore` `invoke_agent_runtime(agentRuntimeArn=AGENT_RUNTIME_ARN, runtimeSessionId=session_id, payload=json bytes)`;
  read the response body (verify the exact response shape in current docs)
- friendly error message if the server isn't running; no stack traces in the UI
- 3 example-prompt buttons: book knee appointment, visiting hours, cancel a booking

## Developer runs (two PowerShell windows)
```
# window 1
.venv\Scripts\activate
python -m agent.main
# window 2
.venv\Scripts\activate
streamlit run ui/app.py
```

## Done when
- [ ] Full demo script works in the browser in local mode:
      book knee appointment → ask visiting hours → dose question blocked → New session → "book with my usual doctor"
- [ ] Stopping the local server shows a friendly message in the UI
- [ ] `pytest -q` passes
