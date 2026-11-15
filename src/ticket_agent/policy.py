"""Deterministic routing. The model describes the ticket, this file decides what happens.

Rules are checked top to bottom and the first match wins. Every Decision names the rule
that fired so the audit log and `explain` can show exactly why a ticket went where it did.
"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel

from ticket_agent.models import (
    ROOT,
    Category,
    Confidence,
    Decision,
    Draft,
    Priority,
    Route,
    TriageResult,
)

CONFIG_PATH = ROOT / "config" / "policy.yaml"


class Models(BaseModel):
    triage: str
    draft: str


class MaxTokens(BaseModel):
    triage: int
    draft: int


class Routing(BaseModel):
    allowed_languages: list[str]
    escalate_priorities_on_medium_confidence: list[Priority]
    queues: dict[Category, str]


class Config(BaseModel):
    models: Models
    max_tokens: MaxTokens
    effort: str
    max_consecutive_failures: int
    routing: Routing


def load_config(path: Path = CONFIG_PATH) -> Config:
    cfg = Config.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    # Env wins over file so a model comparison needs no config edit.
    cfg.models.triage = os.environ.get("TICKET_AGENT_TRIAGE_MODEL", cfg.models.triage)
    cfg.models.draft = os.environ.get("TICKET_AGENT_DRAFT_MODEL", cfg.models.draft)
    return cfg


def human(rule: str, reason: str) -> Decision:
    return Decision(route=Route.HUMAN_REVIEW, rule=rule, reason=reason)


def decide(triage: TriageResult | None, error: str | None, cfg: Config) -> Decision:
    """Route from the triage result alone. AUTO_REPLY_DRAFT here means 'candidate': the
    draft step still has to succeed, see decide_with_draft."""
    r = cfg.routing
    if triage is None:
        return human("triage_failed", f"Triage did not produce a valid result: {error}")
    if triage.injection_suspected:
        return human("injection_suspected", "Ticket text tries to steer the agent.")
    if triage.language not in r.allowed_languages:
        return human("non_english", f"Ticket language is {triage.language}.")
    if triage.multi_intent:
        return human("multi_intent", "Ticket raises more than one issue and must be split.")
    if triage.category is Category.OTHER:
        return human("category_other", triage.summary)
    if triage.needs_human:
        return human("model_requested_human", triage.human_reason or "Model asked for a human.")
    if triage.confidence is Confidence.LOW:
        return human("low_confidence", "Model is not confident in its classification.")
    if (
        triage.confidence is Confidence.MEDIUM
        and triage.priority in r.escalate_priorities_on_medium_confidence
    ):
        return human(
            "medium_confidence_high_priority",
            f"Priority {triage.priority} with only medium confidence.",
        )
    if triage.category is Category.QUESTION:
        if triage.confidence is not Confidence.HIGH:
            return human("question_not_confident", "Question but confidence is not high.")
        return Decision(
            route=Route.AUTO_REPLY_DRAFT,
            rule="confident_question",
            reason="Confident question, draft from the KB if it covers it.",
        )
    queue = r.queues.get(triage.category)
    if queue:
        return Decision(
            route=Route.ROUTE_TO_TEAM,
            rule="confident_team_ticket",
            reason=f"Confident {triage.category}, {triage.priority} priority, to {queue}.",
            queue=queue,
        )
    return human("no_rule_matched", "No routing rule matched, defaulting to a human.")


def decide_with_draft(decision: Decision, draft: Draft | None, problem: str | None) -> Decision:
    """Second pass for auto reply candidates once the draft step has run."""
    if decision.route is not Route.AUTO_REPLY_DRAFT:
        return decision
    if problem:
        return human("draft_failed", problem)
    if draft is None or not draft.covered_by_kb:
        return human("not_covered_by_kb", "not covered by KB")
    return Decision(
        route=Route.AUTO_REPLY_DRAFT,
        rule="grounded_draft",
        reason=f"Draft grounded in {', '.join(draft.kb_ids)}. Needs human approval before sending.",
    )
