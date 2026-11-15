---
description: Run the triage agent over data/tickets.csv and summarise the run.
---
Run `uv run ticket-agent run $ARGUMENTS`. Then read `runs/<run_id>/summary.md` for the run id printed by the command and report: counts per route, automation rate, total cost, mean latency, and any ticket that ended in human_review because of an error (grep `"error"` in `runs/<run_id>/audit.jsonl` for non-null values). Do not edit anything.
