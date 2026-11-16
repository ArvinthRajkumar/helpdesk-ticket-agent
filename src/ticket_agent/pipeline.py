"""One run: recover pending posts, fetch open tickets, then decide, journal, act, log.

The order matters. The journal entry is written before the post, so a crash between the
two leaves a pending intent that the next run settles with a GET instead of a new decision.
"""

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from ticket_agent.audit import RunLog
from ticket_agent.helpdesk import Helpdesk, HelpdeskError, Invalid
from ticket_agent.journal import Journal
from ticket_agent.models import CATEGORIES, LANGUAGES, PRIORITIES, Ticket, scrub, scrub_all


@dataclass
class Result:
    """What deciding produced. `body` is posted as is; `record` only goes to the log."""

    action: str
    body: dict
    record: dict = field(default_factory=dict)
    hard_failure: bool = False  # the model was unavailable and a fallback was used


Decider = Callable[[Ticket], Result]


def fallback_handoff(body: dict, problems: list) -> dict:
    """A minimal handoff the API should accept when it rejected the real decision (422)."""

    def pick(key: str, allowed: tuple, default: str) -> str:
        return body.get(key) if body.get(key) in allowed else default

    why = scrub(str(problems))
    return {
        "language": pick("language", LANGUAGES, "nl"),
        "category": pick("category", CATEGORIES, "other"),
        "priority": pick("priority", PRIORITIES, "P3"),
        "reason": "The helpdesk rejected the agent's decision, so a human takes it.",
        "handoff_note": (
            f"Automatic fallback: the helpdesk rejected the agent's decision ({why}). "
            "Please read the ticket and handle it."
        ),
    }


class Runner:
    def __init__(
        self,
        hd: Helpdesk,
        decide: Decider,
        journal: Journal,
        log: RunLog,
        run_id: str,
        dry_run: bool = False,
        max_failures: int = 3,
    ) -> None:
        self.hd, self.decide, self.journal, self.log = hd, decide, journal, log
        self.run_id, self.dry_run, self.max_failures = run_id, dry_run, max_failures
        self.statuses: Counter = Counter()
        self.actions: Counter = Counter()
        self.cost = 0.0

    def post(self, ticket_id: str, action: str, body: dict) -> tuple[str, str, dict]:
        """Journal, then post. Returns (status, action, body) as finally sent. A HelpdeskError
        other than 422 propagates and leaves the intent pending for the next run."""
        self.journal.intent(ticket_id, self.run_id, action, body)
        try:
            status = self.hd.act(ticket_id, action, body)
        except Invalid as exc:
            # One fallback, never a loop. It is journaled too, so a crash replays the fallback.
            action, body = "handoff", fallback_handoff(body, exc.problems)
            self.journal.intent(ticket_id, self.run_id, action, body)
            try:
                status = "fallback_" + self.hd.act(ticket_id, action, body)
            except Invalid:
                status = "failed_validation"
        self.journal.result(ticket_id, self.run_id, status)
        return status, action, body

    def _start_ticket(self) -> float:
        self.hd.calls, self.hd.events = 0, []
        return time.perf_counter()

    def _write(self, line: dict, started: float, status: str) -> None:
        line["api"] = {"calls": self.hd.calls, "events": self.hd.events, "status": status}
        line["latency_ms"] = int((time.perf_counter() - started) * 1000)
        self.statuses[status] += 1
        self.actions[line.get("action")] += 1
        self.cost += line.get("cost_usd") or 0.0
        self.log.write(scrub_all({"run_id": self.run_id, **line}))
        print(f"{line['ticket_id']}: {line.get('action')} ({status})")

    def recover(self) -> None:
        """Settle intents a previous run journaled but never confirmed. No model call."""
        for ticket_id, intent in self.journal.pending().items():
            started = self._start_ticket()
            action, body = intent["action"], intent["body"]
            try:
                if self.hd.ticket(ticket_id).get("handled"):
                    status = "recovered_already_handled"
                    self.journal.result(ticket_id, self.run_id, status)
                else:
                    status, action, body = self.post(ticket_id, action, body)
                    status = "recovered_" + status
            except HelpdeskError as exc:
                if exc.status != 404:
                    raise
                # Not in the current ticket set any more. Nothing to answer.
                status = "recovered_not_found"
                self.journal.result(ticket_id, self.run_id, status)
            line = {"ticket_id": ticket_id, "action": action, "replayed_from_journal": True}
            line.update({k: body.get(k) for k in ("language", "category", "priority", "reason")})
            self._write(line, started, status)

    def handle(self, ticket: Ticket) -> bool:
        """Decide and act on one ticket. Returns False on a hard failure."""
        started = self._start_ticket()
        result = self.decide(ticket)
        action, body = result.action, result.body
        ok = not result.hard_failure
        if self.dry_run:
            status = "dry_run"
        else:
            try:
                status, action, body = self.post(ticket.id, action, body)
            except HelpdeskError as exc:
                status, ok = f"post_failed: {exc}", False
        line = {"ticket_id": ticket.id, "input_hash": ticket.text_hash, "action": action}
        line.update({k: body.get(k) for k in ("language", "category", "priority", "reason")})
        line["confidence"] = body.get("confidence")
        line.update(result.record)
        self._write(line, started, status)
        return ok and status != "failed_validation"

    def run(self, limit: int | None = None) -> dict:
        status = self.hd.status()
        print(
            f"helpdesk: mode={status.get('mode')} open={status.get('open_tickets')} "
            f"handled={status.get('handled_tickets')}"
        )
        if not self.dry_run:
            self.recover()
        tickets = [Ticket.model_validate(t) for t in self.hd.open_tickets()][:limit]
        failures = 0
        for ticket in tickets:
            failures = 0 if self.handle(ticket) else failures + 1
            if failures >= self.max_failures:
                # The same cause usually fails every ticket. Stop; a re-run picks up the rest.
                print(f"Stopping: {failures} hard failures in a row. Fix the cause and re-run.")
                break
        n = sum(self.actions.values())
        return {
            "helpdesk_status": status,
            "tickets": n,
            "actions": dict(self.actions),
            "statuses": dict(self.statuses),
            "cost_usd": round(self.cost, 6),
            "cost_per_100_usd": round(self.cost / n * 100, 4) if n else 0.0,
        }
