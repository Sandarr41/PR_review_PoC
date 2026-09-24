"""LLM API integration (docs/specs/tools-api.md — LLM API section).

Talks to any OpenAI-Chat-Completions-shaped endpoint via the `openai` SDK.
The PoC's own provider is Polza.ai (docs/specs/serving-config.md) — a
unified proxy in front of ~400 underlying models (OpenAI, Anthropic,
Google, ...) reached via LLM_BASE_URL/LLM_AUTH_TOKEN/LLM_MODEL
(config.py) — but any endpoint speaking the same protocol works. Code is
passed to the model only as data inside the user message — never
concatenated into the system prompt — so the system prompt stays fixed and
isolated (docs/governance.md, раздел 4).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

import openai

from .rate_limiter import RateLimiter

logger = logging.getLogger("pr_review_agent")


class LLMUnavailableError(Exception):
    """Raised when the LLM API cannot serve a request after retries.

    Callers (agents/orchestrator) must catch this and fall back to a
    tools-only report rather than let it bubble up as a run failure
    (docs/system-design.md § 7).
    """


@dataclass
class LLMClient:
    model: str = "openai/gpt-4o"
    temperature: float = 0.2
    max_tokens: int = 4096
    request_timeout_seconds: float = 30.0
    max_retries: int = 1
    api_key: str | None = None
    base_url: str | None = None
    # Client-side throttle, shared across the parallel agent calls made
    # against one LLMClient instance (docs/diagrams/c4-component.md,
    # "Circuit Breaker / Rate Limiter").
    requests_per_minute: int = 5

    def __post_init__(self) -> None:
        self._client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.request_timeout_seconds,
            max_retries=self.max_retries,
        )
        self._rate_limiter = RateLimiter(max_calls=self.requests_per_minute, period_seconds=60.0)
        # Real, measured usage (docs/economics.md) — not an estimate.
        # total_cost_rub comes straight from the provider's own per-call
        # billing (usage.cost_rub / usage.cost on Polza's response, an
        # OpenAI-shape extension) rather than a static per-model price
        # table, which isn't maintainable across an aggregator's ~400
        # models with their own (and changing) pricing.
        # Thread-safe: agents call complete() concurrently on one instance.
        self._usage_lock = threading.Lock()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0
        self.total_cost_rub = 0.0

    def complete(self, system_prompt: str, user_content: str) -> str:
        """Sends a single-turn request and returns the text of the response.

        Raises LLMUnavailableError on timeout/rate-limit/server error/safety
        block — the caller decides the fallback behavior.
        """
        waited = self._rate_limiter.acquire()
        if waited > 0:
            logger.info(
                "rate_limiter_throttled",
                extra={"extra_fields": {"event": "rate_limiter_throttled", "waited_seconds": round(waited, 2)}},
            )

        try:
            response = self._client.chat.completions.create(
                model=self.model,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
            )
        except openai.APIStatusError as exc:
            raise LLMUnavailableError(f"API error {exc.status_code}: {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise LLMUnavailableError(f"connection error: {exc}") from exc

        if not response.choices:
            raise LLMUnavailableError("no choices returned (likely blocked before generation)")

        choice = response.choices[0]
        if choice.finish_reason == "content_filter":
            raise LLMUnavailableError("model declined the request: content_filter")
        if getattr(choice.message, "refusal", None):
            raise LLMUnavailableError(f"model declined the request: {choice.message.refusal}")

        if response.usage is not None:
            self._record_usage(response.usage)
        return choice.message.content or ""

    def _record_usage(self, usage) -> None:
        input_tokens = getattr(usage, "prompt_tokens", None) or 0
        output_tokens = getattr(usage, "completion_tokens", None) or 0
        cost_rub = getattr(usage, "cost_rub", None)
        if cost_rub is None:
            cost_rub = getattr(usage, "cost", None) or 0.0
        with self._usage_lock:
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.total_calls += 1
            self.total_cost_rub += cost_rub
        logger.info(
            "llm_call_completed",
            extra={
                "extra_fields": {
                    "event": "llm_call_completed",
                    "model": self.model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "cost_rub": cost_rub,
                }
            },
        )

    def estimated_cost_rub(self) -> float:
        """Real cost incurred by this client instance so far, as reported
        by the provider per call (docs/economics.md) — not a guess."""
        return self.total_cost_rub
