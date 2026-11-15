import pytest

from ticket_agent.cost import cost_usd, load_pricing
from ticket_agent.llm import Usage

PRICING = load_pricing()


def test_haiku_plain_tokens() -> None:
    # 100k input at 0.10 plus 100k output at 0.50 per million, below the long context tier
    assert cost_usd("claude-haiku-5-5", Usage(100_000, 100_000), PRICING) == pytest.approx(0.06)


def test_cache_tokens_are_priced_separately() -> None:
    usage = Usage(
        input_tokens=0, output_tokens=0, cache_read_tokens=50_000, cache_write_tokens=50_000
    )
    assert cost_usd("claude-haiku-5-5", usage, PRICING) == pytest.approx(
        50_000 * (0.01 + 0.125) / 1_000_000
    )


def test_haiku_long_context_tier_kicks_in_above_threshold() -> None:
    small = cost_usd("claude-haiku-5-5", Usage(100_000, 0), PRICING)
    large = cost_usd("claude-haiku-5-5", Usage(100_001, 0), PRICING)
    assert small == pytest.approx(0.01)
    assert large == pytest.approx(100_001 * 0.50 / 1_000_000)


def test_sonnet_rates() -> None:
    assert cost_usd("claude-sonnet-5-5", Usage(1000, 100), PRICING) == pytest.approx(0.002 + 0.001)


def test_unknown_model_is_none_not_zero() -> None:
    assert cost_usd("claude-opus-5-5", Usage(10, 10), PRICING) is None
