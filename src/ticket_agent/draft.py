"""Draft step: a KB-grounded reply for confident questions, plus a deterministic grounding check.

The model decides whether the KB covers the question and writes the text. Code then checks
the claim: cited ids must exist, numbers must come from the cited entries, no URLs, no
promises. Anything that fails is a plain-language problem the policy turns into human_review.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from ticket_agent.llm import Call, Client, load_prompt, structured_call
from ticket_agent.models import ROOT, Draft, Ticket
from ticket_agent.policy import Config
from ticket_agent.triage import wrap_ticket

PROMPT_PATH = ROOT / "prompts" / "draft.md"
KB_PATH = ROOT / "data" / "kb.md"
DRAFT_MARK = "DRAFT (needs human approval before sending)\n\n"
# Words a how-to reply never needs. Their presence means the model made a promise.
PROMISE_WORDS = ("refund", "guarantee", "we will fix", "roadmap", "compensat")


def load_kb(path: Path = KB_PATH) -> dict[str, str]:
    """Split kb.md on `## KB-xx` headers into id -> entry text (header included)."""
    entries: dict[str, str] = {}
    for block in re.split(r"(?m)^## ", path.read_text(encoding="utf-8"))[1:]:
        kb_id = block.split()[0]
        entries[kb_id] = "## " + block.strip()
    return entries


def check_grounding(draft: Draft, kb: dict[str, str]) -> str | None:
    """Return a problem string when the draft is not safe to show a human as 'grounded'."""
    if not draft.covered_by_kb:
        return None
    if not draft.kb_ids:
        return "draft claims KB coverage but cites no entry"
    unknown = [k for k in draft.kb_ids if k not in kb]
    if unknown:
        return f"draft cites unknown KB ids {unknown}"
    if not draft.reply.strip():
        return "draft is empty"
    text = draft.reply.lower()
    if re.search(r"https?://|www\.|@\w+\.\w", text):
        return "draft contains a URL or email address"
    # Entry bodies without the "## KB-xx Title" line, so the id digits do not count as facts.
    cited = " ".join(kb[k].split("\n", 1)[1] for k in draft.kb_ids).lower()
    hit = next((w for w in PROMISE_WORDS if w in text and w not in cited), None)
    if hit:
        return f"draft makes a promise ({hit})"
    loose = sorted(set(re.findall(r"\b\d+\b", text)) - set(re.findall(r"\b\d+\b", cited)))
    if loose:
        return f"draft uses numbers not in the cited KB entries {loose}"
    return None


@dataclass
class DraftStep:
    draft: Draft | None
    error: str | None  # API, schema or refusal trouble
    grounding_problem: str | None  # the model answered, code rejected the answer
    version: str
    calls: list[Call]

    @property
    def problem(self) -> str | None:
        return self.error or self.grounding_problem


def run_draft(
    client: Client, ticket: Ticket, cfg: Config, kb: dict[str, str] | None = None
) -> DraftStep:
    kb = kb or load_kb()
    version, rules = load_prompt(PROMPT_PATH)
    # KB goes in the system instruction: the stable part first, the ticket after it. No explicit
    # cache is created; the log's cached_tokens shows whether Google's implicit cache fired.
    system = rules + "\n\n# Knowledge base\n\n" + "\n\n".join(kb.values())
    user = (
        "Draft a reply to the question in this ticket, or mark it not covered.\n\n"
        + wrap_ticket(ticket)
    )
    outcome = structured_call(
        client,
        model=cfg.models.draft,
        system=system,
        user=user,
        schema_model=Draft,
        max_tokens=cfg.max_tokens.draft,
        thinking=cfg.thinking_for(cfg.models.draft),
    )
    draft = outcome.parsed
    grounding = check_grounding(draft, kb) if draft else None
    return DraftStep(draft, outcome.error, grounding, version, outcome.calls)


def present(draft: Draft | None) -> str:
    """The text a human sees in results.csv. The DRAFT mark is added by code, not the model."""
    if draft is None or not draft.covered_by_kb or not draft.reply.strip():
        return ""
    return DRAFT_MARK + draft.reply.strip()
