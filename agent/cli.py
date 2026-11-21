"""Terminal chat tester: python -m agent.cli   (type 'exit' to quit)"""
from __future__ import annotations

import logging
import uuid

from agent.agent import chat
from agent.config import get_settings

PATIENT_ID, PATIENT_NAME = "P001", "Ravi"


def main() -> None:
    """Simple chat loop as patient P001 / Ravi. Keeps the conversation between turns."""
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    for noisy in ("strands", "httpx", "google_genai", "openai"):  # keep only our one-line fallback logs
        logging.getLogger(noisy).setLevel(logging.ERROR)

    if not get_settings().db_path.exists():
        print("No database yet. Run: python data\\seed.py")
        return

    print(f"Sunrise Clinic front desk (you are {PATIENT_NAME}). Type 'exit' to quit.\n")
    messages: list[dict] = []
    session_id = uuid.uuid4().hex  # one memory session per CLI run
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if text.lower() in {"exit", "quit"}:
            break
        if not text:
            continue
        result = chat(text, messages, PATIENT_ID, PATIENT_NAME, session_id=session_id)
        messages = result["messages"]
        print(f"desk> {result['reply']}\n")


if __name__ == "__main__":
    main()
