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
    # Cached tokens are already inside input_tokens and are priced as plain input here, which
    # is the conservative choice. The count is logged so a cheaper price could be applied later.
    per_token = 1 / 1_000_000
    return per_token * (usage.input_tokens * rates["input"] + usage.output_tokens * rates["output"])
