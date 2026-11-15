import pytest

from ticket_agent.models import Decision, Draft, Route, TriageResult
from ticket_agent.policy import decide, decide_with_draft, load_config

CFG = load_config()


def triage(**overrides) -> TriageResult:
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
        "summary": "Asks how to reset a password.",
    }
    return TriageResult.model_validate({**base, **overrides})


def test_triage_failure_goes_to_human_with_error() -> None:
    d = decide(None, "api_error: TimeoutError", CFG)
    assert d.route is Route.HUMAN_REVIEW and d.rule == "triage_failed"
    assert "TimeoutError" in d.reason


@pytest.mark.parametrize(
    ("overrides", "rule"),
    [
        ({"injection_suspected": True}, "injection_suspected"),
        ({"language": "nl"}, "non_english"),
        ({"multi_intent": True}, "multi_intent"),
        ({"category": "other"}, "category_other"),
        ({"needs_human": True, "human_reason": "legal threat"}, "model_requested_human"),
        ({"confidence": "low"}, "low_confidence"),
        (
            {"category": "bug", "confidence": "medium", "priority": "urgent"},
            "medium_confidence_high_priority",
        ),
        ({"confidence": "medium", "priority": "medium"}, "question_not_confident"),
    ],
)
def test_human_review_rules(overrides, rule) -> None:
    d = decide(triage(**overrides), None, CFG)
    assert d.route is Route.HUMAN_REVIEW
    assert d.rule == rule
    assert d.reason


def test_injection_beats_everything_even_when_model_is_confident() -> None:
    d = decide(triage(injection_suspected=True, needs_human=False, confidence="high"), None, CFG)
    assert d.route is Route.HUMAN_REVIEW and d.rule == "injection_suspected"


def test_confident_question_is_a_draft_candidate() -> None:
    d = decide(triage(), None, CFG)
    assert d.route is Route.AUTO_REPLY_DRAFT and d.rule == "confident_question"


@pytest.mark.parametrize(
    ("category", "queue"),
    [("bug", "engineering"), ("billing", "billing"), ("feature_request", "product")],
)
def test_confident_team_tickets_get_a_queue(category, queue) -> None:
    d = decide(triage(category=category, priority="high"), None, CFG)
    assert d.route is Route.ROUTE_TO_TEAM and d.queue == queue
    assert "high" in d.reason


def test_medium_confidence_low_priority_bug_still_routes_to_team() -> None:
    d = decide(triage(category="bug", confidence="medium", priority="low"), None, CFG)
    assert d.route is Route.ROUTE_TO_TEAM


def test_draft_not_covered_by_kb_goes_to_human() -> None:
    cand = decide(triage(), None, CFG)
    d = decide_with_draft(cand, Draft(covered_by_kb=False, kb_ids=[], reply=""), None)
    assert d.route is Route.HUMAN_REVIEW and d.reason == "not covered by KB"


def test_draft_failure_goes_to_human() -> None:
    cand = decide(triage(), None, CFG)
    d = decide_with_draft(cand, None, "schema_error: reply: missing")
    assert d.route is Route.HUMAN_REVIEW and d.rule == "draft_failed"


def test_grounded_draft_is_auto_reply() -> None:
    cand = decide(triage(), None, CFG)
    d = decide_with_draft(cand, Draft(covered_by_kb=True, kb_ids=["KB-01"], reply="x"), None)
    assert d.route is Route.AUTO_REPLY_DRAFT and "KB-01" in d.reason


def test_decide_with_draft_leaves_non_candidates_alone() -> None:
    team = Decision(route=Route.ROUTE_TO_TEAM, rule="x", reason="y", queue="billing")
    assert decide_with_draft(team, None, None) is team


def test_env_overrides_model(monkeypatch) -> None:
    monkeypatch.setenv("TICKET_AGENT_TRIAGE_MODEL", "claude-sonnet-5-5")
    assert load_config().models.triage == "claude-sonnet-5-5"
    assert load_config().models.draft == CFG.models.draft
