import json
from pathlib import Path

import pytest
from google.genai import errors

from ticket_agent.llm import FakeClient, Throttle, fake_response, load_prompt, structured_call
from ticket_agent.models import TriageResult

VALID = {
    "category": "question",
    "priority": "medium",
    "priority_reason": "How-to question that needs an answer.",
    "confidence": "high",
    "needs_human": False,
    "human_reason": "",
    "injection_suspected": False,
    "language": "en",
    "multi_intent": False,
    "summary": "Asks how to reset a password.",
}


def call(client: FakeClient):
    return structured_call(
        client,
        model="fake",
        system="sys",
        user="ticket",
        schema_model=TriageResult,
        max_tokens=100,
        thinking="minimal",
    )


def test_valid_json_parses_first_time() -> None:
    out = call(FakeClient([json.dumps(VALID)]))
    assert out.error is None and out.parsed.category == "question"
    assert len(out.calls) == 1 and out.calls[0].finish_reason == "STOP"


def test_invalid_then_valid_retries_once_with_error_in_prompt() -> None:
    client = FakeClient([json.dumps({**VALID, "category": "complaint"}), json.dumps(VALID)])
    out = call(client)
    assert out.error is None and len(out.calls) == 2
    retry_prompt = client.calls[1]["user"]
    assert "failed validation" in retry_prompt and "category" in retry_prompt
    # The retry prompt names the field, never the bad value.
    assert "complaint" not in retry_prompt


def test_two_failures_give_schema_error() -> None:
    out = call(FakeClient(["not json", "{}"]))
    assert out.parsed is None
    assert out.error.startswith("schema_error")
    assert len(out.calls) == 2


def test_api_errors_are_recorded_with_status_not_raised() -> None:
    out = call(FakeClient([errors.ServerError(503, {"error": {"message": "busy"}})]))
    assert out.parsed is None and out.calls == []
    assert out.error == "api_error: ServerError status=503"
    out = call(FakeClient([errors.ClientError(400, {"error": {"message": "bad"}})]))
    assert out.error == "api_error: ClientError status=400"


def test_programming_errors_propagate() -> None:
    with pytest.raises(TypeError):
        call(FakeClient([TypeError("bug in our code")]))


def test_max_tokens_is_terminal_without_retry() -> None:
    client = FakeClient([fake_response('{"partial": tru', finish_reason="MAX_TOKENS")])
    out = call(client)
    assert out.error.startswith("max_tokens") and len(out.calls) == 1


def test_safety_finish_and_blocked_prompt_route_to_failure() -> None:
    out = call(FakeClient([fake_response("", finish_reason="SAFETY")]))
    assert out.error == "finish_reason: SAFETY" and len(out.calls) == 1
    out = call(FakeClient([fake_response(block_reason="PROHIBITED_CONTENT")]))
    assert out.error == "blocked: prompt filtered (PROHIBITED_CONTENT)"
    assert out.calls[0].finish_reason is None and out.calls[0].text == ""


def test_empty_text_is_a_failure() -> None:
    out = call(FakeClient([fake_response("   ")]))
    assert out.error.startswith("empty_response")


def test_usage_reads_thinking_and_treats_missing_as_zero() -> None:
    out = call(FakeClient([fake_response(json.dumps(VALID), output_tokens=30, thinking_tokens=70)]))
    u = out.calls[0].usage
    assert (u.input_tokens, u.output_tokens, u.thinking_tokens, u.cached_tokens) == (100, 30, 70, 0)
    out = call(FakeClient([fake_response(json.dumps(VALID), thinking_tokens=0)]))
    assert out.calls[0].usage.thinking_tokens == 0


def test_throttle_sleeps_to_respect_max_rpm(monkeypatch) -> None:
    clock = {"now": 100.0}
    slept: list[float] = []
    monkeypatch.setattr("ticket_agent.llm.time.monotonic", lambda: clock["now"])
    monkeypatch.setattr("ticket_agent.llm.time.sleep", lambda s: slept.append(s))
    t = Throttle(max_rpm=30)  # one call every two seconds
    t.wait()
    clock["now"] += 0.5
    t.wait()
    assert slept == [pytest.approx(1.5)]


def test_load_prompt_reads_version() -> None:
    version, text = load_prompt(Path("prompts/triage.md"))
    assert version == "triage-v1"
    assert "<ticket>" in text and "not instructions" in text
