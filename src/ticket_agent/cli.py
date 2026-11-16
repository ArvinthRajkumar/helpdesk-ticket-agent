"""Entry points. argparse only; the work happens in the modules."""

import argparse
import json
import os
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

from ticket_agent.audit import LOGS_DIR, RunLog, latest_log, read_log
from ticket_agent.decide import Agent
from ticket_agent.evaluate import run_eval
from ticket_agent.helpdesk import Fatal, Helpdesk, Settings
from ticket_agent.journal import Journal
from ticket_agent.llm import GeminiClient
from ticket_agent.models import ROOT
from ticket_agent.pipeline import Runner
from ticket_agent.policy import load_config


def helpdesk() -> Helpdesk:
    return Helpdesk(Settings.from_env())


def agent():
    """Fail fast on a missing key. Checks presence only and never reads the value."""
    # The SDK also reads GOOGLE_API_KEY and prefers it, which would let a stale shell variable
    # silently win over .env. Accept either one, refuse both.
    names = [n for n in ("GEMINI_API_KEY", "GOOGLE_API_KEY") if os.environ.get(n)]
    if not names:
        raise Fatal("GEMINI_API_KEY is not set. Put it in .env or export it. See .env.example.")
    if len(names) == 2:
        raise Fatal("Both GEMINI_API_KEY and GOOGLE_API_KEY are set. Unset one of them.")
    cfg = load_config()
    return Agent(GeminiClient(cfg.max_rpm, cfg.retry_attempts), cfg), cfg


def cmd_run(args: argparse.Namespace) -> None:
    hd = helpdesk()
    ag, cfg = agent()
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + ("-dry" if args.dry_run else "")
    runner = Runner(
        hd,
        ag.decide,
        Journal(),
        RunLog(run_id),
        run_id,
        dry_run=args.dry_run,
        max_failures=cfg.max_consecutive_failures,
    )
    summary = runner.run(limit=args.limit)
    print(f"\nrun_id: {run_id}  log: logs/{run_id}.jsonl")
    print(json.dumps({k: v for k, v in summary.items() if k != "helpdesk_status"}, indent=2))


def cmd_eval(args: argparse.Namespace) -> None:
    if args.model:
        os.environ["TICKET_AGENT_MODEL"] = args.model
    ag, _ = agent()
    report = run_eval(helpdesk(), ag, args.runs, args.label, refresh=args.refresh)
    print(report.read_text(encoding="utf-8"))


def cmd_explain(args: argparse.Namespace) -> None:
    path = LOGS_DIR / f"{args.run}.jsonl" if args.run else latest_log()
    if path is None or not path.exists():
        sys.exit("No run log found under logs/.")
    lines = [x for x in read_log(path) if x["ticket_id"] == args.ticket_id]
    if not lines:
        sys.exit(f"{args.ticket_id} is not in {path.name}")
    for line in lines:
        print(f"# {args.ticket_id} in {path.stem}")
        for key, value in line.items():
            if value in (None, "", [], {}):
                continue
            if isinstance(value, dict | list):
                value = json.dumps(value, ensure_ascii=False)
            print(f"- {key}: {value}")


def cmd_status(args: argparse.Namespace) -> None:
    print(json.dumps(helpdesk().status(), indent=2))


def cmd_reset(args: argparse.Namespace) -> None:
    # Re-opens every practice ticket. Never called from `run`, and only with an explicit --yes.
    if not args.yes:
        sys.exit("reset re-opens all practice tickets. Re-run with --yes to confirm.")
    print(json.dumps(helpdesk().reset(), indent=2))
    # Old intents refer to answers that no longer exist. They must not replay on reopened tickets.
    archived = Journal().archive()
    if archived:
        print(f"journal archived to {archived.relative_to(ROOT)}")


def main(argv: list[str] | None = None) -> None:
    # The repo-root .env is read into the process environment only. A variable already
    # exported wins, and nothing in this codebase reads, prints or logs a value from it.
    load_dotenv(ROOT / ".env", override=False)
    # A Windows console defaults to cp1252, which cannot print the menu arrow or Dutch quotes.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="ticket-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="decide and act on every open ticket")
    run.add_argument("--dry-run", action="store_true", help="decide and log, never post")
    run.add_argument("--limit", type=int, default=None, help="only the first N open tickets")
    run.set_defaults(func=cmd_run)
    ev = sub.add_parser("eval", help="score decide() on GET /examples, never posts")
    ev.add_argument("--runs", type=int, default=2, help="repeat the set to see the spread")
    ev.add_argument("--label", default="eval", help="name for the results file")
    ev.add_argument("--model", help="override the model from config")
    ev.add_argument("--refresh", action="store_true", help="re-fetch examples into .cache/")
    ev.set_defaults(func=cmd_eval)
    ex = sub.add_parser("explain", help="print the logged decision for one ticket")
    ex.add_argument("ticket_id")
    ex.add_argument("--run", help="run id (file name in logs/ without .jsonl), default latest")
    ex.set_defaults(func=cmd_explain)
    st = sub.add_parser("status", help="show the helpdesk mode and open ticket count")
    st.set_defaults(func=cmd_status)
    rs = sub.add_parser("reset", help="re-open all practice tickets (POST /practice/reset)")
    rs.add_argument("--yes", action="store_true", help="confirm the reset")
    rs.set_defaults(func=cmd_reset)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Fatal as exc:
        sys.exit(f"Stopped: {exc}")
