"""Eval runner: run the labelled tickets, score them, write evals/report.md.

Each eval run stores its metrics as evals/results/<model>__<prompt_version>.json. The report
renders every stored file side by side, so model comparisons and before/after tuning columns
appear without extra code. Hand-written miss diagnoses live in evals/diagnoses.yaml and are
merged in, so re-running the eval never erases them.
"""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import BaseModel

from ticket_agent.audit import RUNS_DIR, read_log
from ticket_agent.llm import GeminiClient
from ticket_agent.models import ROOT, Category, Priority, Route, load_tickets
from ticket_agent.pipeline import require_credentials, run_pipeline
from ticket_agent.policy import load_config

EVALS_DIR = ROOT / "evals"
PRIORITY_RANK = {
    p: i for i, p in enumerate([Priority.LOW, Priority.MEDIUM, Priority.HIGH, Priority.URGENT])
}


class Label(BaseModel):
    category: Category
    priority: Priority
    should_escalate: bool
    note: str = ""


def load_labels(path: Path = EVALS_DIR / "labels.yaml") -> dict[str, Label]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {tid: Label.model_validate(v) for tid, v in data["tickets"].items()}


def expected_route(label: Label) -> Route:
    if label.should_escalate or label.category is Category.OTHER:
        return Route.HUMAN_REVIEW
    if label.category is Category.QUESTION:
        return Route.AUTO_REPLY_DRAFT
    return Route.ROUTE_TO_TEAM


def score(decisions: dict[str, dict], labels: dict[str, Label]) -> dict:
    """decisions: ticket_id -> decision record from the audit log."""
    rows, cat_ok, pri_ok, pri_near, route_ok = [], 0, 0, 0, 0
    tp = fp = fn = false_auto = 0
    for tid, label in labels.items():
        d = decisions.get(tid, {})
        out = d.get("output") or {}
        cat, pri, route = out.get("category"), out.get("priority"), d.get("route")
        escalated = route == Route.HUMAN_REVIEW
        cat_ok += cat == label.category
        pri_ok += pri == label.priority
        pri_near += (
            pri in PRIORITY_RANK and abs(PRIORITY_RANK[pri] - PRIORITY_RANK[label.priority]) <= 1
        )
        route_ok += route == expected_route(label)
        tp += label.should_escalate and escalated
        fp += (not label.should_escalate) and escalated
        fn += label.should_escalate and not escalated
        false_auto += label.should_escalate and route == Route.AUTO_REPLY_DRAFT
        rows.append(
            {
                "ticket_id": tid,
                "label_category": label.category,
                "pred_category": cat,
                "label_priority": label.priority,
                "pred_priority": pri,
                "should_escalate": label.should_escalate,
                "route": route,
                "rule": d.get("rule"),
                "miss": not (
                    cat == label.category
                    and pri == label.priority
                    and route == expected_route(label)
                ),
            }
        )
    n, n_esc = len(labels), sum(lab.should_escalate for lab in labels.values())
    return {
        "n": n,
        "category_accuracy": cat_ok / n,
        "priority_exact": pri_ok / n,
        "priority_within_one": pri_near / n,
        "route_match": route_ok / n,
        "escalation_precision": tp / (tp + fp) if tp + fp else None,
        "escalation_recall": tp / (tp + fn) if tp + fn else None,
        "false_auto_count": false_auto,
        "false_auto_rate": false_auto / n_esc if n_esc else None,
        "rows": rows,
    }


