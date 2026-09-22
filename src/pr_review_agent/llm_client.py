"""LLM API integration (docs/specs/tools-api.md — LLM API section).

Uses Google's Gemini API via the `google-genai` SDK. Code is passed to the
model only as data inside the user message — never concatenated into the
system prompt — so the system prompt stays fixed and isolated
(docs/governance.md, раздел 4).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from google import genai
from google.genai import errors, types

from .pricing import estimate_cost_usd
from .rate_limiter import RateLimiter

logger = logging.getLogger("pr_review_agent")

# Finish reasons that mean "the model declined/blocked this content" rather
# than a normal stop — treated the same as an unavailable LLM so the
# orchestrator falls back to a tools-only report.
_REFUSAL_FINISH_REASONS = {
    types.FinishReason.SAFETY,
    types.FinishReason.PROHIBITED_CONTENT,
    types.FinishReason.RECITATION,
    types.FinishReason.BLOCKLIST,
    types.FinishReason.SPII,
}


class LLMUnavailableError(Exception):
    """Raised when the LLM API cannot serve a request after retries.

    Callers (agents/orchestrator) must catch this and fall back to a
    tools-only report rather than let it bubble up as a run failure
    (docs/system-design.md § 7).
    """


@dataclass
class LLMClient:
    model: str = "gemini-3.6-flash"
    temperature: float = 0.2
    max_tokens: int = 4096
    request_timeout_seconds: float = 30.0
    max_retries: int = 1
    api_key: str | None = None
    # Client-side throttle, shared across the parallel agent calls made
    # against one LLMClient instance (docs/diagrams/c4-component.md,
    # "Circuit Breaker / Rate Limiter"). Default matches the free-tier
    # Gemini limit observed in practice (eval/README.md) — raise it if your
    # plan/quota allows more.
    requests_per_minute: int = 5

    def __post_init__(self) -> None:
        # A bare Client() resolves GOOGLE_API_KEY from the environment,
        # which is also how a proxy-style GOOGLE_GEMINI_BASE_URL override
        # would be picked up — mirrors docs/specs/serving-config.md's
        # "secrets via env, not hardcoded" rule.
        self._client = genai.Client(api_key=self.api_key) if self.api_key else genai.Client()
        self._rate_limiter = RateLimiter(max_calls=self.requests_per_minute, period_seconds=60.0)
        # Real, measured token usage (docs/economics.md) — not an estimate.
        # Thread-safe: agents call complete() concurrently on one instance.
        self._usage_lock = threading.Lock()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.total_calls = 0

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
            response = self._client.models.generate_content(
                model=self.model,
                contents=user_content,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    max_output_tokens=self.max_tokens,
                    temperature=self.temperature,
                    # Findings are structured JSON, not a reasoned essay —
                    # disabling thinking avoids silently burning the output
                    # token budget on invisible thought tokens (seen live:
                    # thinking_budget left at its default truncated a
                    # 32-token response to empty text before any JSON was
                    # emitted).
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                    http_options=types.HttpOptions(
                        timeout=int(self.request_timeout_seconds * 1000),
                        retry_options=types.HttpRetryOptions(attempts=self.max_retries + 1),
                    ),
                ),
            )
        except errors.ClientError as exc:
            raise LLMUnavailableError(f"client error {exc.code}: {exc.message}") from exc
        except errors.ServerError as exc:
            raise LLMUnavailableError(f"server error {exc.code}: {exc.message}") from exc
        except errors.APIError as exc:
            raise LLMUnavailableError(f"API error: {exc}") from exc

        if not response.candidates:
            raise LLMUnavailableError("no candidates returned (likely blocked before generation)")

        finish_reason = response.candidates[0].finish_reason
        if finish_reason in _REFUSAL_FINISH_REASONS:
            raise LLMUnavailableError(f"model declined the request: {finish_reason.value}")

        self._record_usage(response.usage_metadata)
        return response.text or ""

    def _record_usage(self, usage_metadata) -> None:
        input_tokens = getattr(usage_metadata, "prompt_token_count", None) or 0
        output_tokens = getattr(usage_metadata, "candidates_token_count", None) or 0
        with self._usage_lock:
            self.total_input_tokens += input_tokens
            self.total_output_tokens += output_tokens
            self.total_calls += 1
        logger.info(
            "llm_call_completed",
            extra={
                "extra_fields": {
                    "event": "llm_call_completed",
                    "model": self.model,
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                }
            },
        )

    def estimated_cost_usd(self) -> float:
        """Real cost incurred by this client instance so far, computed from
        actually-measured token counts (docs/economics.md) — not a guess."""
        return estimate_cost_usd(self.model, self.total_input_tokens, self.total_output_tokens)
