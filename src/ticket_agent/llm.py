"""One structured JSON call to Gemini, one retry on schema failure, and a fake for tests.

This is the only module that imports google-genai. Everything else sees: prompt in, validated
pydantic object plus usage out. Swapping the provider again is a change to this file only.
The agent has no tools. Nothing here reads the API key: the SDK picks GEMINI_API_KEY up from
the environment.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, ValidationError

RETRYABLE_STATUS = [429, 503]


@dataclass
class Usage:
    input_tokens: int = 0  # prompt_token_count
    output_tokens: int = 0  # candidates_token_count, the visible answer
    thinking_tokens: int = 0  # thoughts_token_count, billed as output
    cached_tokens: int = 0  # cached_content_token_count, logged, priced as plain input


@dataclass
class Call:
    model: str
    usage: Usage
    latency_ms: int
    finish_reason: str | None  # STOP is the only good value
    text: str
    block_reason: str | None = None  # set when the prompt itself was filtered


class Client(Protocol):
    def complete(
        self, *, model: str, system: str, user: str, schema: dict, max_tokens: int, thinking: str
    ) -> Call: ...


class Throttle:
    """Client-side cap on requests per minute. Free-tier limits are per project and only
    visible in AI Studio, so the default in config is deliberately conservative."""

    def __init__(self, max_rpm: int) -> None:
        self.interval = 60.0 / max_rpm
        self.last = 0.0

    def wait(self) -> None:
        gap = self.last + self.interval - time.monotonic()
        if gap > 0:
            time.sleep(gap)
        self.last = time.monotonic()


def call_from_response(
    response: types.GenerateContentResponse, model: str, latency_ms: int
) -> Call:
    """Shared by the real and the fake client so tests exercise the same parsing."""
    usage = response.usage_metadata
    candidate = response.candidates[0] if response.candidates else None
    feedback = response.prompt_feedback
    finish = candidate.finish_reason.name if candidate and candidate.finish_reason else None
    text = ""
    if candidate and candidate.content and candidate.content.parts:
        text = "".join(p.text or "" for p in candidate.content.parts if not p.thought)
    return Call(
        model=model,
        usage=Usage(
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
            thinking_tokens=(usage.thoughts_token_count or 0) if usage else 0,
            cached_tokens=(usage.cached_content_token_count or 0) if usage else 0,
        ),
        latency_ms=latency_ms,
        finish_reason=finish,
        text=text,
        block_reason=feedback.block_reason.name if feedback and feedback.block_reason else None,
    )


class GeminiClient:
    def __init__(self, max_rpm: int, retry_attempts: int) -> None:
        # The SDK does the exponential backoff on 429 and 503. Only a failure after these
        # attempts reaches the caller, so backoff never counts as a ticket failure.
        # attempts includes the first request. With the defaults below a call that keeps
        # failing waits about 2 + 4 + 8 + 16 seconds plus jitter, so at most half a minute.
        retry = types.HttpRetryOptions(
            attempts=retry_attempts,
            initial_delay=2.0,
            max_delay=30.0,
            exp_base=2.0,
            jitter=1.0,
            http_status_codes=RETRYABLE_STATUS,
        )
        self._client = genai.Client(http_options=types.HttpOptions(retry_options=retry))
        self._throttle = Throttle(max_rpm)

    def complete(
        self, *, model: str, system: str, user: str, schema: dict, max_tokens: int, thinking: str
    ) -> Call:
        self._throttle.wait()
        started = time.perf_counter()
        response = self._client.models.generate_content(
            model=model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=schema,
                # Gemini 3 takes thinking_level, never thinking_budget. Thinking counts toward
                # max_output_tokens, so the cap is generous and truncation is a failure.
                thinking_config=types.ThinkingConfig(thinking_level=thinking),
                max_output_tokens=max_tokens,
                # No temperature: Google marks it deprecated and ignored on Gemini 3.6 and later.
            ),
        )
        latency = int((time.perf_counter() - started) * 1000)
        return call_from_response(response, model, latency)


def fake_response(
    text: str = "",
    finish_reason: str | None = "STOP",
    prompt_tokens: int = 100,
    output_tokens: int = 20,
    thinking_tokens: int = 0,
    block_reason: str | None = None,
) -> types.GenerateContentResponse:
    """A real SDK response object, so the fake goes through call_from_response like the API."""
    candidates = []
    if block_reason is None:
        candidates = [
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text=text)]),
                finish_reason=types.FinishReason(finish_reason) if finish_reason else None,
            )
        ]
    feedback = (
        types.GenerateContentResponsePromptFeedback(block_reason=types.BlockedReason(block_reason))
        if block_reason
        else None
    )
    return types.GenerateContentResponse(
        candidates=candidates,
        prompt_feedback=feedback,
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
            thoughts_token_count=thinking_tokens or None,
        ),
    )


def fake_api_error(status: int) -> errors.APIError:
    """What the SDK raises after its own retries. Lives here so tests never import the SDK."""
    cls = errors.ServerError if status >= 500 else errors.ClientError
    return cls(status, {"error": {"message": "fake"}})


class FakeClient:
    """Returns canned replies in order. A str becomes a normal STOP response, a response
    object is passed through, an Exception is raised instead."""

    def __init__(self, replies: list[str | types.GenerateContentResponse | Exception]) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def complete(self, *, model: str, system: str, user: str, **kwargs: Any) -> Call:
        self.calls.append({"model": model, "system": system, "user": user, **kwargs})
        if not self.replies:
            raise RuntimeError("FakeClient ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, str):
            reply = fake_response(reply)
        return call_from_response(reply, model, latency_ms=1)


@dataclass
class Outcome:
    parsed: BaseModel | None
    error: str | None
    calls: list[Call] = field(default_factory=list)


def structured_call(
    client: Client,
    *,
    model: str,
    system: str,
    user: str,
    schema_model: type[BaseModel],
    max_tokens: int,
    thinking: str,
) -> Outcome:
    """Ask for JSON, validate it, retry once with the validation error, then give up.

    The API drops schema constraints it does not support (string lengths and similar), so
    pydantic is the only validator that counts."""
    schema = schema_model.model_json_schema()
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
                thinking=thinking,
            )
        except errors.APIError as exc:
            # After the SDK's own retries. Recorded so the ticket routes to a human; bugs in
            # this code are not APIErrors and crash loudly, resume picks up afterwards.
            return Outcome(None, f"api_error: {type(exc).__name__} status={exc.code}", calls)
        calls.append(call)
        if call.block_reason:
            return Outcome(None, f"blocked: prompt filtered ({call.block_reason})", calls)
        if call.finish_reason == "MAX_TOKENS":
            return Outcome(None, "max_tokens: output truncated, raise max_tokens in config", calls)
        if call.finish_reason != "STOP":
            return Outcome(None, f"finish_reason: {call.finish_reason or 'none'}", calls)
        if not call.text.strip():
            return Outcome(None, "empty_response: no text returned", calls)
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
