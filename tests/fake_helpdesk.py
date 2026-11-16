"""A stateful in-memory helpdesk behind httpx.MockTransport, for pipeline tests."""

import json
import re

import httpx

from ticket_agent.helpdesk import Helpdesk, Retry, Settings


class Crash(BaseException):
    """Stands in for the process dying. BaseException so no handler in our code catches it."""


class FakeServer:
    def __init__(self, ids: list[str]) -> None:
        self.tickets = {
            i: {"id": i, "subject": f"subject {i}", "body": f"body {i}", "handled": None}
            for i in ids
        }
        self.posts: list[tuple[str, str]] = []
        # Hooks keyed by (method, ticket_id): "503", "422", "crash_after_save", "crash_before_save"
        self.faults: dict[tuple[str, str], list[str]] = {}

    def fault(self, method: str, ticket_id: str, *kinds: str) -> None:
        self.faults[(method, ticket_id)] = list(kinds)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/status":
            open_n = sum(1 for t in self.tickets.values() if not t["handled"])
            return httpx.Response(200, json={"mode": "practice", "open_tickets": open_n})
        if path == "/tickets":
            open_t = [t for t in self.tickets.values() if not t["handled"]]
            return httpx.Response(200, json={"tickets": open_t, "next_cursor": None})
        m = re.fullmatch(r"/tickets/(\w+)(?:/(reply|handoff|close))?", path)
        if not m or m.group(1) not in self.tickets:
            return httpx.Response(404, json={"error": "not_found"})
        tid, action = m.groups()
        ticket = self.tickets[tid]
        if request.method == "GET":
            return httpx.Response(200, json=ticket)
        fault = self.faults.get(("POST", tid), [])
        kind = fault.pop(0) if fault else None
        if kind == "503":
            return httpx.Response(503, json={"error": "service_unavailable"})
        if kind == "422":
            return httpx.Response(422, json={"error": "validation_failed", "problems": ["x"]})
        if kind == "crash_before_save":
            raise Crash()
        if ticket["handled"]:
            return httpx.Response(409, json={"error": "already_handled"})
        ticket["handled"] = {"action": action, **json.loads(request.content)}
        self.posts.append((tid, action))
        if kind == "crash_after_save":
            raise Crash()
        return httpx.Response(200, json={"ok": True})

    def client(self) -> Helpdesk:
        settings = Settings(base_url="https://helpdesk.test", api_key="k", email="e@test")
        return Helpdesk(
            settings, Retry(), transport=httpx.MockTransport(self.handler), sleep=lambda s: None
        )
