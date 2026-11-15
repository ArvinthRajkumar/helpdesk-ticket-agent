# ticket-agent

A support ticket triage agent built for the Nordavo take-home. It reads tickets from a CSV,
classifies each one, assigns a priority with a reason, drafts a reply when a question is covered
by the knowledge base, and sends everything it is unsure about to a human. It never sends
anything. Every decision is logged with the rule that made it.

## Approach in 5 lines

1. One structured JSON call per ticket (Gemini 3.1 Flash-Lite by default) returns category,
   priority, confidence, flags for injection, language and multiple intents, and a summary.
2. Deterministic code in `policy.py` turns that into a route. The model describes, the code decides.
3. Confident questions get a second call that drafts a reply from `data/kb.md` only. Code checks
   the draft cites real entries, uses no numbers outside them, and contains no URL or promise.
4. `pipeline.py` runs those steps per ticket and appends every step to a JSONL audit log with
   tokens, cost, latency and the rule. `results.csv` and `summary.md` are rebuilt from that log.
   `--resume` skips finished tickets.
5. A 10-ticket hand-labelled eval reports accuracy, escalation recall and the false-auto rate.

## Runtime model: Gemini

- **Provider isolation.** `llm.py` is the only module that imports `google-genai`. Everything
  else sees prompt in, validated pydantic object plus usage out. The agent started on the
  Anthropic API and the switch to Gemini touched that one file plus config, which is the point.
- **Models.** `gemini-3.1-flash-lite` for triage and drafting, `gemini-3.8-flash` as the
  comparison model. Both IDs are listed as stable on Google's models page as of 2026-11-15.
  Models live in `config/policy.yaml` with env overrides.
- **Structured output.** `response_mime_type` is `application/json` and `response_json_schema`
  is the pydantic model's JSON schema. The schema is never pasted into the prompt. Google
  documents that string length constraints are stripped server side, so pydantic is the only
  validator that counts, and a validation failure gets one retry with the field names, then the
  ticket goes to a human.
- **Thinking.** Gemini 3 takes `thinking_level`, never `thinking_budget`. Triage and drafting
  run at `minimal`, configurable. `gemini-3.8-flash` rejects `minimal`, so config gives it `low`.
  Thinking tokens count toward `max_output_tokens`, so that cap is generous and a `MAX_TOKENS`
  finish is a failure, not a partial answer.
- **Temperature.** Google's current guidance marks `temperature`, `top_p` and `top_k` as
  deprecated and ignored on Gemini 3.6 and later, with a 400 promised for future generations,
  and recommends system instructions for determinism instead. I send none. Runs are therefore
  not guaranteed to be deterministic; the schema, the enums and the deterministic policy do
  the work.
- **Failures that route to a human.** A blocked prompt (`prompt_feedback.block_reason`), any
  finish reason other than `STOP`, missing text, a schema failure after one retry, and an API
  error after the SDK's retries. The injection ticket is the obvious candidate for a safety
  filter, so this path matters.
- **Rate limits.** Free-tier limits are per project and only visible in AI Studio. A
  client-side throttle (`max_rpm`, default 10) spaces requests, and the SDK retries 429 and 503
  with exponential backoff. Those retries never count toward the three-consecutive-failure stop;
  only a failure after they are exhausted does.
- **Privacy.** Google states that free-tier content is used to improve its products and
  paid-tier content is not. Fine for this synthetic data. A real client needs the paid tier.

## Key choices, and what I rejected

- **No framework.** Thirty tickets through a two-step linear pipeline is a for loop. LangChain or
  LangGraph would add abstractions I would have to explain on a call and would hide the one thing
  that matters here: what goes into the prompt and what comes out. The whole agent is about 900
  lines of Python plus the Gemini SDK and pydantic.
- **Routing in code, not in the prompt.** The model is asked for facts about the ticket. The
  thresholds (which confidence is enough, which priorities need a human, which languages are
  allowed) live in `config/policy.yaml`. Changing a threshold is a one-line config edit with a test,
  not a prompt rewrite with unknown side effects.
- **Grounding checked by code.** The model says which KB entries it used. Code verifies the ids
  exist, every number in the draft appears in those entries, and there is no URL, email address
  or promise word. If any check fails the ticket goes to a human with the reason.
- **Rejected: a vector store for the KB.** Ten entries fit in the system instruction. Retrieval
  would be the right move at a few hundred entries.
