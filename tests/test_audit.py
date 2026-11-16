"""Log lines and scrubbing: no ticket body, no payment details, one line per ticket."""

from pathlib import Path

from tests.fake_helpdesk import FakeServer
from ticket_agent.audit import RunLog, read_log
from ticket_agent.cli import main
from ticket_agent.journal import Journal
from ticket_agent.models import Ticket, luhn_ok, scrub
from ticket_agent.pipeline import Result, Runner, fallback_handoff


def luhn_card() -> str:
    """Built at runtime so no card-shaped literal sits in the repo."""
    body = "5" + "1" * 14
    return next(body + d for d in "0123456789" if luhn_ok(body + d))


def iban() -> str:
    return "NL" + "02" + "RABO" + "0123" + "4567" + "89"


def test_scrub_masks_cards_ibans_and_emails() -> None:
    text = f"card {luhn_card()} iban {iban()} mail a.b@example.com"
    assert scrub(text) == "card [card] iban [iban] mail [email]"


def test_ordinary_numbers_survive_scrubbing() -> None:
    assert scrub("60 minuten, iOS 16, factuur 2026-10") == "60 minuten, iOS 16, factuur 2026-10"


def test_log_line_has_no_body_and_no_payment_details(tmp_path: Path) -> None:
    server = FakeServer(["T1"])
    server.tickets["T1"]["body"] = f"secret body text {luhn_card()}"
    leak = f"Customer pasted {luhn_card()} and {iban()}."

    def decide(ticket: Ticket) -> Result:
        body = {
            "language": "nl",
            "category": "billing",
            "priority": "P2",
            "reason": leak,
            "handoff_note": "n",
        }
        return Result("handoff", body, record={"unsupported_claims": [leak]})

    Runner(server.client(), decide, Journal(tmp_path / "j.jsonl"), RunLog("r", tmp_path), "r").run()
    raw = (tmp_path / "r.jsonl").read_text(encoding="utf-8")
    assert "secret body text" not in raw and luhn_card() not in raw and iban() not in raw
    assert len(read_log(tmp_path / "r.jsonl")) == 1


def test_fallback_note_scrubs_whatever_the_api_echoed() -> None:
    note = fallback_handoff({}, [f"bad value {iban()}"])["handoff_note"]
    assert iban() not in note and "[iban]" in note


def test_explain_prints_the_logged_decision(tmp_path: Path, monkeypatch, capsys) -> None:
    log = tmp_path / "run1.jsonl"
    log.write_text('{"ticket_id": "T7", "action": "reply", "gate": null}\n', encoding="utf-8")
    monkeypatch.setattr("ticket_agent.cli.LOGS_DIR", tmp_path)
    main(["explain", "T7", "--run", "run1"])
    out = capsys.readouterr().out
    assert "- action: reply" in out and "gate" not in out
