"""One JSON line per ticket per run in logs/<run_id>.jsonl."""

import json
from datetime import UTC, datetime
from pathlib import Path

from ticket_agent.models import ROOT

LOGS_DIR = ROOT / "logs"


class RunLog:
    def __init__(self, run_id: str, logs_dir: Path = LOGS_DIR) -> None:
        logs_dir.mkdir(parents=True, exist_ok=True)
        self.path = logs_dir / f"{run_id}.jsonl"

    def write(self, record: dict) -> None:
        # Open, append, close per ticket: a crash keeps every earlier line intact.
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(UTC).isoformat(), **record}) + "\n")


def latest_log(logs_dir: Path = LOGS_DIR) -> Path | None:
    logs = sorted(logs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def read_log(path: Path) -> list[dict]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]
