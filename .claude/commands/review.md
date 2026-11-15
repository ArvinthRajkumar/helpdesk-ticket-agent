---
description: Run the read-only reviewer subagent on the current diff.
---
Use the `reviewer` subagent on `$ARGUMENTS` (default: the full diff from the first commit, `git diff $(git rev-list --max-parents=0 HEAD)`). Relay its findings verbatim, then for each high or medium finding say whether you agree and what the fix would be. Do not apply fixes in this command.
