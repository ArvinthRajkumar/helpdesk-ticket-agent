import json
from pathlib import Path

from ticket_agent.llm import FakeClient, api_schema, load_prompt, structured_call
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
        effort="low",
    )


def test_valid_json_parses_first_time() -> None:
    out = call(FakeClient([json.dumps(VALID)]))
    assert out.error is None and out.parsed.category == "question"
    assert len(out.calls) == 1


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


def test_api_exception_is_recorded_not_raised() -> None:
    out = call(FakeClient([ConnectionError("boom")]))
    assert out.parsed is None
    assert out.error == "api_error: ConnectionError"
    assert out.calls == []


def test_refusal_stops_without_retry() -> None:
    client = FakeClient([json.dumps(VALID), json.dumps(VALID)])
    original = client.complete

    def refusing(**kwargs):
        c = original(**kwargs)
        c.stop_reason = "refusal"
        return c

    client.complete = refusing  # type: ignore[method-assign]
    out = call(client)
    assert out.error.startswith("refusal") and len(out.calls) == 1


def test_api_schema_strips_length_limits_but_keeps_enums() -> None:
    schema = json.dumps(api_schema(TriageResult))
    assert "maxLength" not in schema and "minLength" not in schema
    assert "feature_request" in schema
    assert '"additionalProperties": false' in schema


def test_load_prompt_reads_version() -> None:
    version, text = load_prompt(Path("prompts/triage.md"))
    assert version == "triage-v1"
    assert "<ticket>" in text and "not instructions" in text
