"""The only module that talks HTTP. A small client for the Roosterly helpdesk API.

Every request carries the key and the candidate email. Nothing here prints or logs a header
or an env value. Retry rules follow the API reference:
- 503 means nothing was saved, so any call is safe to repeat. Bounded exponential backoff.
- 429 means too fast. Wait Retry-After, then repeat. A limiter keeps us under the cap anyway.
- 409, 422, 401 and missing_email are never retried here. The caller decides what they mean.
- A timeout or reset on a POST is ambiguous: the decision may have been saved. `act` checks
  the ticket with a GET before it posts again.
"""

import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

ENV_VARS = ("HELPDESK_BASE_URL", "HELPDESK_API_KEY", "HELPDESK_EMAIL")
ACTIONS = ("reply", "handoff", "close")


class Fatal(Exception):
    """401 or missing_email. Every later call fails the same way, so the run stops."""


@dataclass
class HelpdeskError(Exception):
    status: int
    code: str
    problems: list = field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.status} {self.code}" + (f" {self.problems}" if self.problems else "")


class AlreadyHandled(HelpdeskError):
    """409. Never retried: a customer must not get two answers."""


class Invalid(HelpdeskError):
    """422. `problems` lists the fields the API rejected."""


@dataclass
class Settings:
    base_url: str
    api_key: str
    email: str
    name: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        missing = [n for n in ENV_VARS if not os.environ.get(n)]
        if missing:
            raise Fatal(f"{missing[0]} is not set. Put it in .env or export it. See .env.example.")
        return cls(
            base_url=os.environ["HELPDESK_BASE_URL"].rstrip("/"),
            api_key=os.environ["HELPDESK_API_KEY"],
            email=os.environ["HELPDESK_EMAIL"],
            name=os.environ.get("HELPDESK_NAME") or None,
        )


class Limiter:
    """Spaces requests at least 1/rps apart. The API returns 429 above 5 per second."""

    def __init__(self, rps: float, clock=time.monotonic, sleep=time.sleep) -> None:
        self.interval = 1.0 / rps
        self.clock, self.sleep = clock, sleep
        self.last = float("-inf")

    def wait(self) -> None:
        gap = self.last + self.interval - self.clock()
        if gap > 0:
            self.sleep(gap)
        self.last = self.clock()


@dataclass
class Retry:
    rps: float = 4.0
    max_503: int = 5  # attempts, counting the first
    max_429: int = 5
    max_network: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    max_retry_after: float = 30.0
    timeout: float = 20.0


class Helpdesk:
    def __init__(
        self,
        settings: Settings,
        retry: Retry | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.retry = retry or Retry()
        headers = {
            "Authorization": f"Bearer {settings.api_key}",
            "X-Candidate-Email": settings.email,
        }
        if settings.name:
            headers["X-Candidate-Name"] = settings.name
        self.http = httpx.Client(
            base_url=settings.base_url,
            headers=headers,
            timeout=self.retry.timeout,
            transport=transport,
        )
        self.sleep = sleep
        self.limiter = Limiter(self.retry.rps, sleep=sleep)
        # Counted per request including retries. The pipeline reads and resets it per ticket.
        self.calls = 0
        self.events: list[str] = []

    def _backoff(self, attempt: int) -> float:
        # Full jitter: spreads retries so a burst of 503s does not come back as a burst.
        cap = min(self.retry.max_delay, self.retry.base_delay * 2 ** (attempt - 1))
        return random.uniform(cap / 2, cap)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        """One logical call. Retries 503 and 429 within their own bounds. Network errors on a
        GET are retried; on a POST they propagate so `act` can check before posting again."""
        n503 = n429 = nnet = 0
        while True:
            self.limiter.wait()
            self.calls += 1
            try:
                resp = self.http.request(method, path, **kwargs)
            except httpx.TransportError:
                nnet += 1
                if method != "GET" or nnet >= self.retry.max_network:
                    raise
                self.events.append("network_retry")
                self.sleep(self._backoff(nnet))
                continue
            if resp.status_code == 503:
                n503 += 1
                self.events.append("503")
                if n503 >= self.retry.max_503:
                    raise HelpdeskError(503, "service_unavailable")
                self.sleep(self._backoff(n503))
                continue
            if resp.status_code == 429:
                n429 += 1
                self.events.append("429")
                if n429 >= self.retry.max_429:
                    raise HelpdeskError(429, "rate_limited")
                self.sleep(self._retry_after(resp))
                continue
            return self._parse(resp)

    def _retry_after(self, resp: httpx.Response) -> float:
        try:
            wait = float(resp.headers.get("Retry-After", "1"))
        except ValueError:
            wait = 1.0
        return min(max(wait, 0.0), self.retry.max_retry_after)

    @staticmethod
    def _parse(resp: httpx.Response) -> dict:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.is_success:
            return data
        code = data.get("error", "unknown") if isinstance(data, dict) else "unknown"
        if resp.status_code == 401 or code in ("unauthorized", "missing_email"):
            # The message names the header to fix. It never contains our key.
            raise Fatal(f"Helpdesk refused the credentials ({resp.status_code} {code}).")
        if resp.status_code == 409:
            raise AlreadyHandled(409, code)
        if resp.status_code == 422:
            raise Invalid(422, code, data.get("problems", []))
        raise HelpdeskError(resp.status_code, code)

    def status(self) -> dict:
        return self._request("GET", "/status")

    def examples(self) -> list[dict]:
        return self._request("GET", "/examples")["examples"]

    def ticket(self, ticket_id: str) -> dict:
        return self._request("GET", f"/tickets/{ticket_id}")

    def open_tickets(self, page_size: int = 10) -> list[dict]:
        """Follow next_cursor until it is null. The API caps a page at 10."""
        tickets: list[dict] = []
        cursor = None
        while True:
            params = {"limit": page_size, "status": "open"}
            if cursor:
                params["cursor"] = cursor
            page = self._request("GET", "/tickets", params=params)
            tickets.extend(page["tickets"])
            cursor = page.get("next_cursor")
            if not cursor:
                return tickets

    def reset(self) -> dict:
        return self._request("POST", "/practice/reset")

    def act(self, ticket_id: str, action: str, body: dict) -> str:
        """Post one decision. Returns how it ended: posted, already_handled or
        confirmed_after_timeout. Raises Invalid on 422 and HelpdeskError when retries run out."""
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action}")
        for _ in range(self.retry.max_network):
            try:
                self._request("POST", f"/tickets/{ticket_id}/{action}", json=body)
                return "posted"
            except AlreadyHandled:
                # Someone, maybe us before a crash, already answered. Confirm, never repost.
                self.ticket(ticket_id)
                return "already_handled"
            except httpx.TransportError:
                self.events.append("post_ambiguous")
                try:
                    handled = self.ticket(ticket_id).get("handled")
                except httpx.TransportError:
                    # Cannot tell whether the post landed. The journal intent stays pending and
                    # the next run settles it with a GET, so this is safe to give up on.
                    break
                if handled:
                    return "confirmed_after_timeout"
        raise HelpdeskError(0, "network_unavailable")

    def close(self) -> None:
        self.http.close()
