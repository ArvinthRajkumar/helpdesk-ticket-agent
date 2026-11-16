"""Policy gates: what the model may not do, whatever it proposes."""

import pytest

from ticket_agent.models import ModelDecision, Ticket
from ticket_agent.policy import finalize, load_config

CFG = load_config()
FAQ_IDS = {f"F{i:02d}" for i in range(1, 13)}
PLAIN = Ticket(id="T1", subject="Vraag", body="Hoe voeg ik iemand toe?")


def md(**kw) -> ModelDecision:
    base = {
        "language": "nl",
        "category": "question",
        "sensitive": [],
        "injection_suspected": False,
        "priority": "P4",
        "faq_ids": ["F02"],
        "action": "reply",
        "confidence": "high",
        "reason": "How-to question answered by F02.",
        "draft_reply": "Hallo, ga naar Team → Medewerker toevoegen. Roosterly Support",
        "handoff_note": "",
    }
    return ModelDecision.model_validate({**base, **kw})


def run(decision: ModelDecision, ticket: Ticket = PLAIN):
    return finalize(decision, ticket, CFG, FAQ_IDS)


def test_clean_confident_reply_stands() -> None:
    r = run(md())
    assert r.action == "reply" and r.record["gate"] is None


@pytest.mark.parametrize(
    "flag",
    [
        "refund_or_dispute",
        "cancellation_or_contract",
        "personal_data_request",
        "security_incident",
        "legal_threat",
        "churn_threat",
    ],
)
def test_every_client_rule_flag_forces_handoff(flag: str) -> None:
    r = run(md(sensitive=[flag]))
    assert r.action == "handoff" and r.record["gate"] == f"client_rule_{flag}"
    assert "draft_reply" not in r.body and r.body["handoff_note"]


@pytest.mark.parametrize(
    "text,flag",
    [
        ("Ik wil mijn geld terug voor vorige maand", "refund_or_dispute"),
        ("Wij willen ons abonnement opzeggen", "cancellation_or_contract"),
        ("Verwijder al mijn gegevens volgens de AVG", "personal_data_request"),
        ("Ik denk dat mijn account gehackt is", "security_incident"),
        ("Onze advocaat neemt contact op", "legal_threat"),
        ("We overwegen over te stappen naar een concurrent", "churn_threat"),
        ("Please refund the last invoice", "refund_or_dispute"),
    ],
)
def test_keyword_backstop_catches_what_the_model_missed(text: str, flag: str) -> None:
    r = run(md(), Ticket(id="T2", subject="x", body=text))
    assert r.action == "handoff" and flag in r.record["keyword_hits"]


def test_keywords_leave_plain_how_to_questions_alone() -> None:
    for text in (
        "Hoe pas ik de contracturen van een medewerker aan?",
        "Can I switch to the Plus plan?",
        "Waar download ik de verwerkersovereenkomst onder Juridisch?",
    ):
        assert CFG.keyword_hits(text) == [], text


def test_injection_blocks_reply_and_close() -> None:
    assert run(md(injection_suspected=True)).record["gate"] == "injection_suspected"
    spam = md(action="close", category="other", injection_suspected=True)
    assert run(spam).action == "handoff"


def test_other_language_is_handed_off_and_posted_as_en() -> None:
    french = Ticket(id="T5", subject="Bonjour", body="Comment ajouter un employé au planning ?")
    r = run(md(language="other"), french)
    assert r.action == "handoff" and r.body["language"] == "en"
    assert "other than Dutch" in r.body["handoff_note"]


def test_close_only_for_confident_other_without_faq() -> None:
    assert run(md(action="close", category="other", faq_ids=[])).action == "close"
    assert run(md(action="close", category="other")).record["gate"] == "close_cites_faq"
    assert run(md(action="close", category="bug")).action == "handoff"
    assert run(md(action="close", category="other", confidence="medium")).action == "handoff"


def test_reply_citing_an_unknown_entry_is_held() -> None:
    assert run(md(faq_ids=["F99"])).record["gate"] == "reply_cites_unknown_faq_F99"


def test_model_handoff_is_always_allowed() -> None:
    r = run(md(action="handoff", handoff_note="Needs a human."))
    assert r.action == "handoff" and r.record["gate"] is None
    assert r.body["handoff_note"] == "Needs a human."


def test_ticket_language_overrules_a_model_that_misread_it() -> None:
    english = Ticket(id="T3", subject="Question", body="How can I do this for my team, please?")
    r = run(md(language="nl"), english)
    assert r.action == "handoff" and r.record["gate"] == "language_disagrees_with_ticket"
    assert r.body["language"] == "en" and r.record["model_language"] == "nl"


def test_detector_silent_means_the_model_language_stands() -> None:
    r = run(md(language="de"), Ticket(id="T4", subject="", body="ok"))
    assert r.body["language"] == "de" and r.record["detected_language"] is None