- **Rejected: the Batch API for this run.** Google prices batch at half the standard rate but it
  is asynchronous. For 200 tickets a week it is the obvious next lever and changes nothing in
  the pipeline.

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

All numbers come from `evals/report.md`, `evals/results/*.json` and `runs/sample/summary.md`.
Run date 2026-11-15. No prompt, rubric or policy tuning was done, so these are first-run numbers.

| metric (eval set, n=10) | flash-lite run 1 | flash-lite run 2 | gemini-3.8-flash |
|---|---|---|---|
| category accuracy | 90% | 90% | 90% |
| priority exact / within one | 80% / 100% | 80% / 100% | 70% / 100% |
| route match | 90% | 80% | 90% |
| escalation precision / recall | 83% / 100% | 71% / 100% | 83% / 100% |
| false-auto count (headline safety number) | 0 | 0 | 0 |
| mean cost per ticket, eval mix | $0.00044 | $0.00043 | $0.00118 |
| mean latency per ticket | 1627 ms | 1487 ms | 2856 ms |

Main run over all 30 tickets (flash-lite): 15 human_review, 12 route_to_team, 3 auto_reply_draft,
no errors. Automation rate 50%, of which 10% are auto drafts. Total cost $0.0128, mean $0.00043
per ticket. That is about $0.043 per 100 tickets and about $0.086 per week at 200 tickets.
Thinking tokens were 0 at the minimal level, so thinking added nothing to the bill.
gemini-3.8-flash costs about 2.7 times more per ticket on the eval mix: roughly $0.118 per 100
tickets at the introductory price through 2026-12-31, and about $0.236 after the price doubles
on 2027-01-01 (cost.py applies the change by date). I did not record whether the key was on the
free or paid tier; all costs are paid list prices.

**Did results move between runs? Yes.** Two identical eval runs on flash-lite gave the same
category and priority accuracy, but route match fell from 90% to 80% and escalation precision
from 83% to 71%. TCK-1001, a clear outage, went to engineering in run 1 and to a human in run 2,
because the model's `needs_human` flag flipped. No temperature is sent, so runs are not
deterministic. The safety number held: false-auto stayed 0 in all three runs.

**Model pick: gemini-3.1-flash-lite.** It matched gemini-3.8-flash on category and escalation,
scored higher on exact priority (80% vs 70%), and costs about 2.7 times less. With n=10 the
priority gap is one ticket, so the honest reading is "not worse, and cheaper".

**Real misses, all in `evals/diagnoses.yaml`.** The model over-escalates: it sets `needs_human`
on a clear data loss bug (TCK-1006) and a plain double charge (TCK-1002 on 3.8 Flash). That is
safe but lowers automation. TCK-1005 and TCK-1012 are partly my label problems: the rubric says
a refund is high and a pain-driven feature request is medium, and the labels disagree. I left the
reviewed labels as they are rather than tune to the model. For TCK-1008 ("it doesnt work") the
model said bug instead of other, but still asked for a human.

n is 10. One ticket moves every rate by 10 points, and the run to run movement above is exactly
that size. These numbers tell me whether the pipeline is sane, not how accurate it is. A real estimate needs a few hundred labelled tickets and confidence
intervals.

The eval labels were drafted by Claude Code from ticket text before any agent run, and reviewed
by me with no edits. The labeller (Claude) and the agent (Gemini) are different model families, so their
errors are less likely to correlate than when both were Claude, but the labels are still one
person's reading of ten tickets. The eval set was also used to decide tuning changes, so after
the first run it is no longer a clean holdout. `evals/report.md` keeps every run side by side.

Cost is computed from the token counts the API returns: `prompt_token_count` as input and
`candidates_token_count` plus `thoughts_token_count` as output, because Google bills thinking
at the output rate. `cached_content_token_count` is logged but priced as plain input, the
conservative choice. Prices in `config/pricing.yaml` were read from Google's pricing page on
2026-11-15 at paid-tier list rates, and cost is reported at those rates even when a run uses
the free tier; the Results section states which tier a run used. `gemini-3.8-flash` is on an
introductory price through 2026-12-31 and doubles on 2027-01-01, so its cost per 100 tickets
is reported for both dates.

## Failure modes and security

- **Untrusted input.** Ticket text is wrapped in `<ticket>` tags, a closing tag inside the text is
  defused, and the prompt says the content is data. The model reports `injection_suspected`, and
  the policy sends any such ticket to a human regardless of what else the model said. TCK-1012 in
  the sample data is a direct test of this.
