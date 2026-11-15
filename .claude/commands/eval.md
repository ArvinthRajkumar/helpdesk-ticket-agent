---
description: Run the eval on the 10 labelled tickets, diff against the previous report, summarise misses.
---
1. If `evals/report.md` exists, copy it to a temp file first so you can diff.
2. Run `uv run ticket-agent eval $ARGUMENTS`.
3. Show the metric table from the new `evals/report.md`, then the diff against the previous report (metrics that moved, tickets whose result changed).
4. For every miss in the per-ticket table, give the one-line diagnosis from the report and say whether it is a label problem, a rubric problem or a model problem.
5. Do not change prompts, policy or labels in this command. Propose changes only.
