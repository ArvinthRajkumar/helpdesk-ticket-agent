import pytest

from ticket_agent.cost import cost_usd, load_pricing
from ticket_agent.llm import Usage

PRICING = load_pricing()


def test_flash_lite_plain_tokens() -> None:
    # 100k input at 0.25 plus 100k output at 1.50 per million
    assert cost_usd("gemini-3.1-flash-lite", Usage(100_000, 100_000), PRICING) == pytest.approx(
        0.025 + 0.15
    )


def test_cached_tokens_are_logged_but_priced_as_input() -> None:
    # cached_tokens is a subset of input_tokens, so it must not be added a second time.
    usage = Usage(input_tokens=100_000, output_tokens=0, cached_tokens=40_000)
    assert cost_usd("gemini-3.1-flash-lite", usage, PRICING) == pytest.approx(0.025)


def test_comparison_model_uses_its_own_rates() -> None:
    assert cost_usd("gemini-3.8-flash", Usage(1000, 100), PRICING) == pytest.approx(
        0.00075 + 0.000375
    )


def test_thinking_tokens_are_billed_as_output() -> None:
    # Regression guard: a cost that ignores thinking_tokens equals the no-thinking cost.
    without = cost_usd("gemini-3.1-flash-lite", Usage(1000, 100, thinking_tokens=0), PRICING)
    with_thinking = cost_usd(
        "gemini-3.1-flash-lite", Usage(1000, 100, thinking_tokens=400), PRICING
    )
    assert with_thinking == pytest.approx(without + 400 * 1.50 / 1_000_000)
    assert with_thinking > without


def test_unknown_model_is_none_not_zero() -> None:
    assert cost_usd("gemini-2.5-flash", Usage(10, 10), PRICING) is None


def test_price_change_is_recorded_for_the_comparison_model() -> None:
    change = PRICING["gemini-3.8-flash"]["price_change"]
    assert str(change["effective"]) == "2027-01-01" and change["output"] > 3.75
