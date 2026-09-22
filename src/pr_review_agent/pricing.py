"""Gemini API pricing (docs/economics.md).

Source: https://ai.google.dev/gemini-api/docs/pricing (fetched 2026-09-21,
"Standard tier", paid usage — the free tier used for this PoC's own testing
is $0 but throttled, see docs/economics.md and RateLimiter in
rate_limiter.py). Prices are USD per 1M tokens, (input, output).

Update this table if you change ``llm.model`` in config.yaml to a model
not listed here — ``estimate_cost_usd`` raises rather than silently costing
$0, so a stale/missing price can't hide in a report.
"""
from __future__ import annotations

MODEL_PRICING_PER_1M_USD: dict[str, tuple[float, float]] = {
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.1-pro-preview": (2.00, 12.00),  # prompts <= 200k tokens
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),  # prompts <= 200k tokens
}


class UnknownModelPricingError(Exception):
    pass


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in MODEL_PRICING_PER_1M_USD:
        raise UnknownModelPricingError(
            f"no pricing entry for model '{model}' — add it to pricing.MODEL_PRICING_PER_1M_USD"
        )
    input_price, output_price = MODEL_PRICING_PER_1M_USD[model]
    return (input_tokens / 1_000_000) * input_price + (output_tokens / 1_000_000) * output_price
