"""Phase 2 tests: history trimming keeps requests small and never splits a tool call from its result."""
from agent.models import approx_tokens, trim_history


def _user(text):
    return {"role": "user", "content": [{"text": text}]}


def _assistant(text):
    return {"role": "assistant", "content": [{"text": text}]}


def _tool_use(use_id):
    return {"role": "assistant", "content": [{"toolUse": {"toolUseId": use_id, "name": "check_availability", "input": {}}}]}


def _tool_result(use_id, size=10):
    return {"role": "user", "content": [{"toolResult": {"toolUseId": use_id, "status": "success",
                                                         "content": [{"text": "x" * size}]}}]}


def _turn_with_tools(n, size=10):
    return [_user(f"q{n}"), _tool_use(f"t{n}"), _tool_result(f"t{n}", size), _assistant(f"a{n}")]


def _pairs_intact(messages) -> bool:
    uses = {b["toolUse"]["toolUseId"] for m in messages for b in m["content"] if "toolUse" in b}
    results = {b["toolResult"]["toolUseId"] for m in messages for b in m["content"] if "toolResult" in b}
    return uses == results


def test_short_history_is_unchanged():
    history = _turn_with_tools(1)
    assert trim_history(history, 6, 3000) == history


def test_trimmed_history_stays_under_limits_and_keeps_pairs():
    history = [m for n in range(10) for m in _turn_with_tools(n, size=200)]
    trimmed = trim_history(history, 6, 3000)
    assert 0 < len(trimmed) <= 6
    assert approx_tokens(trimmed) <= 3000
    assert trimmed[0] == _user("q9")  # starts at a patient message
    assert _pairs_intact(trimmed)
    assert trimmed == history[-len(trimmed):]  # most recent messages


def test_token_limit_wins_over_message_limit():
    history = _turn_with_tools(1, size=8000) + _turn_with_tools(2, size=10)
    trimmed = trim_history(history, 6, 3000)
    assert trimmed == _turn_with_tools(2, size=10)


def test_oversized_turn_falls_back_to_text_only():
    """Bug found live: a booking turn with several tool calls was dropped entirely, so the model
    didn't know what the patient's "yes" referred to."""
    history = [_user("book Dr. Kapoor on the 24th at 15:00"), _tool_use("a"), _tool_result("a"),
               _tool_use("b"), _tool_result("b"), _tool_use("c"), _tool_result("c"),
               _assistant("Shall I book Dr. Kapoor on 24 Nov at 15:00?")]  # one turn, 8 messages
    assert trim_history(history, 6, 3000) == [
        _user("book Dr. Kapoor on the 24th at 15:00"), _assistant("Shall I book Dr. Kapoor on 24 Nov at 15:00?")]


def test_text_only_keeps_last_reply_per_turn_and_no_tool_blocks():
    from agent.models import text_only
    mixed = {"role": "assistant", "content": [{"text": "Let me check."}, {"toolUse": {"toolUseId": "x", "name": "n", "input": {}}}]}
    history = [_user("q1"), mixed, _tool_result("x"), _assistant("a1"), _user("q2"), _tool_use("y"), _tool_result("y"), _assistant("a2")]
    assert text_only(history) == [_user("q1"), _assistant("a1"), _user("q2"), _assistant("a2")]
    assert _pairs_intact(text_only(history))


def test_every_window_keeps_pairs_together():
    history = [m for n in range(6) for m in _turn_with_tools(n, size=300 * n)]
    for max_messages in range(1, 12):
        for max_tokens in (100, 500, 1000, 3000):
            trimmed = trim_history(history, max_messages, max_tokens)
            assert len(trimmed) <= max_messages and approx_tokens(trimmed) <= max_tokens
            assert _pairs_intact(trimmed)
            assert not trimmed or trimmed[0]["role"] == "user" and trimmed[0]["content"][0].get("text")
