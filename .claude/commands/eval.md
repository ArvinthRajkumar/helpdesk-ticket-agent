---
description: Run the eval on GET /examples, diff against the previous report, summarise misses.
---
1. If `evals/report.md` exists, copy it to a temp file first so you can diff.
2. Run `uv run ticket-agent eval $ARGUMENTS`. It calls decide() only and never posts.
3. Show the metric table from the new `evals/report.md` (safety first), then what moved against the previous report.
4. For each difference from gold, name the gate and say whether it is a rubric, policy, check or model problem.
5. Do not change prompts, policy or code in this command, and never quote example ticket text. Propose changes only.
