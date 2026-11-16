"""Checks that let someone who does not read Dutch trust a Dutch reply.

Code checks first, cheap and exact:
  language   the reply's detected language equals the decision's language
  numbers    every number in the reply appears in a cited FAQ entry
  paths      every quoted menu name, every menu path step and every address appears in faq.md
  payment    no IBAN or card number
  ticket_details  no name or company copied from the ticket
Then two model calls, only when the code checks pass:
  back_translation  an English rendering, stored in the log and never sent
  judge             claims in the reply that the cited entries do not support
Any failure turns the reply into a handoff.
"""

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ticket_agent import lang
from ticket_agent.llm import Call, Client, load_prompt, structured_call
from ticket_agent.models import EMAIL, ROOT, has_payment_details

BACKTRANSLATE_PROMPT = ROOT / "prompts" / "backtranslate.md"
JUDGE_PROMPT = ROOT / "prompts" / "judge.md"

NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
ADDRESS = re.compile(
    r"(?:https?://)?\b(?:[a-z0-9-]+\.)+(?:nl|com|de|eu|io|org|net|app)\b(?:/[^\s)\"'”]*)?", re.I
)
QUOTED = re.compile(r"[\"“”„«»]([^\"“”„«»\n]{1,80})[\"“”„«»]")
# Models sometimes write a path with > or -> instead of the arrow the help centre uses.
ARROWS = re.compile(r"\s*(?:→|->|>)\s*")
# A menu name token: letters, digits and inner dots (Loket.nl), never a trailing full stop.
WORD = r"\w+(?:\.\w+)*"


class BackTranslation(BaseModel):
    english: str = Field(min_length=1, max_length=3000)


class Judgement(BaseModel):
    unsupported_claims: list[str]


def _numbers(text: str) -> set[str]:
    # 4, 4,00 and 4.00 are the same number in a reply about prices.
    out = set()
    for n in NUMBER.findall(text):
        value = float(n.replace(",", "."))
        out.add(str(int(value)) if value.is_integer() else str(value))
    return out


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# "Hoi Anouk," at the very start of a reply: one greeting word, a capitalised name, a comma.
GREETING_NAME = re.compile(r"^\s*\w+\s+([A-ZÀ-Ý][\w-]+)\s*[,!]")


def _ticket_details(reply: str, language: str, ticket_text: str, faq_text: str) -> int:
    """Names the reply should not contain. The greeting may use the name the customer wrote
    in the ticket; a greeting name the ticket does not contain is invented and counts. Any
    other capitalised word the reply took from the ticket and the FAQ lacks (a colleague, a
    company) counts too. Sentence-initial words are skipped. German capitalises every noun,
    so there only the greeting slot is checked."""
    found = 0
    greeting = GREETING_NAME.match(reply)
    if greeting and not re.search(rf"\b{re.escape(greeting.group(1))}\b", ticket_text):
        found += 1
    if language == "de":
        # A noun right after "Hallo," (Mitarbeiter, Schichten) is the ticket's topic, not a name.
        return found
    for m in re.finditer(r"\b[A-ZÀ-Ý][\w-]{2,}", reply):
        if greeting and m.start() == greeting.start(1):
            continue  # the greeting slot was judged above
        before = reply[: m.start()].rstrip(" \t")
        if not before or before[-1] in ".!?:\n":
            continue
        word = m.group(0)
        in_ticket = re.search(rf"\b{re.escape(word)}\b", ticket_text)
        if in_ticket and word not in faq_text:
            found += 1
    return found


def code_checks(
    reply: str,
    language: str,
    cited: list[str],
    faq: dict[str, str],
    faq_text: str,
    ticket_text: str = "",
):
    """Name -> None when it passes, or a short English problem when it fails."""
    sources = " ".join(faq.get(i, "") for i in cited)
    flat_faq = _flat(faq_text)
    results: dict[str, str | None] = {}

    detected = lang.detect(reply)
    results["language"] = (
        None if detected == language else f"reply detected as {detected}, decision says {language}"
    )

    loose = sorted(_numbers(reply) - _numbers(sources))
    results["numbers"] = f"numbers not in cited entries: {loose}" if loose else None

    problems = []
    if EMAIL.search(reply):
        problems.append("email address in reply")
    for addr in ADDRESS.findall(reply):
        if addr.lower().rstrip(".") not in flat_faq.lower():
            problems.append(f"address not in faq: {addr}")
    for quoted in QUOTED.findall(reply):
        if quoted.strip() not in flat_faq:
            problems.append(f"quoted text not in faq: {quoted.strip()}")
    # Each arrow joins two menu names. The pair must appear in the FAQ exactly like that.
    normalised = ARROWS.sub(" → ", _flat(reply).replace("*", "").replace('"', ""))
    # Lookahead so a chain A → B → C yields both A → B and B → C.
    for left, right in re.findall(rf"({WORD}) → (?=({WORD}))", normalised):
        if f"{left} → {right}" not in flat_faq:
            problems.append(f"menu step not in faq: {left} → {right}")
    results["paths"] = "; ".join(problems) or None

    results["payment"] = "payment details in reply" if has_payment_details(reply) else None

    # The count only, never the words: they are usually the customer's name.
    copied = _ticket_details(reply, language, ticket_text, faq_text)
    problem = f"reply repeats {copied} name(s) from the ticket"
    results["ticket_details"] = problem if copied else None
    return results


@dataclass
class CheckRun:
    results: dict[str, str | None]
    back_translation: str = ""
    unsupported_claims: list[str] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)

    @property
    def failure(self) -> str | None:
        return next((f"check_{k}" for k, v in self.results.items() if v), None)


def run_checks(
    client: Client,
    cfg,
    reply: str,
    language: str,
    cited: list[str],
    faq: dict,
    faq_text: str,
    ticket_text: str,
) -> CheckRun:
    run = CheckRun(code_checks(reply, language, cited, faq, faq_text, ticket_text))
    if run.failure:
        return run  # no point paying for model checks on a reply that is already held
    wrapped = "<reply>\n" + reply.replace("</reply", "<\\/reply") + "\n</reply>"
    common = {
        "model": cfg.model,
        "max_tokens": cfg.max_tokens,
        "thinking": cfg.thinking_for(cfg.model),
    }

    version, system = load_prompt(BACKTRANSLATE_PROMPT)
    out = structured_call(
        client, system=system, user=wrapped, schema_model=BackTranslation, **common
    )
    run.calls += out.calls
    run.versions.append(version)
    if out.parsed is None:
        run.results["back_translation"] = f"failed: {out.error}"
        return run
    run.back_translation = out.parsed.english
    run.results["back_translation"] = None

    version, rules = load_prompt(JUDGE_PROMPT)
    entries = "\n\n".join(f"## {i}\n{faq[i]}" for i in cited if i in faq)
    out = structured_call(
        client,
        system=f"{rules}\n\n# Cited help-centre entries\n\n{entries}",
        user=wrapped,
        schema_model=Judgement,
        **common,
    )
    run.calls += out.calls
    run.versions.append(version)
    if out.parsed is None:
        run.results["judge"] = f"failed: {out.error}"
        return run
    run.unsupported_claims = out.parsed.unsupported_claims
    n = len(run.unsupported_claims)
    run.results["judge"] = f"{n} unsupported claim(s)" if n else None
    return run
