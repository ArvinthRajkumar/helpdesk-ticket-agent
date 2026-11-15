import json

from ticket_agent.draft import DRAFT_MARK, check_grounding, load_kb, present, run_draft
from ticket_agent.llm import FakeClient
from ticket_agent.models import Draft, Ticket
from ticket_agent.policy import load_config

KB = load_kb()


def test_kb_has_ten_labelled_entries() -> None:
    assert len(KB) == 10
    assert set(KB) == {f"KB-{i:02d}" for i in range(1, 11)}
    assert KB["KB-01"].startswith("## KB-01 Reset your password")


def draft(**kw) -> Draft:
    base = {"covered_by_kb": True, "kb_ids": ["KB-01"], "reply": "Hi, use Forgot password. Thanks."}
    return Draft.model_validate({**base, **kw})


def test_grounded_draft_passes() -> None:
    assert check_grounding(draft(), KB) is None


def test_not_covered_is_not_a_grounding_problem() -> None:
    assert check_grounding(draft(covered_by_kb=False, kb_ids=[], reply=""), KB) is None


def test_unknown_kb_id_fails() -> None:
    assert "unknown KB ids" in check_grounding(draft(kb_ids=["KB-99"]), KB)


def test_no_citation_fails() -> None:
    assert "cites no entry" in check_grounding(draft(kb_ids=[]), KB)


def test_url_fails() -> None:
    assert "URL" in check_grounding(draft(reply="See https://example.com/reset"), KB)


def test_promise_fails() -> None:
    assert "promise" in check_grounding(draft(reply="We will refund you."), KB)


def test_number_not_in_cited_entry_fails() -> None:
    assert "numbers" in check_grounding(draft(reply="Wait 48 hours."), KB)


def test_number_present_in_cited_entry_passes() -> None:
    d = draft(kb_ids=["KB-07"], reply="Hi, the API allows 60 requests per minute. Thanks.")
    assert check_grounding(d, KB) is None


def test_run_draft_puts_kb_in_system_and_ticket_in_user() -> None:
    reply = {
        "covered_by_kb": True,
        "kb_ids": ["KB-02"],
        "reply": "Hi, see Settings, Billing. Thanks.",
    }
    client = FakeClient([json.dumps(reply)])
    ticket = Ticket(id="T2", subject="Invoices?", body="Where are my invoices?")
    step = run_draft(client, ticket, load_config(), KB)
    assert step.problem is None and step.draft.kb_ids == ["KB-02"]
    assert step.version == "draft-v1"
    sent = client.calls[0]
    assert "## KB-02" in sent["system"] and '<ticket id="T2">' in sent["user"]
    assert len(step.calls) == 1


def test_present_adds_draft_mark_only_for_real_drafts() -> None:
    assert present(draft()).startswith(DRAFT_MARK)
    assert present(draft(covered_by_kb=False, kb_ids=[], reply="")) == ""
    assert present(None) == ""


def test_promise_word_quoted_from_cited_entry_is_allowed() -> None:
    d = draft(kb_ids=["KB-06"], reply="Hi, instant delivery is not guaranteed. Thanks.")
    assert check_grounding(d, KB) is None


def test_header_digits_do_not_count_as_facts() -> None:
    # KB-01 has "01" in its header only; a bare 1 in the reply is not grounded by it.
    assert "numbers" in check_grounding(draft(kb_ids=["KB-01"], reply="Hi, wait 1 day."), KB)


def test_grounding_problem_is_not_an_error() -> None:
    reply = {"covered_by_kb": True, "kb_ids": ["KB-99"], "reply": "Hi."}
    ticket = Ticket(id="T", subject="s", body="b")
    step = run_draft(FakeClient([json.dumps(reply)]), ticket, load_config(), KB)
    assert step.error is None and "unknown KB ids" in step.grounding_problem
