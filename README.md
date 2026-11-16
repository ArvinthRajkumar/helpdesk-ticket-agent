# ticket-agent

Reads open Roosterly tickets from the helpdesk API and posts one decision each: reply, handoff or close.

## How to run

```
uv sync
cp .env.example .env              # PowerShell: Copy-Item .env.example .env, then fill it in
uv run ticket-agent run --dry-run # decide and log, post nothing
uv run ticket-agent reset --yes   # practice only: re-open all tickets
uv run ticket-agent run           # post decisions, log to logs/<run_id>.jsonl
uv run ticket-agent eval          # score GET /examples twice, never posts
uv run ticket-agent explain T001
```

## Decisions

1. **Dutch directly, checked by code.** One Gemini 3.1 Flash-Lite call reads the ticket and drafts the reply. No pre-translation: one call, nothing lost in translation. Code then checks the reply language with a word-list detector. It also checks that numbers come from cited FAQ entries, that menu steps, quotes and addresses appear verbatim in `faq.md`, and that the reply has no IBAN or card. An English back-translation goes to the log, and a judge call lists unsupported claims. Any failure means handoff.
2. **The model proposes, code decides.** `policy.py` hands off on any of these:
   - the client's rules, caught by a model flag or a Dutch and English keyword list (coverage untested)
   - suspected injection
   - a language other than nl, en or de
   - confidence below high
3. **Journal before posting**, so no customer gets two answers (see Reliability).

Built with Claude Code under `CLAUDE.md`, with a commit hook blocking keys and a read-only reviewer subagent. No temperature is sent, because Google recommends the default for Gemini 3.

**Assumptions:**
- "Unsure" means below high confidence, or any failed gate.
- Other languages are handed off and posted as `en`.
- Confidence high, medium and low maps to 0.9, 0.6 and 0.3. These are not calibrated probabilities.

## Reliability

- **503:** up to 5 attempts with jittered backoff.
- **429:** a limiter keeps requests at 4 per second, and a 429 waits `Retry-After`.
- **409:** never retried, confirmed by GET.
- **422:** one fallback handoff.
- **401:** stops the run.
- **POST timeout:** GET first, repost only if still open.
- **Crash:** decisions go to `state/journal.jsonl` before posting. A restart settles pending ones by GET, or posts them from the journal without asking the model again.
- **Security:** keys come only from env. Logs keep a hash instead of the body, and mask cards, IBANs and emails.

Practice run `logs/20261116-102739.jsonl`: one 503 was retried and all 30 tickets posted. A re-run posted nothing.

## Eval and fix

Nordavo's 10 labelled examples, two runs each (`evals/results/`):

| | baseline | language fix | + restated details |
|---|---|---|---|
| unsafe replies | 0, 0 | 0, 0 | 0, 0 |
| good replies (of 6) | 4, 5 | 2, 4 | 4, 4 |
| language | 90%, 90% | 100%, 100% | 100%, 100% |
| action | 80%, 90% | 60%, 80% | 80%, 80% |

**The fix:** an English ticket was labelled `nl` and drafted in Dutch. The language check passed it because it compared the reply with that same wrong label. Code now holds the reply when the detector reads the ticket differently. A follow-up stopped drafts from repeating customer details, which the checks had been holding.

**Cost per 100 tickets**, from the practice run at list price. 75,657 input tokens × $0.25/1M plus 6,087 output tokens × $1.50/1M is $0.028 for 30 tickets. That makes **$0.093 per 100**.

## What I would improve

**Next step:** the judge sometimes lists an omission as an unsupported claim, which holds a correct reply. I would ask it twice and hold only when both answers agree.

**Change to the brief:** the FAQ has no German menu names, although F08 says the app runs in German. Both German how-to replies in the practice run (T022, T029) were held for translated menu paths.
