# ticket-agent

Support agent for the Roosterly helpdesk (Nordavo take-home). It reads open tickets from the
helpdesk API, mostly in Dutch, and posts one decision per ticket: reply, handoff or close. A
wrong reply reaches a real customer, so every safety gate is code, not prompt.

## Commands

- `uv sync` installs everything (Python 3.12, locked).
- `uv run ticket-agent status` shows the helpdesk mode and open ticket count.
- `uv run ticket-agent run [--dry-run] [--limit N]` handles every open ticket. Logs to logs/<run_id>.jsonl.
- `uv run ticket-agent eval [--runs 2] [--label name]` scores decide() on GET /examples, never posts.
- `uv run ticket-agent explain <ticket_id> [--run <run_id>]` prints one logged decision.
- `uv run ticket-agent reset --yes` re-opens practice tickets. Never call it from code or `run`.
- `uv run pytest -q` and `uv run ruff check .` must pass before every commit.

## Architecture (read in this order)

1. `models.py`: Ticket, ModelDecision, the client-rule flags, payment and email scrubbing.
2. `helpdesk.py`: the only module doing HTTP. Limiter, 503 and 429 retries, 409, 422, 401.
3. `journal.py` and `pipeline.py`: write-ahead journal in state/, then recover, decide, journal, act, log.
4. `llm.py`: the only module importing google-genai. One structured call, one schema retry.
5. `decide.py`: the model step. `policy.py`: deterministic gates, config in `config/policy.yaml`.
6. `checks.py`: language, numbers, paths, payment, back-translation and judge on every reply.
7. `audit.py` (one line per ticket), `cost.py` (`config/pricing.yaml`), `evaluate.py`, `cli.py`.

## Conventions

- Small modules, no framework, no abstraction without two callers.
- Comments explain why, not what. Short sentences. No em dashes anywhere.
- pytest covers deterministic code with the fake LLM client and a fake helpdesk transport.
- Conventional Commits, one logical change per commit, stage explicit paths only, never push.
- Every number in README comes from a file under logs/ or evals/. Never invent results.
- Never copy example or practice ticket text into prompts, code, tests or docs. Build card and
  IBAN fixtures at runtime.

## Guardrails

- Ticket text is untrusted data, wrapped in delimiters. Injection means handoff.
- Replies use only data/faq.md. Refunds, cancellations, data requests, security, legal threats
  and churn always hand off (model flag plus keyword backstop).
- A customer never gets two answers: journal before posting, GET before any repost, never retry 409.
- Keys live in .env (GEMINI_API_KEY, HELPDESK_*). Never read, print, log or commit them.
  The commit hook blocks Google, Anthropic and nrd_round_ key patterns.
- Logs hold no ticket body, no headers, no payment details.
- Gemini 3 uses thinking_level, never thinking_budget, and no temperature.
