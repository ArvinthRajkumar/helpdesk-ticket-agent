# ticket-agent

Reads open Roosterly tickets from the helpdesk API and posts one decision each: reply, handoff or close.

## How to run

```
uv sync
cp .env.example .env            # then fill it in
uv run ticket-agent run --dry-run
uv run ticket-agent run         # logs to logs/<run_id>.jsonl
uv run ticket-agent eval
```

## Decisions

1. **Dutch directly, checked by code.** One Gemini 3.1 Flash-Lite call reads the ticket and drafts the reply, with no pre-translation. Code then checks the reply's language, that every number comes from a cited FAQ entry, that menu paths and addresses appear verbatim in `faq.md`, and that it holds no payment details or copied names. An English back-translation goes to the log and a judge call lists unsupported claims. Any failure means handoff.
2. **The model proposes, code decides.** `policy.py` hands off on the client's rules (model flag plus a keyword list, coverage untested), injection, other languages or confidence below high.
3. **Journal before posting**, so no customer gets two answers.

Built with Claude Code under `CLAUDE.md`, with a key-blocking commit hook and a reviewer subagent. No temperature is sent, as Google recommends for Gemini 3.

Assumptions: "unsure" means below high confidence or any failed gate. Other languages are posted as `en` with a note. Confidence maps high, medium and low to 0.9, 0.6 and 0.3, uncalibrated.

## Reliability

- **503:** up to 5 attempts with jittered backoff.
- **429:** a 4 per second limiter, then `Retry-After`.
- **409:** never retried, confirmed by GET.
- **422:** one fallback handoff.
- **401:** stops the run.
- **POST timeout:** GET first.
- **Crash:** decisions go to `state/journal.jsonl` before posting. A restart settles pending ones by GET, or posts them without asking the model again.
- **Security:** keys only from env. Logs hold a hash, not the body, and mask cards, IBANs and emails.

## Eval and fix

Nordavo's 10 labelled examples, two runs each (`evals/results/`):

| | baseline | after fix | final |
|---|---|---|---|
| unsafe replies | 0, 0 | 0, 0 | 0, 0 |
| good replies (of 6) | 4, 5 | 2, 4 | 4, 5 |
| language | 90%, 90% | 100%, 100% | 100%, 100% |

**The fix:** an English ticket was labelled `nl` and drafted in Dutch. The language check passed because it compared the reply with that same label. Code now holds the reply when a detector reads the ticket differently. Later prompt and review fixes restored the replies.

**Cost per 100 tickets**, from `logs/20261116-102739.jsonl` at list price: 75,657 input tokens × $0.25/1M plus 6,087 output tokens × $1.50/1M is $0.028 for 30 tickets, so **$0.093**.

## What I would improve

**Next:** the judge sometimes lists an omission as an unsupported claim. I would ask it twice and hold only on agreement.

**Brief change:** the FAQ gives menu names in one language only, with English meanings, although F08 says the app runs in German. German replies therefore quote the English meaning, not the menu a German user sees.
