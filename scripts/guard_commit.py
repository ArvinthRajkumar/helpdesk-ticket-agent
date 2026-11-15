"""PreToolUse hook: block `git commit` when staged content could leak a secret.

Claude Code pipes the tool call as JSON on stdin. Exit code 2 blocks the call and
shows stderr to the model. Any other exit code lets the command through, so every
unexpected error falls back to allow rather than wedging the session.
"""

import json
import re
import subprocess
import sys

# Anthropic keys start with sk-ant-. The generic pattern catches other long tokens
# assigned to key-like names. False positives are cheap: the user sees the reason.
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"(?i)(api[_-]?key|secret|token)\s*[=:]\s*['\"]?[A-Za-z0-9_-]{32,}"),
]
BLOCKED_PATHS = re.compile(r"(^|/)\.env(\..+)?$")
ALLOWED_PATHS = {".env.example"}


def staged_files() -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--staged", "--name-only"], capture_output=True, text=True, check=False
    )
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def staged_added_lines() -> str:
    out = subprocess.run(
        ["git", "diff", "--staged", "--unified=0"], capture_output=True, text=True, check=False
    )
    return "\n".join(line[1:] for line in out.stdout.splitlines() if line.startswith("+"))


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = payload.get("tool_input", {}).get("command", "")
    # git, optional global options such as -C . or -c k=v, then the commit subcommand.
    # Group 1 stops at the end of that shell statement so flags of a later command do not count.
    match = re.search(r"\bgit(?:\s+-\S+(?:\s+[^-\s]\S*)?)*\s+commit\b([^;&|\n]*)", command)
    if not match:
        return 0
    # Flags that stage files after this check ran, so the staged diff below would be stale.
    if re.search(r"\s(-a|--all|--include)\b", match.group(1)):
        print(
            "BLOCKED: commit -a stages files after this check. Stage explicit paths.",
            file=sys.stderr,
        )
        return 2

    bad_paths = [
        p
        for p in staged_files()
        if BLOCKED_PATHS.search(p) and p.split("/")[-1] not in ALLOWED_PATHS
    ]
    if bad_paths:
        print(
            f"BLOCKED: staged env file(s) {bad_paths}. Unstage them before committing.",
            file=sys.stderr,
        )
        return 2

    added = staged_added_lines()
    for pattern in SECRET_PATTERNS:
        match = pattern.search(added)
        if match:
            preview = match.group(0)[:12] + "..."
            print(f"BLOCKED: staged diff matches a secret pattern ({preview}).", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
