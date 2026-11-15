"""One structured JSON call to Claude, one retry on schema failure, and a fake for tests.

The agent has no tools. The model can only return a JSON object that pydantic validates.
Nothing here reads the API key: the SDK picks ANTHROPIC_API_KEY up from the environment.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import anthropic
from pydantic import BaseModel, ValidationError


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class Call:
    model: str
    usage: Usage
    latency_ms: int
    stop_reason: str | None
    text: str


class Client(Protocol):
    def complete(
        self, *, model: str, system: str, user: str, schema: dict, max_tokens: int, effort: str
    ) -> Call: ...


class AnthropicClient:
    def __init__(self) -> None:
        # Default retries (2) cover 429 and 5xx. Anything that still fails becomes an error
        # the caller records, never an exception that stops the run.
        self._client = anthropic.Anthropic()

    def complete(
        self, *, model: str, system: str, user: str, schema: dict, max_tokens: int, effort: str
    ) -> Call:
        started = time.perf_counter()
        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            # cache_control only pays off above the model's minimum prefix (512 tokens on
            # Haiku 5.5 and Sonnet 5.5). Usage fields show whether it actually cached.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            # No temperature: Haiku 5.5 and Sonnet 5.5 reject non-default sampling values.
            # No forced tool_choice: Sonnet 5.5 rejects it. The JSON schema does the forcing.
            output_config={"format": {"type": "json_schema", "schema": schema}, "effort": effort},
        )
        usage = response.usage
        return Call(
            model=response.model,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens or 0,
                cache_write_tokens=usage.cache_creation_input_tokens or 0,
            ),
            latency_ms=int((time.perf_counter() - started) * 1000),
            stop_reason=response.stop_reason,
            text="".join(block.text for block in response.content if block.type == "text"),
        )


class FakeClient:
    """Returns canned replies in order. An Exception in the list is raised instead."""

    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, model: str, system: str, user: str, **kwargs: Any) -> Call:
        self.calls.append({"model": model, "system": system, "user": user, **kwargs})
        if not self.replies:
            raise RuntimeError("FakeClient ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return Call(
            model=model, usage=Usage(100, 20), latency_ms=1, stop_reason="end_turn", text=reply
        )


@dataclass
class Outcome:
    parsed: BaseModel | None
    error: str | None
    calls: list[Call] = field(default_factory=list)


def api_schema(model: type[BaseModel]) -> dict:
    """Pydantic schema minus string length limits, which the API rejects.

    Pydantic still enforces them on our side."""

    def strip(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip(v) for k, v in node.items() if k not in ("minLength", "maxLength")}
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    return strip(model.model_json_schema())


def structured_call(
    client: Client,
    *,
    model: str,
    system: str,
    user: str,
    schema_model: type[BaseModel],
    max_tokens: int,
    effort: str,
) -> Outcome:
    """Ask for JSON, validate it, retry once with the validation error, then give up."""
    schema = api_schema(schema_model)
    calls: list[Call] = []
    prompt = user
    error = "no attempt made"
    for _attempt in range(2):
        try:
            call = client.complete(
                model=model,
                system=system,
                user=prompt,
                schema=schema,
                max_tokens=max_tokens,
                effort=effort,
            )
        except anthropic.APIError as exc:
            # SDK errors become a recorded error so the ticket routes to a human. Anything
            # else is a bug in this code and should crash loudly; resume picks up afterwards.
            status = getattr(exc, "status_code", None)
            detail = f" status={status}" if status else ""
            return Outcome(None, f"api_error: {type(exc).__name__}{detail}", calls)
        calls.append(call)
        if call.stop_reason == "refusal":
            return Outcome(None, "refusal: model declined the request", calls)
        if call.stop_reason == "max_tokens":
            # Retrying with the same budget would truncate again, so stop here.
            return Outcome(None, "max_tokens: output truncated, raise max_tokens in config", calls)
        try:
            return Outcome(schema_model.model_validate_json(call.text), None, calls)
        except ValidationError as exc:
            # Only field paths and messages, never the offending values, so no ticket text
            # leaks into the log through an error string.
            problems = "; ".join(
                f"{'.'.join(map(str, e['loc'])) or 'root'}: {e['msg']}" for e in exc.errors()
            )
            error = f"schema_error: {problems}"
            prompt = (
                f"{user}\n\nYour previous answer failed validation: {problems}\n"
                "Return only the JSON object."
            )
    return Outcome(None, error, calls)


def load_prompt(path: Path) -> tuple[str, str]:
    """First line is `version: <id>` so the audit log can say which prompt produced a result."""
    first, _, rest = path.read_text(encoding="utf-8").partition("\n")
    if not first.startswith("version:"):
        raise ValueError(f"{path} must start with a version line")
    return first.split(":", 1)[1].strip(), rest.strip()
