"""Write-ahead journal. The decision is on disk before it is posted.

Two events per ticket: `intent` (decision about to be posted, with the exact body) and
`result` (how the post ended). An intent with no later result is pending: the process died
or the helpdesk stayed down between the two. On the next run a pending ticket is checked
with a GET and either marked done or posted from the journal, without asking the model
again. That is what keeps a customer from getting two answers.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from ticket_agent.models import ROOT

STATE_DIR = ROOT / "state"


class Journal:
    def __init__(self, path: Path = STATE_DIR / "journal.jsonl") -> None:
        self.path = path

    def _append(self, record: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"ts": datetime.now(UTC).isoformat(), **record}) + "\n"
        with self.path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)
            fh.flush()
            # fsync so an intent survives a power cut, not only a crash of this process.
            os.fsync(fh.fileno())

    def intent(self, ticket_id: str, run_id: str, action: str, body: dict) -> None:
        self._append(
            {
                "event": "intent",
                "ticket_id": ticket_id,
                "run_id": run_id,
                "action": action,
                "body": body,
            }
        )

    def result(self, ticket_id: str, run_id: str, status: str) -> None:
        self._append(
            {"event": "result", "ticket_id": ticket_id, "run_id": run_id, "status": status}
        )

    def records(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # Only the last line can be torn by a crash. A torn intent was never posted,
                # because the post happens after the write returns. Anywhere else: corruption.
                if i == len(lines) - 1:
                    continue
                raise
        return out

    def pending(self) -> dict[str, dict]:
        """ticket_id -> its latest intent, for tickets whose latest event is an intent."""
        latest: dict[str, dict] = {}
        for r in self.records():
            latest[r["ticket_id"]] = r
        return {tid: r for tid, r in latest.items() if r["event"] == "intent"}

    def archive(self) -> Path | None:
        """After a practice reset every ticket is open again, so old intents must not replay."""
        if not self.path.exists():
            return None
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        target = self.path.with_name(f"journal-before-reset-{stamp}.jsonl")
        self.path.rename(target)
        return target
