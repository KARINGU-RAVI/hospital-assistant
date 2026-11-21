"""Creates the guardrail + AgentCore Memory and prints their IDs. ASK before running (costs money).

Run (only after the developer confirms budget alerts and their own AWS login):
    python scripts\\aws_setup.py

Safe to run twice: existing resources with the same name are reused, not duplicated.
It never edits .env; it prints the IDs for the developer to add.

APIs checked: boto3 1.44.9 bedrock.create_guardrail / list_guardrails / get_guardrail (status READY);
bedrock-agentcore 1.27.0 MemoryClient.create_memory_and_wait(name, strategies, event_expiry_days) with
{"userPreferenceMemoryStrategy": {"name", "namespaceTemplates"}} (AgentCore devguide; "namespaces" is
deprecated); bedrock-agentcore-control list_memories / get_memory.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_settings  # noqa: E402
from agent.guardrails import SAFE_MESSAGE  # noqa: E402
from agent.memory import NAMESPACE_TEMPLATE  # noqa: E402

GUARDRAIL_NAME = "hospital-frontdesk-guardrail"
MEMORY_NAME = "hospital_frontdesk_memory"
EVENT_EXPIRY_DAYS = 7


def guardrail_request() -> dict:
    """create_guardrail arguments: two denied topics, phone numbers masked in model OUTPUT only."""
    return {
        "name": GUARDRAIL_NAME,
        "description": "Sunrise Clinic front desk (learning project): no diagnosis or medication advice.",
        "topicPolicyConfig": {
            "topicsConfig": [
                {
                    "name": "MedicalDiagnosis",
                    "definition": "Telling a person which illness, disease or condition they have, or guessing a diagnosis from symptoms.",
                    "examples": ["What disease do I have?", "Is my headache a tumour?", "Diagnose my chest rash."],
                    "type": "DENY",
                },
                {
                    "name": "MedicationAdvice",
                    "definition": "Recommending medicines, doses, dosages or prescriptions, or saying which drug to take.",
                    "examples": ["What dose of paracetamol should I take?", "Which antibiotic is best for a cough?",
                                 "Can you prescribe something for pain?"],
                    "type": "DENY",
                },
            ]
        },
        "sensitiveInformationPolicyConfig": {
            "piiEntitiesConfig": [
                # Mask phone numbers in replies; patients may still type their own number.
                {"type": "PHONE", "action": "ANONYMIZE", "inputEnabled": False, "outputEnabled": True,
                 "inputAction": "NONE", "outputAction": "ANONYMIZE"},
            ]
        },
        "blockedInputMessaging": SAFE_MESSAGE,
        "blockedOutputsMessaging": SAFE_MESSAGE,
    }


def memory_strategies() -> list[dict]:
    """One built-in user-preference strategy; records go to /patients/{actorId}/preferences/."""
    return [{"userPreferenceMemoryStrategy": {"name": "PatientPreferences", "namespaceTemplates": [NAMESPACE_TEMPLATE]}}]


def ensure_guardrail(bedrock) -> str:
    """Create the guardrail (or reuse one with the same name) and wait until READY. Returns its ID."""
    existing = [g for g in bedrock.list_guardrails().get("guardrails", []) if g.get("name") == GUARDRAIL_NAME]
    guardrail_id = existing[0]["id"] if existing else bedrock.create_guardrail(**guardrail_request())["guardrailId"]
    print(("Reusing" if existing else "Created") + f" guardrail {GUARDRAIL_NAME}; waiting for READY ...")
    for _ in range(60):
        status = bedrock.get_guardrail(guardrailIdentifier=guardrail_id, guardrailVersion="DRAFT")["status"]
        if status == "READY":
            return guardrail_id
        if status == "FAILED":
            raise RuntimeError("guardrail creation FAILED; check the Bedrock console")
        time.sleep(5)
    raise TimeoutError("guardrail not READY after 5 minutes")


def ensure_memory(memory_client, control) -> str:
    """Create the memory (or reuse one with the same name) and wait until ACTIVE. Returns its ID."""
    for summary in control.list_memories().get("memories", []):
        memory = control.get_memory(memoryId=summary["id"])["memory"]
        if memory.get("name") == MEMORY_NAME and memory.get("status") in ("ACTIVE", "CREATING"):
            print(f"Reusing memory {MEMORY_NAME} (status {memory['status']})")
            return summary["id"]
    print(f"Creating memory {MEMORY_NAME} (can take a few minutes) ...")
    created = memory_client.create_memory_and_wait(
        name=MEMORY_NAME,
        strategies=memory_strategies(),
        description="Sunrise Clinic front desk: patient preferences (learning project, fake data)",
        event_expiry_days=EVENT_EXPIRY_DAYS,
    )
    return created.get("id") or created.get("memoryId")


def main() -> None:
    import boto3
    from bedrock_agentcore.memory import MemoryClient

    region = get_settings().aws_region
    print(f"This creates PAID resources in {region}:")
    print(f"  - Bedrock guardrail '{GUARDRAIL_NAME}' (billed per text unit checked)")
    print(f"  - AgentCore Memory '{MEMORY_NAME}' (events kept {EVENT_EXPIRY_DAYS} days, one preference strategy)")
    if input("Type CREATE to continue: ").strip() != "CREATE":
        print("Cancelled. Nothing was created.")
        return

    guardrail_id = ensure_guardrail(boto3.client("bedrock", region_name=region))
    memory_id = ensure_memory(MemoryClient(region_name=region), boto3.client("bedrock-agentcore-control", region_name=region))

    print("\nDone. Add these lines to your .env (this script does not edit it):")
    print(f"GUARDRAIL_ID={guardrail_id}")
    print("GUARDRAIL_VERSION=DRAFT")
    print(f"MEMORY_ID={memory_id}")
    print("Then set GUARDRAILS_ENABLED=true and MEMORY_ENABLED=true.")
    print("Remember: run python scripts\\aws_teardown.py when you are finished today.")


if __name__ == "__main__":
    main()
