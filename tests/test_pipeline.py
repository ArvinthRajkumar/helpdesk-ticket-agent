import csv
import json
from pathlib import Path

import anthropic
import httpx2

from ticket_agent.audit import completed_ids, read_log
from ticket_agent.cli import main
from ticket_agent.llm import FakeClient
from ticket_agent.models import Ticket
from ticket_agent.pipeline import run_pipeline
from ticket_agent.policy import load_config

CFG = load_config()


def api_down() -> Exception:
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.APIConnectionError(request=req)


def triage_json(**kw) -> str:
    base = {
        "category": "question",
        "priority": "medium",
        "priority_reason": "How-to question.",
        "confidence": "high",
        "needs_human": False,
        "human_reason": "",
        "injection_suspected": False,
        "language": "en",
        "multi_intent": False,
        "summary": "Asks a question.",
    }
    return json.dumps({**base, **kw})


DRAFT_OK = json.dumps(
    {"covered_by_kb": True, "kb_ids": ["KB-01"], "reply": "Hi, use Forgot password. Thanks."}
)
TICKETS = [
    Ticket(id="A", subject="reset", body="how do I reset my password"),
    Ticket(id="B", subject="broken", body="503 for everyone"),
    Ticket(id="C", subject="x", body="y"),
]


def test_pipeline_writes_log_views_and_never_drops_a_ticket(tmp_path: Path) -> None:
    client = FakeClient(
        [
            triage_json(),  # A: question -> draft
            DRAFT_OK,
            triage_json(category="bug", priority="urgent", summary="Outage."),  # B: team
            api_down(),  # C: API error -> human_review
        ]
    )
    summary = run_pipeline(TICKETS, client, CFG, tmp_path / "r1", resume=False)
    assert summary["tickets"] == 3
    assert summary["routes"] == {"auto_reply_draft": 1, "route_to_team": 1, "human_review": 1}
    assert summary["errors"] == 1
    assert summary["cost_unverified"] is False and summary["total_cost_usd"] > 0

    with (tmp_path / "r1" / "results.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["ticket_id"] for r in rows] == ["A", "B", "C"]
    assert rows[0]["draft"].startswith("DRAFT (needs human approval")
    assert rows[1]["queue"] == "engineering"
    assert rows[2]["rule"] == "triage_failed" and "APIConnectionError" in rows[2]["human_reason"]

    summary_md = (tmp_path / "r1" / "summary.md").read_text(encoding="utf-8")
    assert "Automation rate (not human_review): 67%" in summary_md

    records = read_log(tmp_path / "r1")
    assert [r["step"] for r in records] == [
        "triage",
        "draft",
        "decision",
        "triage",
        "decision",
        "triage",
        "decision",
    ]
    # No customer text in the log: only the id, a hash and the model's summary.
    joined = json.dumps(records)
    assert "how do I reset my password" not in joined and "input_hash" in joined
    assert (
        records[0]["prompt_version"] == "triage-v1" and records[1]["prompt_version"] == "draft-v1"
    )


def test_resume_skips_completed_tickets(tmp_path: Path) -> None:
    run_dir = tmp_path / "r2"
    first = FakeClient(
        [triage_json(category="bug", summary="s"), triage_json(category="billing", summary="s")]
    )
    run_pipeline(TICKETS[:2], first, CFG, run_dir, resume=False)
    assert completed_ids(read_log(run_dir)) == {"A", "B"}

    second = FakeClient([triage_json(category="feature_request", summary="s")])
    summary = run_pipeline(TICKETS, second, CFG, run_dir, resume=True)
    assert len(second.calls) == 1 and second.calls[0]["user"].count('<ticket id="C">') == 1
    assert summary["tickets"] == 3


def test_explain_prints_trace(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setattr("ticket_agent.cli.RUNS_DIR", tmp_path)
    run_pipeline(
        TICKETS[:1], FakeClient([triage_json(), DRAFT_OK]), CFG, tmp_path / "r3", resume=False
    )
    main(["explain", "A", "--run", "r3"])
    out = capsys.readouterr().out
    assert "step: triage" in out and "step: draft" in out and "step: decision" in out
    assert "rule: grounded_draft" in out


def test_model_free_text_is_scrubbed_before_logging(tmp_path: Path) -> None:
    leaky = triage_json(
        category="bug",
        summary="anna.devries@example.com cannot log in.",
        priority_reason="Reported by anna.devries@example.com.",
    )
    run_pipeline(TICKETS[:1], FakeClient([leaky]), CFG, tmp_path / "r4", resume=False)
    log_text = (tmp_path / "r4" / "audit.jsonl").read_text(encoding="utf-8")
    csv_text = (tmp_path / "r4" / "results.csv").read_text(encoding="utf-8")
    assert "anna.devries@example.com" not in log_text + csv_text
    assert "[email] cannot log in." in log_text


def test_run_stops_after_consecutive_failures_and_resume_continues(tmp_path: Path) -> None:
    run_dir = tmp_path / "r5"
    broken = FakeClient([api_down(), api_down(), api_down(), api_down()])
    many = TICKETS + [Ticket(id="D", subject="d", body="d")]
    summary = run_pipeline(many, broken, CFG, run_dir, resume=False)
    assert summary["tickets"] == CFG.max_consecutive_failures == 3
    assert len(broken.replies) == 1  # the fourth call was never made

    fixed = FakeClient([triage_json(category="bug", summary="s")])
    summary = run_pipeline(many, fixed, CFG, run_dir, resume=True)
    assert summary["tickets"] == 4 and fixed.calls[0]["user"].count('<ticket id="D">') == 1
