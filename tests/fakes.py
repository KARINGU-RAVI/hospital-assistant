"""Fake Strands model for offline tests: replies, raises queued errors, or makes scripted tool calls."""
from __future__ import annotations

import json
from itertools import count

from strands.models import Model

_ids = count(1)


def tool_call(name: str, **tool_input) -> dict:
    """A scripted step: the model calls `name` with `tool_input`."""
    return {"tool": name, "input": tool_input}


class FakeModel(Model):
    """Each model call: raise the next queued error, else play the next scripted step, else reply text.

    A script step is a str (final text reply) or a tool_call(...) dict.
    """

    def __init__(self, reply: str = "ok", errors: list[Exception] | None = None, script: list | None = None):
        self.reply, self.errors, self.script, self.calls = reply, list(errors or []), list(script or []), 0
        self.received: list[list[dict]] = []  # the messages sent on each model call
        self.system_prompts: list[str] = []

    def update_config(self, **kwargs):
        pass

    def get_config(self):
        return {}

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError
        yield  # pragma: no cover

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        self.calls += 1
        self.received.append(json.loads(json.dumps(messages, default=str)))
        self.system_prompts.append(system_prompt or "")
        if self.errors:
            raise self.errors.pop(0)
        step = self.script.pop(0) if self.script else self.reply
        yield {"messageStart": {"role": "assistant"}}
        if isinstance(step, dict):
            use_id = f"tool-{next(_ids)}"
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": use_id, "name": step["tool"]}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(step["input"])}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
            return
        yield {"contentBlockStart": {"start": {}}}
        yield {"contentBlockDelta": {"delta": {"text": step}}}
        yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "end_turn"}}
