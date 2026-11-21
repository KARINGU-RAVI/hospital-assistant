"""Deletes guardrail, memory, runtime, ECR repo. Asks for 'DELETE' first. Safe to run twice.

Run: python scripts\\aws_teardown.py

Finds resources by NAME (same names as aws_setup.py), so it works even if .env is incomplete.
Phase 4: guardrail + memory. Phase 6 adds the AgentCore runtime and its ECR repository.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.config import get_settings  # noqa: E402
from scripts.aws_setup import GUARDRAIL_NAME, MEMORY_NAME  # noqa: E402


def _is_not_found(error: Exception) -> bool:
    code = getattr(error, "response", {}).get("Error", {}).get("Code", "")
    return code in ("ResourceNotFoundException", "NotFoundException")


def delete_guardrails(bedrock) -> int:
    """Delete every guardrail named GUARDRAIL_NAME. Returns how many were deleted."""
    deleted = 0
    for g in bedrock.list_guardrails().get("guardrails", []):
        if g.get("name") != GUARDRAIL_NAME:
            continue
        try:
            bedrock.delete_guardrail(guardrailIdentifier=g["id"])
            deleted += 1
        except Exception as e:
            if not _is_not_found(e):
                raise
    return deleted


def delete_memories(memory_client, control) -> int:
    """Delete every memory named MEMORY_NAME (waits until gone). Returns how many were deleted."""
    deleted = 0
    for summary in control.list_memories().get("memories", []):
        try:
            memory = control.get_memory(memoryId=summary["id"])["memory"]
            if memory.get("name") != MEMORY_NAME or memory.get("status") == "DELETING":
                continue
            memory_client.delete_memory_and_wait(memory_id=summary["id"])
            deleted += 1
        except Exception as e:
            if not _is_not_found(e):
                raise
    return deleted


def main() -> None:
    import boto3
    from bedrock_agentcore.memory import MemoryClient

    region = get_settings().aws_region
    print(f"This DELETES in {region}: guardrail '{GUARDRAIL_NAME}' and AgentCore Memory '{MEMORY_NAME}' "
          "(all stored events and preferences).")
    if input("Type DELETE to continue: ").strip() != "DELETE":
        print("Cancelled. Nothing was deleted.")
        return
    g = delete_guardrails(boto3.client("bedrock", region_name=region))
    print(f"Guardrails deleted: {g}")
    m = delete_memories(MemoryClient(region_name=region), boto3.client("bedrock-agentcore-control", region_name=region))
    print(f"Memories deleted: {m}")
    print("Done. Set GUARDRAILS_ENABLED=false and MEMORY_ENABLED=false in .env. "
          "Check the AWS console that nothing is left.")


if __name__ == "__main__":
    main()
