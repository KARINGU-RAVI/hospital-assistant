"""Streamlit chat UI. APP_MODE=local -> local server, APP_MODE=cloud -> invoke_agent_runtime.

Run: streamlit run ui/app.py   (with python -m agent.main running in another window for local mode)

APIs checked: Streamlit 1.67.0 st.chat_message / st.chat_input (docs: api-reference/chat and the
"build conversational apps" tutorial); boto3 1.44.9 bedrock-agentcore invoke_agent_runtime(agentRuntimeArn,
runtimeSessionId (min 33 chars), payload=bytes) -> {"contentType", "response": streaming body}
(AgentCore devguide runtime-invoke-agent: join the chunks for application/json).
Never shows which model answered or any fallback.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import requests
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow `streamlit run ui/app.py`

from agent.config import get_settings  # noqa: E402

TITLE = "Sunrise Clinic — Front Desk Assistant"
NOT_RUNNING = "The assistant isn't available right now. Please try again in a minute."
FAILED = "Sorry, something went wrong. Please try again."
EXAMPLES = {
    "Book a knee appointment": "My knee hurts, can I see someone tomorrow?",
    "Visiting hours": "What are the visiting hours?",
    "Cancel a booking": "I want to cancel my booking.",
}


def call_agent(payload: dict) -> dict:
    """Send one turn to the agent (local server or AgentCore Runtime). Returns {"reply", "messages"}."""
    s = get_settings()
    if s.app_mode == "cloud":
        import boto3

        client = boto3.client("bedrock-agentcore", region_name=s.aws_region)
        response = client.invoke_agent_runtime(
            agentRuntimeArn=s.agent_runtime_arn,
            runtimeSessionId=payload["session_id"],
            payload=json.dumps(payload).encode("utf-8"),
        )
        body = b"".join(chunk for chunk in response["response"])
        return json.loads(body.decode("utf-8"))
    response = requests.post(s.local_agent_url, json=payload, timeout=180)
    response.raise_for_status()
    return response.json()


def send(prompt: str) -> str:
    """Call the agent and update session state. Returns the reply text to show (friendly on errors)."""
    state = st.session_state
    payload = {
        "prompt": prompt,
        "session_id": state.session_id,
        "patient_id": state.patient_id,
        "patient_name": state.patient_name,
        "messages": state.agent_messages,
    }
    try:
        result = call_agent(payload)
    except requests.exceptions.ConnectionError:
        return NOT_RUNNING
    except Exception:  # timeouts, AWS errors, bad JSON: no stack traces in the UI
        return FAILED
    if isinstance(result.get("messages"), list):
        state.agent_messages = result["messages"]
    return str(result.get("reply") or FAILED)


def new_session() -> None:
    st.session_state.session_id = str(uuid.uuid4())  # 36 chars (AgentCore needs >= 33)
    st.session_state.chat = []
    st.session_state.agent_messages = []


def main() -> None:
    st.set_page_config(page_title="Sunrise Clinic", page_icon="🏥")
    state = st.session_state
    if "session_id" not in state:
        new_session()

    with st.sidebar:
        st.text_input("Patient name", value="Ravi", key="patient_name")
        st.text_input("Patient ID", value="P001", key="patient_id")
        st.button("New session", on_click=new_session)

    st.title(TITLE)
    st.caption("Learning project: Sunrise Clinic is fictional and all data is fake. Not medical advice.")

    columns = st.columns(len(EXAMPLES))
    clicked = None
    for column, (label, text) in zip(columns, EXAMPLES.items()):
        if column.button(label, width="stretch"):
            clicked = text

    for message in state.chat:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    prompt = st.chat_input("Type your message") or clicked
    if prompt:
        with st.chat_message("user"):
            st.markdown(prompt)
        state.chat.append({"role": "user", "content": prompt})
        with st.chat_message("assistant"):
            with st.spinner("..."):
                reply = send(prompt)
            st.markdown(reply)
        state.chat.append({"role": "assistant", "content": reply})


main()
