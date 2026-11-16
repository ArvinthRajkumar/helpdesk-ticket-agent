"""Helpdesk client against a scripted transport. No network, no sleeping."""

import httpx
import pytest

from ticket_agent.helpdesk import (
    AlreadyHandled,
    Fatal,
    Helpdesk,
    HelpdeskError,
    Invalid,
    Limiter,
    Retry,
    Settings,
)

SETTINGS = Settings(base_url="https://helpdesk.test", api_key="k-test", email="me@test", name="Me")
BODY = {"language": "nl", "category": "question", "priority": "P4", "reason": "r"}


def client(responses: list, seen: list | None = None) -> tuple[Helpdesk, list[float]]:
    """Each item is (status, json, headers) or an exception to raise for that request."""
    queue = list(responses)
    seen = seen if seen is not None else []
    slept: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        status, payload, headers = (*item, {})[:3]
        return httpx.Response(status, json=payload, headers=headers)

    hd = Helpdesk(SETTINGS, Retry(), transport=httpx.MockTransport(handler), sleep=slept.append)
    return hd, slept


def test_every_request_sends_key_email_and_name() -> None:
    seen: list = []
    hd, _ = client([(200, {"mode": "practice"})], seen)
    hd.status()
    h = seen[0].headers
    assert h["Authorization"] == "Bearer k-test"
    assert h["X-Candidate-Email"] == "me@test" and h["X-Candidate-Name"] == "Me"


def test_pagination_follows_cursor_until_null() -> None:
    seen: list = []
    pages = [
        (200, {"tickets": [{"id": "T1"}, {"id": "T2"}], "next_cursor": "T2"}),
        (200, {"tickets": [{"id": "T3"}], "next_cursor": None}),
    ]
    hd, _ = client(pages, seen)
    assert [t["id"] for t in hd.open_tickets()] == ["T1", "T2", "T3"]
    assert seen[1].url.params["cursor"] == "T2" and seen[1].url.params["limit"] == "10"


def test_503_backs_off_then_succeeds() -> None:
    hd, slept = client([(503, {}), (503, {}), (200, {"ok": True})])
    assert hd.status() == {"ok": True}
    assert len(slept) >= 2 and hd.calls == 3


def test_503_gives_up_after_bounded_attempts() -> None:
    hd, _ = client([(503, {})] * 5)
    with pytest.raises(HelpdeskError) as exc:
        hd.status()
    assert exc.value.status == 503 and hd.calls == 5


def test_429_waits_retry_after() -> None:
    hd, slept = client([(429, {}, {"Retry-After": "2"}), (200, {"ok": True})])
    hd.status()
    assert 2.0 in slept


def test_409_is_not_retried_and_is_confirmed_with_a_get() -> None:
    seen: list = []
    hd, _ = client([(409, {"error": "already_handled"}), (200, {"handled": {"x": 1}})], seen)
    assert hd.act("T1", "reply", BODY) == "already_handled"
    assert [r.method for r in seen] == ["POST", "GET"]


def test_422_surfaces_the_problems() -> None:
    hd, _ = client([(422, {"error": "validation_failed", "problems": ["handoff_note missing"]})])
    with pytest.raises(Invalid) as exc:
        hd.act("T1", "handoff", BODY)
    assert exc.value.problems == ["handoff_note missing"]


@pytest.mark.parametrize(
    "status,payload", [(401, {"error": "unauthorized"}), (400, {"error": "missing_email"})]
)
def test_auth_problems_are_fatal(status: int, payload: dict) -> None:
    hd, _ = client([(status, payload)])
    with pytest.raises(Fatal):
        hd.status()


def test_post_timeout_checks_the_ticket_and_does_not_repost_if_handled() -> None:
    seen: list = []
    hd, _ = client(
        [httpx.ReadTimeout("t"), (200, {"id": "T1", "handled": {"action": "reply"}})], seen
    )
    assert hd.act("T1", "reply", BODY) == "confirmed_after_timeout"
    assert [r.method for r in seen] == ["POST", "GET"]


def test_post_timeout_reposts_only_when_still_open() -> None:
    seen: list = []
    hd, _ = client([httpx.ConnectError("x"), (200, {"handled": None}), (200, {})], seen)
    assert hd.act("T1", "reply", BODY) == "posted"
    assert [r.method for r in seen] == ["POST", "GET", "POST"]


def test_already_handled_is_a_helpdesk_error() -> None:
    assert issubclass(AlreadyHandled, HelpdeskError)


def test_missing_env_var_is_named(monkeypatch) -> None:
    for name in ("HELPDESK_BASE_URL", "HELPDESK_API_KEY", "HELPDESK_EMAIL"):
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("HELPDESK_EMAIL")
    with pytest.raises(Fatal, match="HELPDESK_EMAIL"):
        Settings.from_env()


def test_limiter_spaces_requests() -> None:
    now = {"t": 10.0}
    slept: list[float] = []

    def sleep(s: float) -> None:
        slept.append(s)
        now["t"] += s

    lim = Limiter(4, clock=lambda: now["t"], sleep=sleep)
    for _ in range(5):
        lim.wait()
    assert slept == [pytest.approx(0.25)] * 4
