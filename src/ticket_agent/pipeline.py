"""The per-ticket pipeline shared by `run` and `eval`: triage, decide, maybe draft, log."""

import os
import sys
from pathlib import Path

from ticket_agent.audit import AuditLog, completed_ids, read_log, write_views
from ticket_agent.cost import cost_usd, load_pricing
from ticket_agent.draft import present, run_draft
from ticket_agent.llm import Call, Client
from ticket_agent.models import Route, Ticket, TriageResult, scrub
from ticket_agent.policy import Config, decide, decide_with_draft
from ticket_agent.triage import run_triage


def require_credentials() -> None:
    """Fail before creating a run directory. Only checks presence, never reads the value."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        sys.exit("ANTHROPIC_API_KEY is not set. Export it and retry. See .env.example.")


def _usage(calls: list[Call], pricing: dict) -> dict:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    cost: float | None = 0.0
    for c in calls:
        for k in totals:
            totals[k] += getattr(c.usage, k)
        step_cost = cost_usd(c.model, c.usage, pricing)
        cost = None if cost is None or step_cost is None else cost + step_cost
    latency = sum(c.latency_ms for c in calls)
    return {**totals, "cost_usd": cost, "latency_ms": latency, "attempts": len(calls)}


def _scrubbed(triage: TriageResult | None) -> TriageResult | None:
    """The prompt asks for no names or addresses in free text. Code enforces the address part."""
    if triage is None:
        return None
    return triage.model_copy(
        update={
            "summary": scrub(triage.summary),
            "priority_reason": scrub(triage.priority_reason),
            "human_reason": scrub(triage.human_reason),
        }
    )


def process_ticket(
    ticket: Ticket, client: Client, cfg: Config, pricing: dict, log: AuditLog, run_id: str
):
    """Triage, decide, maybe draft, decide again, log every step. Never raises on model trouble."""
    base = {"run_id": run_id, "ticket_id": ticket.id, "input_hash": ticket.text_hash}
    outcome, version = run_triage(client, ticket, cfg)
    triage = _scrubbed(outcome.parsed)
    log.write(
        {
            **base,
            "step": "triage",
            "model": cfg.models.triage,
            "prompt_version": version,
            "output": triage.model_dump() if triage else None,
            "error": outcome.error,
            **_usage(outcome.calls, pricing),
        }
    )
    decision = decide(triage, outcome.error, cfg)
    draft_text = ""
    total_latency = sum(c.latency_ms for c in outcome.calls)
    if decision.route is Route.AUTO_REPLY_DRAFT:
        draft, problem, dversion, calls = run_draft(client, ticket, cfg)
        total_latency += sum(c.latency_ms for c in calls)
        log.write(
            {
                **base,
                "step": "draft",
                "model": cfg.models.draft,
                "prompt_version": dversion,
                "output": draft.model_dump() if draft else None,
                "error": problem,
                **_usage(calls, pricing),
            }
        )
        decision = decide_with_draft(decision, draft, problem)
        if decision.route is Route.AUTO_REPLY_DRAFT:
            draft_text = present(draft)
    log.write(
        {
            **base,
            "step": "decision",
            "output": triage.model_dump() if triage else {},
            "route": decision.route,
            "rule": decision.rule,
            "reason": decision.reason,
            "queue": decision.queue,
            "human_reason": decision.reason if decision.route is Route.HUMAN_REVIEW else "",
            "draft": draft_text,
            "latency_ms": total_latency,
            "error": outcome.error,
        }
    )
    return decision


def run_pipeline(
    tickets: list[Ticket], client: Client, cfg: Config, run_dir: Path, resume: bool
) -> dict:
    pricing = load_pricing()
    log = AuditLog(run_dir)
    done = completed_ids(read_log(run_dir)) if resume else set()
    failures = 0
    for ticket in tickets:
        if ticket.id in done:
            continue
        decision = process_ticket(ticket, client, cfg, pricing, log, run_dir.name)
        print(f"{ticket.id}: {decision.route} ({decision.rule})")
        # A bad model id or key fails every ticket the same way. Stop paying for it early;
        # the tickets so far are logged and --resume continues from here.
        failures = failures + 1 if decision.rule == "triage_failed" else 0
        if failures >= cfg.max_consecutive_failures:
            print(f"Stopping: {failures} tickets in a row failed triage. Fix the cause and resume.")
            break
    return write_views(run_dir, read_log(run_dir))
