from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import find_dotenv, load_dotenv

_DEFAULTS: dict[str, Any] = {
    "github": {
        "api_base_url": "https://api.github.com",
        "request_timeout_seconds": 10,
        "max_retries": 3,
        "retry_backoff_seconds": [1, 2, 4],
    },
    "llm": {
        "model": "openai/gpt-4o",
        "temperature": 0.2,
        "max_tokens": 4096,
        "request_timeout_seconds": 30,
        "max_retries": 1,
        "requests_per_minute": 5,
    },
    "diff": {
        "max_diff_lines": 2000,
        "chunk_size_lines": 500,
    },
    "tools": {
        "enabled": ["pylint", "flake8", "bandit", "semgrep"],
        "timeout_seconds": 15,
    },
    "guardrail": {
        "secret_patterns_extra": [],
        "injection_patterns_extra": [],
    },
    "orchestrator": {
        "run_timeout_seconds": 300,
        "agent_timeout_seconds": 60,
    },
    "report": {
        "top_findings_limit": 5,
    },
    "job_store": {
        "retention_days": 30,
    },
    "logging": {
        "level": "INFO",
        "format": "json",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


@dataclass
class Config:
    raw: dict[str, Any] = field(default_factory=lambda: dict(_DEFAULTS))
    github_token: str | None = None
    # LLM access — one OpenAI-Chat-Completions-shaped endpoint (`openai`
    # SDK), reached via URL + bearer token + model name
    # (docs/specs/serving-config.md — "LLM endpoint"). The PoC's own
    # provider is Polza.ai, a unified proxy in front of ~400 underlying
    # models, but any endpoint speaking the same protocol works. All three
    # must be set together to take effect.
    llm_base_url: str | None = None
    llm_auth_token: str | None = None
    llm_model_override: str | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        # Filling in .env (docs/quickstart.md) does nothing by itself unless
        # something loads it into the process environment — this used to be
        # a manual `source .env` step nobody automated. Searches upward from
        # the current working directory; never overrides a variable that's
        # already set (e.g. from the real shell/deployment environment), so
        # this can't clobber intentional config.
        load_dotenv(find_dotenv(usecwd=True))

        data = dict(_DEFAULTS)
        if path is not None and Path(path).exists():
            with open(path, encoding="utf-8") as f:
                user_data = yaml.safe_load(f) or {}
            data = _deep_merge(data, user_data)
        return cls(
            raw=data,
            github_token=os.environ.get("GITHUB_TOKEN"),
            llm_base_url=os.environ.get("LLM_BASE_URL"),
            llm_auth_token=os.environ.get("LLM_AUTH_TOKEN"),
            llm_model_override=os.environ.get("LLM_MODEL"),
        )

    @property
    def has_llm_credentials(self) -> bool:
        return bool(self.llm_base_url and self.llm_auth_token)

    def resolved_llm_model(self, default: str) -> str:
        """LLM_MODEL env var wins (part of the URL/AUTH_TOKEN/MODEL trio for
        a custom endpoint) over config.yaml's llm.model, which wins over the
        hardcoded default."""
        return self.llm_model_override or self.get("llm", "model", default=default)

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node
