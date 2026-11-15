from pathlib import Path

import pytest
from pydantic import ValidationError

from ticket_agent.models import Category, Ticket, TriageResult, load_tickets

DATA = Path(__file__).resolve().parents[1] / "data" / "tickets.csv"


def test_loads_the_shipped_csv() -> None:
    tickets = load_tickets(DATA)
    assert len(tickets) == 30
    assert tickets[0].id == "TCK-1001"
    assert tickets[0].subject.startswith("Cannot log in")


def test_empty_body_is_kept_not_dropped() -> None:
    tickets = {t.id: t for t in load_tickets(DATA)}
    assert tickets["TCK-1025"].body == ""


def test_alternative_column_names(tmp_path: Path) -> None:
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("ID,Title,Description\n7,Hello,World\n", encoding="utf-8")
    [t] = load_tickets(csv_path)
    assert (t.id, t.subject, t.body) == ("7", "Hello", "World")


def test_missing_body_column_fails_loudly(tmp_path: Path) -> None:
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("id,subject\n1,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no body column"):
        load_tickets(csv_path)


def test_duplicate_ids_fail(tmp_path: Path) -> None:
    csv_path = tmp_path / "t.csv"
    csv_path.write_text("id,subject,body\n1,a,b\n1,c,d\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_tickets(csv_path)


def test_text_hash_is_stable_and_short() -> None:
    t = Ticket(id="x", subject="s", body="b")
    assert t.text_hash == Ticket(id="y", subject="s", body="b").text_hash
    assert len(t.text_hash) == 16


VALID = {
    "category": "bug",
    "priority": "urgent",
    "priority_reason": "Outage blocks the whole team.",
    "confidence": "high",
    "needs_human": False,
    "human_reason": "",
    "injection_suspected": False,
    "language": "en",
    "multi_intent": False,
    "summary": "Login returns 503 for everyone.",
}


def test_triage_result_parses_enums() -> None:
    r = TriageResult.model_validate(VALID)
    assert r.category is Category.BUG


def test_triage_result_rejects_unknown_category_and_extra_field() -> None:
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**VALID, "category": "complaint"})
    with pytest.raises(ValidationError):
        TriageResult.model_validate({**VALID, "route": "auto_reply_draft"})
