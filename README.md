# ticket-agent

A support ticket triage agent built for the Nordavo take-home. It reads tickets from a CSV,
classifies each one, assigns a priority with a reason, drafts a reply when a question is covered
by the knowledge base, and sends everything it is unsure about to a human. It never sends
anything. Every decision is logged with the rule that made it.

## Approach in 5 lines

1. One structured JSON call per ticket (Claude Haiku 5.5 by default) returns category, priority,
   confidence, flags for injection, language and multiple intents, and a summary.
2. Deterministic code in `policy.py` turns that into a route. The model describes, the code decides.
3. Confident questions get a second call that drafts a reply from `data/kb.md` only. Code checks
   the draft cites real entries, uses no numbers outside them, and contains no URL or promise.
4. `pipeline.py` runs those steps per ticket and appends every step to a JSONL audit log with
   tokens, cost, latency and the rule. `results.csv` and `summary.md` are rebuilt from that log.
   `--resume` skips finished tickets.
5. A 10-ticket hand-labelled eval reports accuracy, escalation recall and the false-auto rate.

## Key choices, and what I rejected

- **No framework.** Thirty tickets through a two-step linear pipeline is a for loop. LangChain or
  LangGraph would add abstractions I would have to explain on a call and would hide the one thing
  that matters here: what goes into the prompt and what comes out. The whole agent is about 800
  lines of Python plus the Anthropic SDK and pydantic.
- **Routing in code, not in the prompt.** The model is asked for facts about the ticket. The
  thresholds (which confidence is enough, which priorities need a human, which languages are
  allowed) live in `config/policy.yaml`. Changing a threshold is a one-line config edit with a test,
  not a prompt rewrite with unknown side effects.
- **Structured outputs instead of a forced tool call.** The brief asked for a forced tool call.
  Claude Sonnet 5.5, which I needed for the model comparison, returns a 400 on forced
  `tool_choice`. So I force the JSON through `output_config.format` with the same enum schema
  and validate with pydantic. Same guarantee, works on both models.
- **No temperature.** Haiku 5.5 and Sonnet 5.5 reject non-default sampling values. Determinism
  comes from the schema, the enums and the deterministic policy, not from temperature.
- **Effort low.** Thinking is adaptive and always on for these models. Effort is the cost lever.
  Low is enough for one-ticket classification and is set in config so it is easy to raise.
- **Grounding checked by code.** The model says which KB entries it used. Code verifies the ids
  exist, every number in the draft appears in those entries, and there is no URL, email address
  or promise word. If any check fails the ticket goes to a human with the reason.
- **Rejected: a vector store for the KB.** Ten entries fit in the system prompt and form a stable
  cacheable prefix. Retrieval would be the right move at a few hundred entries.
- **Rejected: the Batch API for this run.** It halves the price but adds asynchrony. For 200
  tickets a week it is the obvious next lever and it changes nothing in the pipeline.

## Assumptions

- `data/tickets.csv` is a synthetic stand-in I generated because the real CSV was not attached
  to the email. The loader reads the header and only needs an id, subject and body column, with
  common fallbacks, so a real export can be dropped in. Eval labels are tied to ticket ids, so a
  new CSV means new labels.
- `data/kb.md` is an illustrative assumption. Ten invented FAQ entries, clearly labelled. I do not
  have the client's docs.
- The priority rubric in `prompts/triage.md` is mine. Urgent means outage, data loss, security
  issue, legal threat, money taken after cancellation or repeatedly. The client would tune it.
- Non-English tickets go to a human. The team's languages are a config value.

## Results

PENDING. No API key was available while this was built, so no live run has happened yet. Every
number below will be copied from files under `runs/` and `evals/` once the commands in
"How to run" have been executed. Nothing here is estimated.

| metric | Haiku 5.5 | Sonnet 5.5 | source |
|---|---|---|---|
| category accuracy (n=10) | pending | pending | evals/report.md |
| priority exact / within one | pending | pending | evals/report.md |
| escalation precision / recall | pending | pending | evals/report.md |
| false-auto count (headline safety number) | pending | pending | evals/report.md |
| automation rate over 30 tickets | pending | not run | runs/<run_id>/summary.md |
| mean cost per ticket | pending | pending | runs/<run_id>/summary.md |
| cost per 100 tickets | pending | pending | derived from the line above |
| cost per week at 200 tickets | pending | pending | derived from the line above |

n is 10. One ticket moves every rate by 10 points. These numbers tell me whether the pipeline is
sane, not how accurate it is. A real estimate needs a few hundred labelled tickets and confidence
intervals.

The eval labels were drafted by Claude Code from ticket text before any agent run, and reviewed
by me. The labeller and the agent share a model family, so their errors may correlate. The eval
set was also used to decide tuning changes, so after the first run it is no longer a clean holdout.
`evals/report.md` keeps every run side by side.

Prices in `config/pricing.yaml` were fetched from the Anthropic pricing page on 2026-11-15.
Costs are computed from the token counts the API returns, including cache reads and writes.
Prompt caching is enabled on the system prompt; whether it actually fires depends on the prefix
clearing the 512-token minimum, and the usage fields in the audit log show the truth.

