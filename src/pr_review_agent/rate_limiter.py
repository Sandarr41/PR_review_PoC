"""Client-side rate limiter — the "Circuit Breaker / Rate Limiter" control
point named in docs/diagrams/c4-component.md, applied ahead of every LLM
call in ``llm_client.py``.

Motivated by an empirically observed failure, not a hypothetical one: the
Gemini free tier returned ``429 RESOURCE_EXHAUSTED`` under this project's
own 4-parallel-agents-per-chunk design (see eval/README.md, "Operational
finding"). This throttles client-side *before* sending a request, instead
of only reacting to a 429 after the fact.
"""
from __future__ import annotations

import threading
import time


class RateLimiter:
    """Sliding-window rate limiter: at most ``max_calls`` calls are allowed
    to proceed within any ``period_seconds`` window. ``acquire()`` blocks
    the calling thread until a slot is free — safe to share across the
    ThreadPoolExecutor workers that run agents in parallel
    (docs/specs/agent-orchestrator.md)."""

    def __init__(self, max_calls: int, period_seconds: float = 60.0):
        if max_calls <= 0:
            raise ValueError("max_calls must be positive")
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._lock = threading.Lock()
        self._call_times: list[float] = []

    def acquire(self) -> float:
        """Blocks until a call is permitted. Returns the seconds spent waiting
        (0.0 if a slot was immediately available) — surfaced by the caller
        for observability (docs/specs/observability-evals.md)."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                self._call_times = [t for t in self._call_times if now - t < self.period_seconds]
                if len(self._call_times) < self.max_calls:
                    self._call_times.append(now)
                    return waited
                sleep_for = self.period_seconds - (now - self._call_times[0]) + 0.05

            time.sleep(sleep_for)
            waited += sleep_for
