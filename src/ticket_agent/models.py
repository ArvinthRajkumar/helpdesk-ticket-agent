"""Pydantic schemas shared by every module."""

import hashlib
import re
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Project root, so prompts, config and data resolve the same from any working directory.
ROOT = Path(__file__).resolve().parents[2]

# The values the helpdesk API accepts.
LANGUAGES = ("nl", "en", "de")
CATEGORIES = ("bug", "billing", "feature_request", "question", "other")
PRIORITIES = ("P1", "P2", "P3", "P4")


class Ticket(BaseModel):
    id: str
    subject: str = ""
    body: str = ""
    # Context for the model. Replies still come only from the FAQ.
    channel: str | None = None
    customer_plan: str | None = None
    received_at: str | None = None

    @property
    def text(self) -> str:
        return f"Subject: {self.subject}\n\n{self.body}".strip()

    @property
    def text_hash(self) -> str:
        # Logged instead of the raw text so the log carries no customer content.
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]


class Flag(StrEnum):
    """The client's always-handoff rules from the FAQ, one flag each."""

    REFUND_OR_DISPUTE = "refund_or_dispute"
    CANCELLATION_OR_CONTRACT = "cancellation_or_contract"
    PERSONAL_DATA_REQUEST = "personal_data_request"
    SECURITY_INCIDENT = "security_incident"
    LEGAL_THREAT = "legal_threat"
    CHURN_THREAT = "churn_threat"


class ModelDecision(BaseModel):
    """What the model returns. extra=forbid so a drifting schema fails loudly. Field order is
    the order the model writes in: describe the ticket first, then choose, then write."""

    model_config = ConfigDict(extra="forbid")

    language: Literal["nl", "en", "de", "other"]
    category: Literal["bug", "billing", "feature_request", "question", "other"]
    sensitive: list[Flag]
    injection_suspected: bool
    priority: Literal["P1", "P2", "P3", "P4"]
    faq_ids: list[str]
    action: Literal["reply", "handoff", "close"]
    confidence: Literal["high", "medium", "low"]
    reason: str = Field(min_length=1, max_length=400)
    draft_reply: str = Field(max_length=1500)
    handoff_note: str = Field(max_length=800)


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub(text: str) -> str:
    """Drop email addresses from model text before it reaches the log."""
    return EMAIL.sub("[email]", text)