def cmd_eval(args: argparse.Namespace) -> None:
    require_credentials()
    cfg = load_config()
    if args.model:
        cfg.models.triage = cfg.models.draft = args.model
    labels = load_labels()
    tickets = [t for t in load_tickets(Path(args.tickets)) if t.id in labels]
    missing = set(labels) - {t.id for t in tickets}
    if missing:
        raise SystemExit(f"Labelled tickets missing from CSV: {sorted(missing)}")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / f"eval-{cfg.models.triage}-{stamp}"
    client = GeminiClient(cfg.max_rpm, cfg.retry_attempts)
    summary = run_pipeline(tickets, client, cfg, run_dir, resume=False)
    records = read_log(run_dir)
    decisions = {r["ticket_id"]: r for r in records if r["step"] == "decision"}
    metrics = score(decisions, labels)
    triage_cost = sum(r.get("cost_usd") or 0 for r in records if r["step"] == "triage")
    draft_cost = sum(r.get("cost_usd") or 0 for r in records if r["step"] == "draft")
    version = next(r["prompt_version"] for r in records if r["step"] == "triage")
    result = {
        "run_id": run_dir.name,
        "model": cfg.models.triage,
        "prompt_version": version,
        "thinking_level": cfg.thinking_for(cfg.models.triage),
        **metrics,
        "triage_cost_usd": triage_cost,
        "draft_cost_usd": draft_cost,
        "mean_cost_per_ticket_usd": summary["mean_cost_per_ticket_usd"],
        "mean_latency_ms": summary["mean_latency_ms"],
    }
    out_dir = EVALS_DIR / "results"
    out_dir.mkdir(exist_ok=True)
    (out_dir / f"{cfg.models.triage}__{version}.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8"
    )
    write_report()
    print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))


RATE_SUFFIXES = ("accuracy", "exact", "within_one", "match", "precision", "recall", "rate")


def fmt(key: str, v) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.0%}" if key.endswith(RATE_SUFFIXES) else f"{v:.4f}"
    return str(v)


def write_report(
    results_dir: Path = EVALS_DIR / "results", report: Path = EVALS_DIR / "report.md"
) -> None:
    files = sorted(results_dir.glob("*.json"))
    results = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    n = results[0]["n"] if results else 0
    diag_path = EVALS_DIR / "diagnoses.yaml"
    diagnoses = yaml.safe_load(diag_path.read_text(encoding="utf-8")) if diag_path.exists() else {}
    metric_keys = [
        "category_accuracy",
        "priority_exact",
        "priority_within_one",
        "route_match",
        "escalation_precision",
        "escalation_recall",
        "false_auto_count",
        "false_auto_rate",
        "mean_cost_per_ticket_usd",
        "triage_cost_usd",
        "draft_cost_usd",
        "mean_latency_ms",
    ]
    lines = [
        "# Eval report",
        "",
        f"Generated by `uv run ticket-agent eval`. n is {n}, so one ticket moves every rate by",
        f"{100 // n if n else 0} points; treat every number as a smoke signal, not a measurement.",
        "Labels were drafted by Claude Code before any agent run and are marked proposed until",
        "reviewed. The eval set was also used to decide tuning changes, so later columns are not a",
        "clean holdout.",
        "",
        "## Metrics side by side",
        "",
        "| metric | " + " | ".join(f"{r['model']} / {r['prompt_version']}" for r in results) + " |",
        "|---|" + "---|" * len(results),
    ]
    lines += [
        f"| {k} | " + " | ".join(fmt(k, r.get(k)) for r in results) + " |" for k in metric_keys
    ]
    lines += ["| run_id | " + " | ".join(r["run_id"] for r in results) + " |"]
    for r in results:
        key = f"{r['model']}__{r['prompt_version']}"
        lines += [
            "",
            f"## Per ticket: {r['model']} / {r['prompt_version']} (run {r['run_id']})",
            "",
            "| ticket | label cat | pred cat | label pri | pred pri | should_escalate "
            "| route | rule | miss |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for row in r["rows"]:
            lines.append(
                f"| {row['ticket_id']} | {row['label_category']} | {row['pred_category']} | "
                f"{row['label_priority']} | {row['pred_priority']} | {row['should_escalate']} | "
                f"{row['route']} | {row['rule']} | {'MISS' if row['miss'] else ''} |"
            )
        misses = [row for row in r["rows"] if row["miss"]]
        lines += ["", f"Misses: {len(misses)}", ""]
        for row in misses:
            note = (diagnoses.get(key) or {}).get(row["ticket_id"], "diagnosis pending")
            lines.append(f"- {row['ticket_id']}: {note}")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
