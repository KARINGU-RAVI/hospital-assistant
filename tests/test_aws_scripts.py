"""Phase 4 tests: AWS setup/teardown scripts. Offline: requests are validated against botocore's own
service models (no network, no credentials), and the create/delete logic runs on fake clients."""
import boto3
import pytest
from botocore.validate import ParamValidator

from scripts import aws_setup, aws_teardown


def _validate(service: str, operation: str, params: dict) -> None:
    model = boto3.session.Session(region_name="ap-south-1").client(service)._service_model
    report = ParamValidator().validate(params, model.operation_model(operation).input_shape)
    assert not report.has_errors(), report.generate_report()


def test_guardrail_request_matches_bedrock_api():
    _validate("bedrock", "CreateGuardrail", aws_setup.guardrail_request())


def test_guardrail_request_limits():
    req = aws_setup.guardrail_request()
    for topic in req["topicPolicyConfig"]["topicsConfig"]:  # API limits: definition <= 200, <= 5 examples of <= 100
        assert len(topic["definition"]) <= 200 and len(topic["examples"]) <= 5
        assert all(len(e) <= 100 for e in topic["examples"])
    assert len(req["blockedInputMessaging"]) <= 500


def test_memory_request_matches_agentcore_api():
    _validate("bedrock-agentcore-control", "CreateMemory", {
        "name": aws_setup.MEMORY_NAME, "eventExpiryDuration": aws_setup.EVENT_EXPIRY_DAYS,
        "memoryStrategies": aws_setup.memory_strategies()})


class FakeBedrock:
    def __init__(self, existing=()):
        self.guardrails = [{"id": f"g{i}", "name": n} for i, n in enumerate(existing)]
        self.created, self.deleted = [], []

    def list_guardrails(self):
        return {"guardrails": self.guardrails}

    def create_guardrail(self, **kwargs):
        self.created.append(kwargs)
        self.guardrails.append({"id": "new", "name": kwargs["name"]})
        return {"guardrailId": "new"}

    def get_guardrail(self, **kwargs):
        return {"status": "READY"}

    def delete_guardrail(self, guardrailIdentifier):
        self.deleted.append(guardrailIdentifier)


def test_setup_creates_once_then_reuses():
    bedrock = FakeBedrock()
    assert aws_setup.ensure_guardrail(bedrock) == "new"
    assert aws_setup.ensure_guardrail(bedrock) == "new"  # second run reuses
    assert len(bedrock.created) == 1


def test_teardown_deletes_only_our_guardrail_and_is_safe_twice():
    bedrock = FakeBedrock(existing=[aws_setup.GUARDRAIL_NAME, "someone-elses-guardrail"])
    assert aws_teardown.delete_guardrails(bedrock) == 1 and bedrock.deleted == ["g0"]
    bedrock.guardrails = [g for g in bedrock.guardrails if g["id"] not in bedrock.deleted]
    assert aws_teardown.delete_guardrails(bedrock) == 0


class FakeControl:
    def __init__(self, memories):
        self.memories = memories  # id -> {"name", "status"}

    def list_memories(self):
        return {"memories": [{"id": i} for i in self.memories]}

    def get_memory(self, memoryId):
        return {"memory": {"id": memoryId, **self.memories[memoryId]}}


class FakeMemoryClient:
    def __init__(self):
        self.created, self.deleted = [], []

    def create_memory_and_wait(self, **kwargs):
        self.created.append(kwargs)
        return {"id": "mem-new"}

    def delete_memory_and_wait(self, memory_id):
        self.deleted.append(memory_id)


def test_memory_setup_reuses_existing_and_creates_with_7_days():
    client = FakeMemoryClient()
    existing = FakeControl({"m1": {"name": aws_setup.MEMORY_NAME, "status": "ACTIVE"}})
    assert aws_setup.ensure_memory(client, existing) == "m1" and client.created == []
    assert aws_setup.ensure_memory(client, FakeControl({})) == "mem-new"
    assert client.created[0]["event_expiry_days"] == 7
    assert client.created[0]["strategies"] == aws_setup.memory_strategies()


def test_memory_teardown_only_ours():
    client = FakeMemoryClient()
    control = FakeControl({"m1": {"name": aws_setup.MEMORY_NAME, "status": "ACTIVE"},
                           "m2": {"name": "other_memory", "status": "ACTIVE"}})
    assert aws_teardown.delete_memories(client, control) == 1 and client.deleted == ["m1"]


@pytest.mark.parametrize("module", [aws_setup, aws_teardown])
def test_scripts_cancel_without_confirmation(module, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    monkeypatch.setattr(boto3, "client", lambda *a, **k: pytest.fail("AWS client created without confirmation"))
    module.main()
    assert "Cancelled" in capsys.readouterr().out
