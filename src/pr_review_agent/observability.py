"""Observability & Eval layer — logging (docs/specs/observability-evals.md).

Structured logs matching the shape in docs/governance.md § 2: timestamp,
event, job_id, agents_used, tools_used, status, duration_ms. Never logs
full PR code, secrets, tokens, or raw LLM prompts.
"""
from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import contextmanager


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        extra = getattr(record, "extra_fields", None)
        if extra:
            payload.update(extra)
        return json.dumps(payload, ensure_ascii=False)


def get_logger(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger("pr_review_agent")
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger


def log_event(logger: logging.Logger, event: str, **fields) -> None:
    logger.info(event, extra={"extra_fields": {"event": event, **fields}})


@contextmanager
def timed_step(logger: logging.Logger, step: str, **fields):
    start = time.monotonic()
    try:
        yield
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        log_event(logger, f"step_completed:{step}", duration_ms=duration_ms, **fields)