## Failure modes and security

- **Untrusted input.** Ticket text is wrapped in `<ticket>` tags, a closing tag inside the text is
  defused, and the prompt says the content is data. The model reports `injection_suspected`, and
  the policy sends any such ticket to a human regardless of what else the model said. TCK-1012 in
  the sample data is a direct test of this.
- **Drafts only.** The agent has no tools. It cannot send, refund, delete or call anything. Its
  only output is files under `runs/`. Every draft starts with a DRAFT mark added by code.
- **Secrets.** The key comes from the environment and is never read by my code. `.env` is
  git-ignored, denied to Claude Code by `settings.json`, and a pre-commit hook blocks any commit
  whose staged diff contains an env file or a key pattern.
- **Failure halfway.** Each audit record is appended and the file closed per write, so a crash
  leaves a valid partial log. `--resume <run_id>` skips tickets that have a decision record.
  API errors after the SDK's retries, schema failures after one retry, and refusals all become
  `human_review` with the error in the log. No ticket is ever dropped.
- **PII.** The audit log and results carry the ticket id, a hash of the text and the model's
  one-sentence summary. Not the customer email, not the body. Drafts may restate the question.
- **Cost.** `max_tokens` is capped per step, retries are capped at one, the SDK retries 429 and
  5xx twice, and every token is priced in the log. A run stops after three consecutive triage
  failures, so a wrong model id or key does not pay for 30 identical errors. A run of 30 tickets
  costs cents.

## Harness

- `CLAUDE.md`: what the project is, commands, architecture in five lines, guardrails, definition
  of done. Under 50 lines so it is actually read.
- `.claude/settings.json`: allows `uv run` and read-only git plus add and commit. Denies reading
  `.env`, `git push`, `git remote add`, recursive deletes. A PostToolUse hook runs ruff format
  and ruff check on every edit (it fired on every file in this repo while building). A PreToolUse
  hook runs `scripts/guard_commit.py` before any `git commit`; it exits 2 when staged content
  includes an env file or a key pattern. Both hooks were tested by piping sample hook JSON.
- `.claude/commands`: `/run`, `/eval` (runs, diffs against the previous report, summarises
  misses), `/review` (runs the reviewer subagent).
- `.claude/agents/reviewer.md`: a read-only reviewer that checks secrets, injection handling,
  cost blowups, failure halfway, PII in logs and code I could not explain. I ran it on the full
  diff before the docs commits. It found no secrets and confirmed the injection path, but it
  caught 14 real things, two of them medium: the model's free-text fields went into the log
  unscrubbed, so one prompt line was all that kept a customer email out of `audit.jsonl`; and
  a bad model id would have produced a "successful" run of 30 human_review rows because every
  exception was swallowed as `api_error`. It also caught a truncated last log line breaking
  resume, a promise-word check that rejected a faithful quote from the KB, header digits
  counting as grounded numbers, and a circular import between `cli.py` and `evaluate.py`.
  Each theme is its own `fix:` commit in the history.
- `.cursor/rules/project.mdc`: the same rules in 15 lines, pointing at CLAUDE.md.
- MCP log inspector: not built. It was the stretch goal gated on a verified live run.

## What I would do with more time

1. Real KB with retrieval. The client's help centre, chunked and retrieved per ticket, with the
   same grounding check on cited chunks.
2. A larger eval with confidence intervals. A few hundred tickets labelled by the support team,
   bootstrap intervals, and a held-out set that is never used for tuning.
3. Confidence calibration. Compare the model's confidence to its measured accuracy per bucket
   and set the policy thresholds from data instead of judgement.
4. Batch API. Same pipeline, half the price, fine for a weekly backlog.
5. A feedback loop from human corrections. Agents edit the category, priority or draft; the
   diffs become new labels and the eval grows every week.

## How to run

```powershell
uv sync
$env:ANTHROPIC_API_KEY = "..."          # bash: export ANTHROPIC_API_KEY=...
uv run ticket-agent run                 # then: uv run ticket-agent eval
```

Other commands: `uv run ticket-agent run --limit 1` for a cheap smoke test,
`uv run ticket-agent run --resume <run_id>` to continue a crashed run,
`uv run ticket-agent eval --model claude-sonnet-5-5` for the comparison column,
`uv run ticket-agent explain TCK-1012` for the full decision trace of one ticket,
`uv run pytest -q` and `uv run ruff check .` for the checks.

## Deviations from the brief, stated plainly

- Structured outputs instead of a forced tool call, and no temperature. Both because the target
  models reject the original approach. Explained above.
- The source is about 850 lines of code without comments and blanks, against a target of about
  600. The eval report rendering is the largest part. I preferred a readable report to a shorter
  file, and I would cut it first if asked.
- Two reviewer findings in `.claude/settings.json` are still open, because Claude Code would not
  let the agent edit its own permission file: the deny rule `Read(./.env.*)` also blocks the
  tracked `.env.example` (so its wording could not be updated either), and the commit guard hook
  is attached to the Bash tool only, not PowerShell. Both are one-line edits I make by hand.
