"""Deterministic gates. The model proposes an action, this file decides what is posted.

Anything the gates do not positively allow becomes a handoff. Every downgrade names the gate
that fired, so the log and `explain` show exactly why a ticket went where it did.
"""

import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from ticket_agent import lang
from ticket_agent.models import LANGUAGES, ROOT, Flag, ModelDecision, Ticket
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
    sensitive_keywords: dict[Flag, list[str]]

    def thinking_for(self, model: str) -> str:
        return self.thinking_level_by_model.get(model, self.thinking_level)

    def keyword_hits(self, text: str) -> list[str]:
        """Flags whose backstop pattern matches. The model's own flags are checked separately."""
        return [
            str(flag)
            for flag, patterns in self.sensitive_keywords.items()
            if any(re.search(p, text, re.IGNORECASE) for p in patterns)
        ]


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
    record = {
        "fallback": True,
        "gate": "model_failed",
        "keyword_hits": cfg.keyword_hits(ticket.text),
    }
    return Result("handoff", body, record, hard_failure=True)


def gate(
    md: ModelDecision, flags: list[str], faq_ids: set[str], check_failure: str | None = None
) -> str | None:
    """The first rule that forbids the model's action, or None if it stands. Handoff is
    always allowed. Order: client rules, injection, language, then per-action checks."""
    if md.action == "handoff":
        return None
    if flags:
        return f"client_rule_{flags[0]}"
    if md.injection_suspected:
        return "injection_suspected"
    if md.language not in LANGUAGES:
        return "language_not_supported"
    if md.confidence != "high":
        return f"{md.action}_not_confident"
    if md.action == "close":
        # Close is for spam and empty tickets only. Anything with a real category is answered.
        return None if md.category == "other" else "close_needs_category_other"
    if not md.faq_ids:
        return "reply_without_faq"
    unknown = [i for i in md.faq_ids if i not in faq_ids]
    if unknown:
        return f"reply_cites_unknown_faq_{unknown[0]}"
    if not md.draft_reply.strip():
        return "reply_empty"
    # Last: the grounding and language checks in checks.py, run only on a reply that got here.
    return check_failure


def finalize(
    md: ModelDecision,
    ticket: Ticket,
    cfg: Config,
    faq_ids: set[str],
    check_failure: str | None = None,
) -> Result:
    hits = cfg.keyword_hits(ticket.text)
    flags = list(dict.fromkeys([str(f) for f in md.sensitive] + hits))
    blocked = gate(md, flags, faq_ids, check_failure)
    action = "handoff" if blocked else md.action
    # The API only accepts nl, en and de. Another language is always a handoff, posted as en
    # with the real situation in the note.
    language = md.language if md.language in LANGUAGES else "en"
    body = {
        "language": language,
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
        if md.language not in LANGUAGES:
            note = f"Customer wrote in a language other than Dutch, English or German. {note}"
        if blocked:
            note = f"Agent proposed {md.action}, held by rule {blocked}. {note}"
        body["handoff_note"] = note
    record = {
        "proposed_action": md.action,
        "gate": blocked,
        "flags": flags,
        "keyword_hits": hits,
        "injection_suspected": md.injection_suspected,
        "faq_ids": md.faq_ids,
        "confidence_level": md.confidence,
        "fallback": False,
    }
    return Result(action, body, record)
