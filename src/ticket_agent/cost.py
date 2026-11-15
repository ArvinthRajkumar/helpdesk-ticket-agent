"""Turn measured token usage into dollars using config/pricing.yaml."""

from pathlib import Path

import yaml

from ticket_agent.llm import Usage
from ticket_agent.models import ROOT

PRICING_PATH = ROOT / "config" / "pricing.yaml"


def load_pricing(path: Path = PRICING_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["models"]


def cost_usd(model: str, usage: Usage, pricing: dict) -> float | None:
    """None when the model has no price row, so the caller can mark the cost unverified
    instead of silently reporting zero."""
    rates = pricing.get(model)
    if rates is None:
        return None
    prompt_tokens = usage.input_tokens + usage.cache_read_tokens + usage.cache_write_tokens
    long = rates.get("long_context")
    if long and prompt_tokens > long["threshold_tokens"]:
        rates = long
    per_token = 1 / 1_000_000
    return per_token * (
        usage.input_tokens * rates["input"]
        + usage.output_tokens * rates["output"]
        + usage.cache_read_tokens * rates["cache_read"]
        + usage.cache_write_tokens * rates["cache_write"]
    )
