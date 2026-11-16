"""Entry points. argparse only; the work happens in the modules."""

import argparse
import json
import sys

from dotenv import load_dotenv

from ticket_agent.helpdesk import Fatal, Helpdesk, Settings
from ticket_agent.models import ROOT


def helpdesk() -> Helpdesk:
    return Helpdesk(Settings.from_env())


def cmd_status(args: argparse.Namespace) -> None:
    print(json.dumps(helpdesk().status(), indent=2))


def main(argv: list[str] | None = None) -> None:
    # The repo-root .env is read into the process environment only. A variable already
    # exported wins, and nothing in this codebase reads, prints or logs a value from it.
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(prog="ticket-agent")
    sub = parser.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("status", help="show the helpdesk mode and open ticket count")
    st.set_defaults(func=cmd_status)
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Fatal as exc:
        sys.exit(f"Stopped: {exc}")
