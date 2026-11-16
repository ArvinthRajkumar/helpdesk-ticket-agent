"""Eval on the 10 labelled examples from GET /examples. Gold labels are Nordavo's; the model
under test never writes them. Calls decide() only and never posts.

The examples are cached in .cache/ (gitignored) and never committed. The report lists
example ids and the fields that differ, never ticket text. n=10 is tiny, so every eval runs
the set more than once and reports the spread.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

from ticket_agent.decide import Agent
from ticket_agent.helpdesk import Helpdesk
from ticket_agent.models import ROOT, Ticket, scrub_all

CACHE = ROOT / ".cache" / "examples.json"
EVALS_DIR = ROOT / "evals"
FIELDS = ("language", "category", "priority", "action")


def load_examples(hd: Helpdesk, refresh: bool = False, cache: Path = CACHE) -> list[dict]:
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8"))
    examples = hd.examples()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(examples, ensure_ascii=False), encoding="utf-8", newline="\n")
    return examples


def score_once(examples: list[dict], agent: Agent) -> list[dict]:
    rows = []
    for ex in examples:
        result = agent.decide(Ticket.model_validate(ex))
        got = {f: (result.action if f == "action" else result.body.get(f)) for f in FIELDS}
        gold = {f: ex["label"][f] for f in FIELDS}
        r = result.record
        rows.append(
            {
                "id": ex["id"],
                "gold": gold,
                "got": got,
                "proposed_action": r.get("proposed_action"),
                "gate": r.get("gate"),
                "flags": r.get("flags", []),
                "keyword_hits": r.get("keyword_hits", []),
                "faq_ids": r.get("faq_ids", []),
                # Outcome only: a failure message can quote reply text derived from the example.
                "checks": {
                    k: "pass" if v == "pass" else "fail" for k, v in r.get("checks", {}).items()
                },
                "confidence": result.body.get("confidence"),
                "fallback": r.get("fallback", False),
                "cost_usd": r.get("cost_usd") or 0.0,
            }
        )
        print(f"{ex['id']}: gold {gold['action']}, got {got['action']} ({r.get('gate')})")
    return rows


def metrics(rows: list[dict]) -> dict:
    n = len(rows)
    unsafe = [
        r["id"] for r in rows if r["gold"]["action"] != "reply" and r["got"]["action"] == "reply"
    ]
    gold_replies = [r for r in rows if r["gold"]["action"] == "reply"]
    good_replies = [
        r["id"]
        for r in gold_replies
        if r["got"]["action"] == "reply" and r["got"]["language"] == r["gold"]["language"]
    ]
    out = {
        "n": n,
        "unsafe_replies": unsafe,
        "gold_replies": len(gold_replies),
        "good_replies": good_replies,
    }
    for f in FIELDS:
        out[f"{f}_acc"] = sum(r["gold"][f] == r["got"][f] for r in rows) / n if n else 0.0
    cost = sum(r["cost_usd"] for r in rows)
    out["cost_usd"] = cost
    out["cost_per_100_usd"] = cost / n * 100 if n else 0.0
    return out


def render(runs: list[list[dict]], model: str, label: str) -> str:
    ms = [metrics(rows) for rows in runs]
    head = "| metric | " + " | ".join(f"run {i + 1}" for i in range(len(ms))) + " | spread |"
    lines = [
        f"# Eval: {label}",
        "",
        f"Model `{model}`, {ms[0]['n']} labelled examples from GET /examples, {len(ms)} runs.",
        "Gold labels are Nordavo's. Ids and fields only; no ticket text in this file.",
        "",
        head,
        "|" + "---|" * (len(ms) + 2),
    ]

    def row(name: str, values: list[float], fmt: str) -> None:
        spread = max(values) - min(values)
        cells = " | ".join(fmt.format(v) for v in values)
        lines.append(f"| {name} | {cells} | {fmt.format(spread)} |")

    row(
        "1. unsafe replies (gold not reply, agent replied)",
        [len(m["unsafe_replies"]) for m in ms],
        "{:.0f}",
    )
    row(
        f"2. good replies on gold replies (of {ms[0]['gold_replies']})",
        [len(m["good_replies"]) for m in ms],
        "{:.0f}",
    )
    for f in FIELDS:
        row(f"3. {f} accuracy", [m[f"{f}_acc"] for m in ms], "{:.0%}")
    row("4. cost per 100 tickets (USD, list price)", [m["cost_per_100_usd"] for m in ms], "{:.4f}")
    lines += [
        "",
        "Unsafe replies per run: "
        + "; ".join(", ".join(m["unsafe_replies"]) or "none" for m in ms),
    ]
    lines += [
        "",
        "## Differences from gold",
        "",
        "| run | id | field | gold | agent | gate |",
        "|---|---|---|---|---|---|",
    ]
    for i, rows in enumerate(runs, start=1):
        for r in rows:
            for f in FIELDS:
                if r["gold"][f] != r["got"][f]:
                    cells = [str(i), r["id"], f, r["gold"][f], r["got"][f], r["gate"] or ""]
                    lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def run_eval(hd: Helpdesk, agent: Agent, runs: int, label: str, refresh: bool = False) -> Path:
    examples = load_examples(hd, refresh)
    all_rows = []
    for i in range(runs):
        print(f"run {i + 1} of {runs}")
        all_rows.append(score_once(examples, agent))
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    results = EVALS_DIR / "results" / f"{stamp}-{label}.json"
    results.parent.mkdir(parents=True, exist_ok=True)
    payload = scrub_all({"label": label, "model": agent.cfg.model, "runs": all_rows})
    results.write_text(json.dumps(payload, indent=1), encoding="utf-8", newline="\n")
    report = EVALS_DIR / "report.md"
    text = render(all_rows, agent.cfg.model, label)
    footer = f"\nRaw results: `{results.relative_to(ROOT).as_posix()}`\n"
    report.write_text(text + footer, encoding="utf-8", newline="\n")
    return report
