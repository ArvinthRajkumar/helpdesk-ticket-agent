"""Append-only JSONL audit log plus the two human-facing views derived from it.

results.csv and summary.md are rebuilt from audit.jsonl every time, so a resumed run and a
fresh run produce the same files from the same records.
"""

import csv
import json
import statistics
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ticket_agent.models import ROOT, Route

RUNS_DIR = ROOT / "runs"
RESULT_COLUMNS = [
    "ticket_id",
    "category",
    "priority",
    "priority_reason",
    "confidence",
    "route",
    "queue",
    "rule",
    "human_reason",
    "draft",
]


class AuditLog:
    def __init__(self, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        self.path = run_dir / "audit.jsonl"

    def write(self, record: dict) -> None:
        # Open, append, close per record: a crash mid-run leaves every earlier line intact.
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(UTC).isoformat(), **record}) + "\n")


def read_log(run_dir: Path) -> list[dict]:
    path = run_dir / "audit.jsonl"
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def completed_ids(records: list[dict]) -> set[str]:
    """A ticket counts as done only when its decision record was written."""
    return {r["ticket_id"] for r in records if r["step"] == "decision"}


def write_views(run_dir: Path, records: list[dict]) -> dict:
    decisions = [r for r in records if r["step"] == "decision"]
    with (run_dir / "results.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for d in decisions:
            writer.writerow({**d.get("output", {}), **d, "draft": d.get("draft", "")})

    n = len(decisions)
    routes = Counter(d["route"] for d in decisions)
    costs = [r.get("cost_usd") for r in records if r["step"] != "decision"]
    unverified = any(c is None for c in costs)
    total_cost = sum(c or 0 for c in costs)
    latency = [d.get("latency_ms", 0) for d in decisions]
    # Tickets with any error, not error lines: the decision record repeats the triage error.
    errors = {r["ticket_id"] for r in records if r.get("error")}
    automated = routes[Route.AUTO_REPLY_DRAFT] + routes[Route.ROUTE_TO_TEAM]
    summary = {
        "tickets": n,
        "routes": dict(routes),
        "automation_rate": round(automated / n, 3) if n else 0,
        "auto_draft_rate": round(routes[Route.AUTO_REPLY_DRAFT] / n, 3) if n else 0,
        "total_cost_usd": round(total_cost, 6),
        "cost_unverified": unverified,
        "mean_cost_per_ticket_usd": round(total_cost / n, 6) if n else 0,
        "mean_latency_ms": int(statistics.mean(latency)) if latency else 0,
        "p50_latency_ms": int(statistics.median(latency)) if latency else 0,
        "errors": len(errors),
        "models": sorted({r["model"] for r in records if r.get("model")}),
        "prompt_versions": sorted(
            {r["prompt_version"] for r in records if r.get("prompt_version")}
        ),
    }
    lines = [
        f"# Run {run_dir.name}",
        "",
        f"Tickets: {n}",
        f"Routes: {', '.join(f'{k} {v}' for k, v in sorted(routes.items()))}",
        f"Automation rate (not human_review): {summary['automation_rate']:.0%}",
        f"Auto draft rate: {summary['auto_draft_rate']:.0%}",
        f"Total cost: ${total_cost:.4f}"
        + (" (unverified: a model had no price row)" if unverified else ""),
        f"Mean cost per ticket: ${summary['mean_cost_per_ticket_usd']:.5f}",
        f"Cost per 100 tickets at this mix: ${summary['mean_cost_per_ticket_usd'] * 100:.3f}",
        f"Latency per ticket: mean {summary['mean_latency_ms']} ms, "
        f"p50 {summary['p50_latency_ms']} ms",
        f"Tickets with errors: {len(errors)}",
        f"Models: {', '.join(summary['models'])}",
        f"Prompt versions: {', '.join(summary['prompt_versions'])}",
    ]
    (run_dir / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def latest_run_dir() -> Path | None:
    runs = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir() and (p / "audit.jsonl").exists())
    return runs[-1] if runs else None
