"""Eval scoring with a scripted decider. Example texts here are invented for the test."""

from ticket_agent.evaluate import metrics, render, score_once
from ticket_agent.pipeline import Result


def example(i: str, action: str, language: str = "nl") -> dict:
    label = {"language": language, "category": "question", "priority": "P4", "action": action}
    return {"id": i, "subject": "s", "body": "b", "label": {**label, "reason": "r"}}


class Scripted:
    def __init__(self, actions: dict[str, str]) -> None:
        self.actions = actions

    def decide(self, ticket) -> Result:
        body = {"language": "nl", "category": "question", "priority": "P4", "reason": "r"}
        return Result(self.actions[ticket.id], body, {"gate": None, "cost_usd": 0.001})


EXAMPLES = [example("E1", "reply"), example("E2", "handoff"), example("E3", "reply", "en")]


def test_safety_counts_replies_where_gold_says_human() -> None:
    rows = score_once(EXAMPLES, Scripted({"E1": "reply", "E2": "reply", "E3": "handoff"}))
    m = metrics(rows)
    assert m["unsafe_replies"] == ["E2"]
    assert m["good_replies"] == ["E1"] and m["gold_replies"] == 2
    assert m["action_acc"] == 1 / 3 and abs(m["cost_per_100_usd"] - 0.1) < 1e-9


def test_report_has_ids_and_fields_but_no_ticket_text() -> None:
    examples = [{**EXAMPLES[1], "body": "UNIQUE-BODY-TEXT"}]
    runs = [score_once(examples, Scripted({"E2": "reply"})) for _ in range(2)]
    text = render(runs, "m", "t")
    assert "| 1 | E2 | action | handoff | reply |" in text
    assert "UNIQUE-BODY-TEXT" not in text and "spread" in text
