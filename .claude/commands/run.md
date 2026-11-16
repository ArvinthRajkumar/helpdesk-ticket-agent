---
description: Run the helpdesk agent (dry run by default) and summarise the run log.
---
Run `uv run ticket-agent run --dry-run $ARGUMENTS`. Only drop `--dry-run` when the user explicitly asks for a real run, because a real run posts answers to the helpdesk. Never run `reset`. Then read `logs/<run_id>.jsonl` for the run id printed by the command and report: counts per action, gates that turned a proposed reply into a handoff, API events (503, 429), fallbacks, total cost and cost per 100 tickets. Do not edit anything.
