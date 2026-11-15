import json
from pathlib import Path

import pytest

from ticket_agent.evaluate import Label, expected_route, load_labels, score, write_report
from ticket_agent.models import Route

LABELS = {
    "A": Label(category="bug", priority="urgent", should_escalate=False),
    "B": Label(category="question", priority="medium", should_escalate=False),
    "C": Label(category="billing", priority="high", should_escalate=True),
    "D": Label(category="other", priority="low", should_escalate=True),
}


def decision(cat, pri, route, rule="r") -> dict:
    return {"output": {"category": cat, "priority": pri}, "route": route, "rule": rule}


def test_expected_route_from_labels() -> None:
    assert expected_route(LABELS["A"]) is Route.ROUTE_TO_TEAM
    assert expected_route(LABELS["B"]) is Route.AUTO_REPLY_DRAFT
    assert expected_route(LABELS["C"]) is Route.HUMAN_REVIEW
    assert expected_route(LABELS["D"]) is Route.HUMAN_REVIEW


def test_score_counts_every_metric() -> None:
    decisions = {
        "A": decision("bug", "high", "route_to_team"),  # category ok, priority off by one
        "B": decision("question", "medium", "auto_reply_draft"),  # all ok
        "C": decision("billing", "high", "auto_reply_draft"),  # false auto: should escalate
        # D: triage failed, no output, routed to human
        "D": {"output": {}, "route": "human_review", "rule": "triage_failed"},
    }
    m = score(decisions, LABELS)
    assert m["n"] == 4
    assert m["category_accuracy"] == 0.75
    assert m["priority_exact"] == 0.5
    assert m["priority_within_one"] == 0.75
    assert m["route_match"] == 0.75  # only C is routed wrong
    assert m["escalation_precision"] == 1.0  # one escalated, correctly
    assert m["escalation_recall"] == 0.5  # C missed
    assert m["false_auto_count"] == 1 and m["false_auto_rate"] == 0.5
    assert [r["miss"] for r in m["rows"]] == [True, False, True, True]


def test_score_with_no_escalation_labels_gives_none_not_crash() -> None:
    labels = {"A": LABELS["A"]}
    m = score({"A": decision("bug", "urgent", "route_to_team")}, labels)
    assert m["escalation_precision"] is None and m["false_auto_rate"] is None
    assert m["route_match"] == 1.0


def test_shipped_labels_load_and_cover_ten_tickets() -> None:
    labels = load_labels()
    assert len(labels) == 10
    assert sum(lab.should_escalate for lab in labels.values()) >= 3


def test_write_report_renders_all_result_files(tmp_path: Path) -> None:
    results = tmp_path / "results"
    results.mkdir()
    base = {
        **score({"A": decision("bug", "urgent", "route_to_team")}, {"A": LABELS["A"]}),
        "mean_cost_per_ticket_usd": 0.001,
        "triage_cost_usd": 0.001,
        "draft_cost_usd": 0.0,
        "mean_latency_ms": 900,
    }
    (results / "m1__v1.json").write_text(
        json.dumps({**base, "run_id": "r1", "model": "m1", "prompt_version": "v1"})
    )
    (results / "m2__v1.json").write_text(
        json.dumps({**base, "run_id": "r2", "model": "m2", "prompt_version": "v1"})
    )
    report = tmp_path / "report.md"
    write_report(results, report)
    text = report.read_text(encoding="utf-8")
    assert "| metric | m1 / v1 | m2 / v1 |" in text
    assert text.count("## Per ticket") == 2
    assert "n is 1, so one ticket moves every rate by\n100 points" in text


@pytest.mark.parametrize(
    ("key", "value", "shown"),
    [
        ("escalation_recall", None, "n/a"),
        ("category_accuracy", 0.5, "50%"),
        ("false_auto_count", 3, "3"),
        ("mean_cost_per_ticket_usd", 0.0012, "0.0012"),
    ],
)
def test_fmt(key, value, shown) -> None:
    from ticket_agent.evaluate import fmt

    assert fmt(key, value) == shown
