"""Deterministic gates. The model proposes an action, this file decides what is posted.

Anything the gates do not positively allow becomes a handoff. Every downgrade names the gate
that fired, so the log and `explain` show exactly why a ticket went where it did.
"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ticket_agent import lang
from ticket_agent.models import ROOT, ModelDecision, Ticket
from ticket_agent.pipeline import Result

CONFIG_PATH = ROOT / "config" / "policy.yaml"


class Config(BaseModel):
    model: str
    thinking_level: str
    thinking_level_by_model: dict[str, str] = {}
    max_tokens: int
    max_rpm: int = Field(gt=0)
    retry_attempts: int = Field(ge=1)
    max_consecutive_failures: int
    confidence_scores: dict[str, float]
    default_language: str

    def thinking_for(self, model: str) -> str:
        return self.thinking_level_by_model.get(model, self.thinking_level)


def load_config(path: Path = CONFIG_PATH) -> Config:
    cfg = Config.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    cfg.model = os.environ.get("TICKET_AGENT_MODEL", cfg.model)
    return cfg


def fallback(ticket: Ticket, error: str, cfg: Config) -> Result:
    """The model gave no usable decision. A human takes it; the language comes from code."""
    language = lang.detect(ticket.text) or cfg.default_language
    body = {
        "language": language,
        "category": "other",
        "priority": "P3",
        "reason": "The agent could not reach a decision, so a human takes this ticket.",
        "handoff_note": (
            f"Automatic fallback: the model was unavailable or returned no valid decision "
            f"({error.split(':')[0]}). Please read the ticket and handle it."
        ),
    }
    return Result("handoff", body, {"fallback": True, "gate": "model_failed"}, hard_failure=True)


def gate(md: ModelDecision) -> str | None:
    """The first rule that forbids the model's action, or None if it stands."""
    if md.action == "reply":
        if md.confidence != "high":
            return "reply_not_confident"
        if not md.faq_ids:
            return "reply_without_faq"
        if not md.draft_reply.strip():
            return "reply_empty"
    if md.action == "close" and md.confidence != "high":
        return "close_not_confident"
    return None


def finalize(md: ModelDecision, cfg: Config) -> Result:
    blocked = gate(md)
    action = "handoff" if blocked else md.action
    body = {
        "language": md.language,
        "category": md.category,
        "priority": md.priority,
        "reason": md.reason,
        "confidence": cfg.confidence_scores[md.confidence],
    }
    if action == "reply":
        body["draft_reply"] = md.draft_reply.strip()
    if action == "handoff":
        # The model may leave the note empty when it proposed a reply. The reason is English too.
        note = md.handoff_note.strip() or md.reason
        if blocked:
            note = f"Agent proposed {md.action}, held by rule {blocked}. {note}"
        body["handoff_note"] = note
    record = {
        "proposed_action": md.action,
        "gate": blocked,
        "flags": [str(f) for f in md.sensitive],
        "injection_suspected": md.injection_suspected,
        "faq_ids": md.faq_ids,
        "confidence_level": md.confidence,
        "fallback": False,
    }
    return Result(action, body, record)
