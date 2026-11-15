"""Entry points: run, eval, explain. Also the per-ticket pipeline that both run and eval use."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from ticket_agent.audit import (
    RUNS_DIR,
    AuditLog,
    completed_ids,
    latest_run_dir,
    read_log,
    write_views,
)
from ticket_agent.cost import cost_usd, load_pricing
from ticket_agent.draft import present, run_draft
from ticket_agent.llm import AnthropicClient, Call, Client
from ticket_agent.models import ROOT, Route, Ticket, load_tickets
from ticket_agent.policy import Config, decide, decide_with_draft, load_config
from ticket_agent.triage import run_triage

DEFAULT_TICKETS = ROOT / "data" / "tickets.csv"


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


def process_ticket(
    ticket: Ticket, client: Client, cfg: Config, pricing: dict, log: AuditLog, run_id: str
):
    """Triage, decide, maybe draft, decide again, log every step. Never raises on model trouble."""
    base = {"run_id": run_id, "ticket_id": ticket.id, "input_hash": ticket.text_hash}
    outcome, version = run_triage(client, ticket, cfg)
    triage = outcome.parsed
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
    for ticket in tickets:
        if ticket.id in done:
            continue
        decision = process_ticket(ticket, client, cfg, pricing, log, run_dir.name)
        print(f"{ticket.id}: {decision.route} ({decision.rule})")
    return write_views(run_dir, read_log(run_dir))


def require_credentials() -> None:
    """Fail before creating a run directory. Only checks presence, never reads the value."""
    if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        sys.exit("ANTHROPIC_API_KEY is not set. Export it and retry. See .env.example.")


def cmd_run(args: argparse.Namespace) -> None:
    require_credentials()
    cfg = load_config()
    tickets = load_tickets(Path(args.tickets))[: args.limit]
    run_id = args.resume or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    summary = run_pipeline(
        tickets, AnthropicClient(), cfg, RUNS_DIR / run_id, resume=bool(args.resume)
    )
    print(f"\nrun_id: {run_id}")
    print(json.dumps(summary, indent=2))


def cmd_explain(args: argparse.Namespace) -> None:
    run_dir = RUNS_DIR / args.run if args.run else latest_run_dir()
    if run_dir is None:
        sys.exit("No runs found under runs/.")
    records = [r for r in read_log(run_dir) if r["ticket_id"] == args.ticket_id]
    if not records:
        sys.exit(f"{args.ticket_id} not found in {run_dir.name}")
    print(f"# {args.ticket_id} in run {run_dir.name}\n")
    for r in records:
        print(f"## step: {r['step']}  ({r['ts']})")
        for k, v in r.items():
            if k not in ("ts", "step", "ticket_id", "run_id") and v not in (None, "", {}):
                print(f"- {k}: {json.dumps(v) if isinstance(v, (dict, list)) else v}")
        print()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ticket-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="triage every ticket in the CSV")
    run.add_argument("--tickets", default=str(DEFAULT_TICKETS))
    run.add_argument("--resume", help="run_id to continue; completed tickets are skipped")
    run.add_argument("--limit", type=int, default=None, help="only the first N tickets")
    run.set_defaults(func=cmd_run)
    ev = sub.add_parser("eval", help="score the labelled tickets and write evals/report.md")
    ev.add_argument("--model", help="triage and draft model, overrides config and env")
    ev.add_argument("--tickets", default=str(DEFAULT_TICKETS))
    ex = sub.add_parser("explain", help="print the decision trace for one ticket")
    ex.add_argument("ticket_id")
    ex.add_argument("--run", help="run_id, default latest")
    ex.set_defaults(func=cmd_explain)
    args = parser.parse_args(argv)
    if args.cmd == "eval":
        from ticket_agent.evaluate import cmd_eval

        cmd_eval(args)
        return
    args.func(args)
