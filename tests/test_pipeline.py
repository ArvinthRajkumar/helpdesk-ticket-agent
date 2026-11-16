"""Exactly-once behaviour of the run loop against a stateful fake helpdesk."""

from pathlib import Path

import pytest

from tests.fake_helpdesk import Crash, FakeServer
from ticket_agent.audit import RunLog, read_log
from ticket_agent.journal import Journal
from ticket_agent.models import Ticket
from ticket_agent.pipeline import Result, Runner

BODY = {"language": "nl", "category": "question", "priority": "P4", "reason": "r"}


def reply(ticket: Ticket) -> Result:
    return Result("reply", {**BODY, "draft_reply": "Hallo"}, record={"faq_ids": ["F01"]})


def never(ticket: Ticket) -> Result:
    raise AssertionError("the model must not be called for a journaled ticket")


def runner(server: FakeServer, tmp: Path, decide=reply, run_id="r1", **kw) -> Runner:
    return Runner(
        server.client(), decide, Journal(tmp / "journal.jsonl"), RunLog(run_id, tmp), run_id, **kw
    )


def test_every_open_ticket_gets_one_post_and_one_log_line(tmp_path: Path) -> None:
    server = FakeServer(["T1", "T2", "T3"])
    r = runner(server, tmp_path)
    r.run()
    assert sorted(server.posts) == [("T1", "reply"), ("T2", "reply"), ("T3", "reply")]
    lines = read_log(tmp_path / "r1.jsonl")
    assert [x["ticket_id"] for x in lines] == ["T1", "T2", "T3"]
    assert all(x["api"]["status"] == "posted" for x in lines)
    assert Journal(tmp_path / "journal.jsonl").pending(r.scope) == {}


def test_rerun_after_a_clean_run_posts_nothing(tmp_path: Path) -> None:
    server = FakeServer(["T1", "T2"])
    runner(server, tmp_path).run()
    runner(server, tmp_path, decide=never, run_id="r2").run()
    assert len(server.posts) == 2


def test_crash_after_save_is_settled_by_get_without_second_post(tmp_path: Path) -> None:
    server = FakeServer(["T1", "T2"])
    server.fault("POST", "T1", "crash_after_save")
    with pytest.raises(Crash):
        runner(server, tmp_path).run()
    assert server.posts == [("T1", "reply")]
    # Restart: T1 is journaled and handled, T2 was never touched.
    runner(server, tmp_path, run_id="r2").run()
    assert server.posts == [("T1", "reply"), ("T2", "reply")]
    lines = {x["ticket_id"]: x for x in read_log(tmp_path / "r2.jsonl")}
    assert lines["T1"]["api"]["status"] == "recovered_already_handled"


def test_crash_before_save_reposts_the_journaled_decision_without_the_model(
    tmp_path: Path,
) -> None:
    server = FakeServer(["T1"])
    server.fault("POST", "T1", "crash_before_save")
    with pytest.raises(Crash):
        runner(server, tmp_path).run()
    assert server.posts == []
    runner(server, tmp_path, decide=never, run_id="r2").run()
    assert server.posts == [("T1", "reply")]
    assert server.tickets["T1"]["handled"]["draft_reply"] == "Hallo"


def test_503_exhausted_leaves_the_intent_pending_for_the_next_run(tmp_path: Path) -> None:
    server = FakeServer(["T1"])
    server.fault("POST", "T1", *["503"] * 5)
    r = runner(server, tmp_path)
    r.run()
    assert server.posts == [] and "T1" in Journal(tmp_path / "journal.jsonl").pending(r.scope)
    runner(server, tmp_path, decide=never, run_id="r2").run()
    assert server.posts == [("T1", "reply")]


def test_422_falls_back_once_to_a_minimal_handoff(tmp_path: Path) -> None:
    server = FakeServer(["T1"])
    server.fault("POST", "T1", "422")
    runner(server, tmp_path).run()
    assert server.posts == [("T1", "handoff")]
    handled = server.tickets["T1"]["handled"]
    assert handled["language"] == "nl" and "handoff_note" in handled
    [line] = read_log(tmp_path / "r1.jsonl")
    assert line["action"] == "handoff" and line["api"]["status"] == "fallback_posted"


def test_409_counts_as_done_and_is_not_retried(tmp_path: Path) -> None:
    server = FakeServer(["T1"])
    server.tickets["T1"]["handled"] = None
    r = runner(server, tmp_path)
    # Someone answers between our fetch and our post.
    original = r.decide

    def answer_first(ticket: Ticket) -> Result:
        server.tickets["T1"]["handled"] = {"action": "reply"}
        return original(ticket)

    r.decide = answer_first
    r.run()
    [line] = read_log(tmp_path / "r1.jsonl")
    assert line["api"]["status"] == "already_handled" and server.posts == []


def test_dry_run_never_posts_or_journals(tmp_path: Path) -> None:
    server = FakeServer(["T1", "T2"])
    runner(server, tmp_path, dry_run=True).run(limit=1)
    assert server.posts == [] and not (tmp_path / "journal.jsonl").exists()
    assert [x["api"]["status"] for x in read_log(tmp_path / "r1.jsonl")] == ["dry_run"]


def test_three_hard_failures_in_a_row_stop_the_run(tmp_path: Path) -> None:
    server = FakeServer(["T1", "T2", "T3", "T4"])

    def fallback(ticket: Ticket) -> Result:
        return Result("handoff", {**BODY, "handoff_note": "n"}, hard_failure=True)

    runner(server, tmp_path, decide=fallback).run()
    assert len(server.posts) == 3


def test_archive_moves_the_journal_aside(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    j.intent("T1", "r1", "reply", BODY, "s")
    assert j.archive() is not None and j.pending("s") == {}


def test_torn_last_line_is_ignored(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    j.intent("T1", "r1", "reply", BODY, "s")
    with j.path.open("a", encoding="utf-8") as fh:
        fh.write('{"event": "inte')
    assert list(j.pending("s")) == ["T1"]


def test_an_intent_from_another_helpdesk_mode_never_replays(tmp_path: Path) -> None:
    j = Journal(tmp_path / "journal.jsonl")
    j.intent("T1", "r0", "reply", {**BODY, "draft_reply": "old"}, "practice@https://other/")
    server = FakeServer(["T1"])
    runner(server, tmp_path).run()
    # T1 was decided fresh in this scope, not replayed from the other one.
    assert server.tickets["T1"]["handled"]["draft_reply"] == "Hallo"
