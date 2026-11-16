"""Pydantic schemas shared by every module."""

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel

# Project root, so prompts, config and data resolve the same from any working directory.
ROOT = Path(__file__).resolve().parents[2]


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


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub(text: str) -> str:
    """Drop email addresses from model text before it reaches the log."""
    return EMAIL.sub("[email]", text)