- **Drafts only.** The agent has no tools. It cannot send, refund, delete or call anything. Its
  only output is files under `runs/`. Every draft starts with a DRAFT mark added by code.
- **Secrets.** `GEMINI_API_KEY` lives in `.env`, which the CLI loads into the environment with
  python-dotenv (an exported variable wins). No code reads, prints or logs the value. `.env` is
  git-ignored and denied to Claude Code by `settings.json`, and a pre-commit hook blocks any
  commit whose staged diff contains an env file or a Google or Anthropic key pattern.
- **Failure halfway.** Each audit record is appended and the file closed per write, so a crash
  leaves a valid partial log and a half-written last line is dropped on the next read.
  `--resume <run_id>` skips tickets that have a decision record. API errors after the SDK's
  retries, schema failures after one retry, blocked prompts and bad finish reasons all become
  `human_review` with the error in the log. No ticket is ever dropped.
- **PII.** The audit log and results carry the ticket id, a hash of the text and the model's
  one-sentence summary with email addresses scrubbed by code. Not the customer email, not the
  body. Drafts may restate the question.
- **Cost.** `max_output_tokens` is capped per step, schema retries are capped at one, transport
  retries at `retry_attempts`, and every token including thinking is priced in the log. A run stops after three consecutive triage failures,
  so a wrong model id or key does not pay for 30 identical errors.

## Harness

- `CLAUDE.md`: what the project is, commands, architecture in five lines, guardrails, definition
  of done. Under 50 lines so it is actually read.
- `.claude/settings.json`: allows `uv run` and read-only git plus add and commit. Denies reading
  `.env`, `git push`, `git remote add`, recursive deletes. A PostToolUse hook runs ruff format
  and ruff check on every edit (it fired on every file in this repo while building). A PreToolUse
  hook runs `scripts/guard_commit.py` before any `git commit`; it exits 2 when staged content
  includes an env file or a key pattern. Both hooks were tested by piping sample hook JSON, and
  the commit guard blocked one of my own commands during the build.
- `.claude/commands`: `/run`, `/eval` (runs, diffs against the previous report, summarises
  misses), `/review` (runs the reviewer subagent).
- `.claude/agents/reviewer.md`: a read-only reviewer that checks secrets, injection handling,
  cost blowups, failure halfway, PII in logs and code I could not explain. I ran it on the full
  diff before the docs commits. It found no secrets and confirmed the injection path, but it
  caught 14 real things, two of them medium: the model's free-text fields went into the log
  unscrubbed, so one prompt line was all that kept a customer email out of `audit.jsonl`; and
  a bad model id would have produced a "successful" run of 30 human_review rows because every
  exception was swallowed as an API error. It also caught a truncated last log line breaking
  resume, a promise-word check that rejected a faithful quote from the KB, header digits
  counting as grounded numbers, and a circular import between `cli.py` and `evaluate.py`.
  Each theme is its own `fix:` commit in the history.
- `.cursor/rules/project.mdc`: the same rules in under 20 lines, pointing at CLAUDE.md.
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
# put GEMINI_API_KEY=... in .env (see .env.example), or export it in the shell
uv run ticket-agent run                 # then: uv run ticket-agent eval
```

Other commands: `uv run ticket-agent run --limit 1` for a cheap smoke test,
`uv run ticket-agent run --resume <run_id>` to continue a crashed run,
`uv run ticket-agent eval --model gemini-3.8-flash` for the comparison column,
`uv run ticket-agent explain TCK-1012` for the full decision trace of one ticket,
`uv run pytest -q` and `uv run ruff check .` for the checks.

## Deviations from the brief, stated plainly

- The brief asked for a forced tool call at temperature 0. Gemini's structured output does the
  forcing through `response_json_schema`, and temperature is deprecated on current Gemini
  models, so I send neither. Explained above.
- The source is about 900 lines of code without comments and blanks, against a target of about
  600. The eval report rendering is the largest part. I preferred a readable report to a shorter
  file, and I would cut it first if asked.
- One reviewer finding in `.claude/settings.json` is still open because Claude Code would not let
  the agent edit its own permission file: the deny rule `Read(./.env.*)` also covers the tracked
  `.env.example`, and the commit guard hook is attached to the Bash tool only, not PowerShell.
  Both are one-line edits I make by hand. The MCP log inspector stretch goal was not built.
