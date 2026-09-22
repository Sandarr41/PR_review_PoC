import pytest

from pr_review_agent.pricing import UnknownModelPricingError, estimate_cost_usd


def test_estimate_cost_usd_known_model():
    # gemini-3.6-flash: $0.75/1M input, $3.75/1M output
    cost = estimate_cost_usd("gemini-3.6-flash", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(0.75 + 3.75)


def test_estimate_cost_usd_zero_tokens():
    assert estimate_cost_usd("gemini-3.6-flash", 0, 0) == 0.0


def test_estimate_cost_usd_unknown_model_raises():
    with pytest.raises(UnknownModelPricingError):
        estimate_cost_usd("not-a-real-model", 100, 100)


def test_estimate_cost_usd_scales_linearly():
    small = estimate_cost_usd("gemini-2.5-flash", 100_000, 50_000)
    large = estimate_cost_usd("gemini-2.5-flash", 200_000, 100_000)
    assert large == pytest.approx(small * 2)
