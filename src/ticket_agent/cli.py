"""Entry points. The helpdesk commands are added back step by step."""

import argparse

from dotenv import load_dotenv

from ticket_agent.models import ROOT


def main(argv: list[str] | None = None) -> None:
    # The repo-root .env is read into the process environment only. A variable already
    # exported wins, and nothing in this codebase reads, prints or logs a value from it.
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(prog="ticket-agent")
    parser.add_subparsers(dest="cmd")
    parser.parse_args(argv)
    parser.print_help()
