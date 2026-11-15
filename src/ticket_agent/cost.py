"""Turn measured token usage into dollars using config/pricing.yaml."""

from datetime import date
from pathlib import Path

import yaml

from ticket_agent.llm import Usage
from ticket_agent.models import ROOT

PRICING_PATH = ROOT / "config" / "pricing.yaml"


def load_pricing(path: Path = PRICING_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["models"]


def cost_usd(model: str, usage: Usage, pricing: dict, today: date | None = None) -> float | None:
    """None when the model has no price row, so the caller can mark the cost unverified
    instead of silently reporting zero."""
    rates = pricing.get(model)
    if rates is None:
        return None
    # Introductory prices have an end date. Once it passes, the new rates apply automatically.
    change = rates.get("price_change")
    if change and (today or date.today()) >= change["effective"]:
        rates = change
    # Cached tokens are already inside input_tokens and are priced as plain input here, which
    # is the conservative choice. The count is logged so a cheaper price could be applied later.
    # Thinking tokens are billed at the output rate. Dropping them under-reports cost by the
    # whole thinking budget, which on a classification call can exceed the visible answer.
    billed_output = usage.output_tokens + usage.thinking_tokens
    per_token = 1 / 1_000_000
    return per_token * (usage.input_tokens * rates["input"] + billed_output * rates["output"])
