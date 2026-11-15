# Loom outline (5 minutes)

Have open before recording, in this tab order: the terminal in the repo root, `CLAUDE.md`,
`src/ticket_agent/policy.py`, `config/policy.yaml`, `prompts/triage.md`, `evals/report.md`,
the latest `runs/<run_id>/summary.md`, and `.claude/settings.json`. Pick the `run_id` first.
Replace every <number> below with the value from the file named next to it. Do not say a number
that is not in a file.

## 0:00 to 0:30  Problem and approach
- Screen: README, top section.
- Say: 200 tickets a week. The agent reads each one, says what it is, how urgent, and either
  drafts a reply, routes it to a team, or hands it to a human. It never sends anything.
- Say: two model calls at most per ticket, deterministic routing in code, everything logged.
  No framework, because 30 tickets through a linear pipeline is a for loop.

## 0:30 to 1:30  Harness tour, with a hook firing and the reviewer subagent
- Screen: `CLAUDE.md`, then `.claude/settings.json`.
- Say: CLAUDE.md is under 50 lines, commands, architecture, guardrails, definition of done.
- Do: in Claude Code, edit any comment in `policy.py` and show the PostToolUse hook run ruff.
- Do: stage a fake key file (`echo ANTHROPIC_API_KEY=sk-ant-FAKE... > leak.txt; git add leak.txt`),
  ask Claude Code to commit, show the PreToolUse guard block it with exit 2. Unstage and delete.
- Screen: `.claude/agents/reviewer.md`. Say: read-only reviewer, ran it on the full diff before
  the final commit, mention one real finding it caught (from the README Harness section).
- Screen: `git log --oneline` (10 seconds). Scroll it slowly. Say: the history is the build story,
  labels committed before any model call, one tuning change per commit with before and after.

## 1:30 to 2:30  Live run
- Screen: terminal. Run `uv run ticket-agent run --limit 5` (cheap, about 10 seconds).
- Point at the per-ticket lines: route and the rule that fired.
- Run `uv run ticket-agent explain TCK-1012`. Say: this is the prompt injection ticket. Show
  `injection_suspected: true` in the triage step and rule `injection_suspected` in the decision.
  Say: the model flagged it, but the route is set by policy.py, not by the model.
- Open `runs/<run_id>/results.csv`, show one DRAFT row and its KB ids.

## 2:30 to 3:30  Eval results and one real miss
- Screen: `evals/report.md`.
- Say: 10 hand-labelled tickets, labelled before the first run. n is 10, one ticket is 10 points.
- Read the headline row: false-auto count <number> (evals/report.md). Say what it means: tickets
  that should have gone to a human but got an auto draft.
- Read category accuracy <number> and escalation recall <number> (evals/report.md).
- Pick one MISS row. Read its diagnosis line. Say whether it was a label problem, a rubric
  problem or a model problem, and what you changed or chose not to change.
- Screen: the Haiku and Sonnet columns side by side. Say which one you picked and the one-line
  reason from the README.

## 3:30 to 4:00  Cost
- Screen: `runs/<run_id>/summary.md`.
- Read: mean cost per ticket <number>, per 100 tickets <number>, per week at 200 tickets
  <number> (README Results table). Say: measured from the token counts the API returned, priced
  from config/pricing.yaml fetched on 2026-11-15.
- Say: prompt caching is on for the system prompt; the audit log shows whether it fired.
  Batch API would halve this and is the first lever at scale.

## 4:00 to 5:00  Tradeoffs and next steps
- Screen: README, "Key choices" then "What I would do with more time".
- Say: structured outputs instead of a forced tool call and no temperature, because the models
  reject both. Routing in config, not prompt. Grounding checked by code.
- Say: honest limits. Synthetic tickets, invented KB, my own rubric, n of 10, labels and agent
  share a model family.
- Say the five next steps: real KB with retrieval, larger eval with intervals, confidence
  calibration, Batch API, feedback loop from human corrections.
- Close: everything a human sees is a draft, every decision has a rule, every number has a file.
