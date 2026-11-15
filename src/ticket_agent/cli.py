"""Entry points: run, eval, explain."""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from ticket_agent.audit import RUNS_DIR, latest_run_dir, read_log
from ticket_agent.evaluate import cmd_eval
from ticket_agent.llm import GeminiClient
from ticket_agent.models import ROOT, load_tickets
from ticket_agent.pipeline import require_credentials, run_pipeline
from ticket_agent.policy import load_config

DEFAULT_TICKETS = ROOT / "data" / "tickets.csv"


def cmd_run(args: argparse.Namespace) -> None:
    require_credentials()
    cfg = load_config()
    tickets = load_tickets(Path(args.tickets))[: args.limit]
    run_id = args.resume or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    if args.resume and not (RUNS_DIR / run_id / "audit.jsonl").exists():
        sys.exit(f"No audit log under runs/{run_id}. Check the run id.")
    summary = run_pipeline(
        tickets,
        GeminiClient(cfg.max_rpm, cfg.retry_attempts),
        cfg,
        RUNS_DIR / run_id,
        resume=bool(args.resume),
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
    ev.set_defaults(func=cmd_eval)
    ex = sub.add_parser("explain", help="print the decision trace for one ticket")
    ex.add_argument("ticket_id")
    ex.add_argument("--run", help="run_id, default latest")
    ex.set_defaults(func=cmd_explain)
    args = parser.parse_args(argv)
    args.func(args)
