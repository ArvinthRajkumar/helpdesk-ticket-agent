"""Pydantic schemas and the CSV loader. Everything else imports from here."""

import csv
import hashlib
import re
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

# Project root, so prompts and config resolve the same from any working directory.
ROOT = Path(__file__).resolve().parents[2]


class Category(StrEnum):
    BUG = "bug"
    BILLING = "billing"
    FEATURE_REQUEST = "feature_request"
    QUESTION = "question"
    OTHER = "other"


class Priority(StrEnum):
    URGENT = "urgent"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Route(StrEnum):
    AUTO_REPLY_DRAFT = "auto_reply_draft"
    ROUTE_TO_TEAM = "route_to_team"
    HUMAN_REVIEW = "human_review"


class Ticket(BaseModel):
    id: str
    subject: str
    body: str

    @property
    def text(self) -> str:
        return f"Subject: {self.subject}\n\n{self.body}".strip()

    @property
    def text_hash(self) -> str:
        # Logged instead of the raw text so the audit log carries no customer content.
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:16]


class TriageResult(BaseModel):
    """What the model returns. extra=forbid so a drifting schema fails loudly."""

    model_config = ConfigDict(extra="forbid")

    category: Category
    priority: Priority
    priority_reason: str = Field(min_length=1, max_length=300)
    confidence: Confidence
    needs_human: bool
    human_reason: str = Field(max_length=300)
    injection_suspected: bool
    language: str = Field(min_length=2, max_length=16, description="ISO 639-1 code, e.g. en")
    multi_intent: bool
    summary: str = Field(min_length=1, max_length=200)


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    covered_by_kb: bool
    kb_ids: list[str]
    reply: str = Field(max_length=1500)


class Decision(BaseModel):
    route: Route
    rule: str
    reason: str
    queue: str | None = None


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# Ids end up inside a tag attribute in the prompt and in file names, so keep them boring.
ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


def scrub(text: str) -> str:
    """Drop email addresses from model text before it reaches the log or results."""
    return EMAIL.sub("[email]", text)


# Column names we accept, in order of preference. Lets a real export drop in unchanged.
ID_COLUMNS = ("ticket_id", "id", "ticket", "number")
SUBJECT_COLUMNS = ("subject", "title", "summary")
BODY_COLUMNS = ("body", "description", "message", "text", "content")


def _pick(header: list[str], candidates: tuple[str, ...], what: str) -> str:
    lowered = {h.lower().strip(): h for h in header}
    for name in candidates:
        if name in lowered:
            return lowered[name]
    raise ValueError(f"CSV has no {what} column. Looked for {candidates}, found {header}")


def load_tickets(path: Path) -> list[Ticket]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        id_col = _pick(header, ID_COLUMNS, "id")
        subj_col = _pick(header, SUBJECT_COLUMNS, "subject")
        body_col = _pick(header, BODY_COLUMNS, "body")
        tickets = []
        for n, row in enumerate(reader, start=2):
            ticket_id = (row.get(id_col) or "").strip() or f"ROW-{n}"
            if not ID_PATTERN.match(ticket_id):
                raise ValueError(f"Row {n}: ticket id {ticket_id!r} has characters we do not allow")
            tickets.append(
                Ticket(
                    id=ticket_id,
                    subject=(row.get(subj_col) or "").strip(),
                    body=(row.get(body_col) or "").strip(),
                )
            )
    if len({t.id for t in tickets}) != len(tickets):
        raise ValueError("Duplicate ticket ids in CSV. Resume and eval key on the id.")
    return tickets
