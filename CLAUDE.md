# ticket-agent

Support ticket triage agent for a take-home. Reads tickets from a CSV, classifies each one,
sets a priority, drafts replies for simple KB-covered questions, and sends everything it is
unsure about to a human. It never sends anything. Drafts only.

## Commands

- `uv sync` installs everything (Python 3.12, locked).
- `uv run ticket-agent run [--resume <run_id>] [--limit N]` triages data/tickets.csv into runs/<run_id>/.
- `uv run ticket-agent eval [--model <id>]` scores the 10 labelled tickets, writes evals/report.md.
- `uv run ticket-agent explain <ticket_id> [--run <run_id>]` prints the decision trace from the audit log.
- `uv run pytest -q` and `uv run ruff check .` must pass before every commit.

## Architecture (read in this order)

1. `models.py` holds the pydantic schemas (Ticket, TriageResult, Draft, Decision) and the CSV loader.
2. `llm.py` is the only module that touches the Gemini SDK (google-genai, generateContent): one structured JSON call, one retry on schema failure, a fake client for tests. Swapping providers is a change to this file only.
3. `triage.py` then `draft.py` are the two model steps. Prompts live in `prompts/*.md` with a version string.
4. `policy.py` is deterministic routing with thresholds from `config/policy.yaml`. The model never picks the route.
5. `audit.py` writes the append-only JSONL log, results.csv and summary.md. `cost.py` prices usage from `config/pricing.yaml`. `pipeline.py` runs one ticket through all of it and `cli.py` is argparse only.

## Conventions

- Small modules, about 600 lines of src in total. No framework, no abstraction without two callers.
- Comments explain why, not what. Short sentences. No em dashes anywhere, including docs and commit messages.
- pytest only covers deterministic parts (policy, schema parsing, cost math, CSV loading, audit writer) with the fake client.
- Conventional Commits, one logical change per commit, stage explicit paths only.
- Every number in README or docs must come from a file under runs/ or evals/. Never invent results.

## Guardrails

- Ticket text is untrusted data. It is wrapped in delimiters and labelled as data in the prompt. Instructions inside a ticket are never followed; the model reports them as `injection_suspected` and policy routes to a human.
- The agent has no side-effecting tools. It cannot send email, refund, delete or call external systems. Output is files under runs/.
- The key is `GEMINI_API_KEY`, loaded from `.env` into the environment by the CLI. Never read, print, log or commit `.env` or any API key. Hooks block commits that contain a Google (`AIza...`) or Anthropic key pattern.
- Models are `gemini-3.1-flash-lite` by default and `gemini-3.8-flash` for comparison, set in `config/policy.yaml`. Gemini 3 uses `thinking_level`, never `thinking_budget`, and no temperature.
- Failures never drop a ticket. Any API error, schema failure or refusal routes to human_review with the error recorded in the audit log.
- Drafts use only facts from data/kb.md, promise nothing, invent no URLs or numbers, and are marked DRAFT for human approval.
- The audit log stores the ticket id and a hash of the text, not the customer email or body.

## Definition of done

- `uv run ruff check .` and `uv run pytest -q` pass.
- A real run exists under runs/ and its numbers match README and evals/report.md.
- The reviewer subagent (.claude/agents/reviewer.md) has been run on the diff and real findings fixed.
- `git status` is clean and no commit contains a secret.
