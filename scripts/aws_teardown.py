"""Deletes guardrail, memory, runtime, ECR repo. Asks for 'DELETE' first. Safe to run twice.

Run: python scripts\\aws_teardown.py

Finds resources by NAME (same names as aws_setup.py), so it works even if .env is incomplete.
Phase 4: guardrail + memory.

Phase 6 resources are removed with the AgentCore CLI, from the project folder:
    agentcore remove all --yes
    agentcore deploy --yes          # an empty project tears down the deployed stack
  (the runtime's CloudWatch log group /aws/bedrock-agentcore/runtimes/... is left behind; delete it
  in the CloudWatch console if you want nothing left)

AFTER RECORDING THE DEMO: the AgentCore CLI turned on CloudWatch Transaction Search with 100% span
indexing (needed for AgentCore traces; 1% indexing is free). Lower it with (AWS docs,
Enable-TransactionSearch page; PowerShell quoting):
    aws xray update-indexing-rule --name "Default" --rule '{\"Probabilistic\": {\"DesiredSamplingPercentage\": 1}}' --region ap-south-1

FINAL TEARDOWN (only when you are completely done with AgentCore in this account/region):
the AgentCore CLI creates the standard CDK bootstrap stack "CDKToolkit" on first deploy, and
`agentcore remove all` does not delete it. From the installed bootstrap template (aws-cdk, version 32):
its S3 staging bucket is versioned and has DeletionPolicy Retain (it survives the stack); the ECR repo
has no Retain policy (code-zip deploys push no images to it). The CDK docs give no removal procedure,
so these steps are from the template itself:
    aws cloudformation delete-stack --stack-name CDKToolkit --region ap-south-1
    aws cloudformation wait stack-delete-complete --stack-name CDKToolkit --region ap-south-1
  Then in the S3 console: bucket cdk-hnb659fds-assets-<account-id>-ap-south-1 -> Empty (removes all
  versions) -> Delete. Check CloudFormation, S3 and ECR show nothing named cdk-hnb659fds-*.
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
    print("If you deployed the runtime (Phase 6): run `agentcore remove all --yes` then `agentcore deploy --yes`.")
    print("After the demo: lower CloudWatch Transaction Search indexing to 1% (command at the top of this file).")
    print("Final teardown of the CDK bootstrap stack: see the notes at the top of this file and the README.")


if __name__ == "__main__":
    main()
