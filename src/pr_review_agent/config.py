from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_DEFAULTS: dict[str, Any] = {
    "github": {
        "api_base_url": "https://api.github.com",
        "request_timeout_seconds": 10,
        "max_retries": 3,
        "retry_backoff_seconds": [1, 2, 4],
    },
    "llm": {
        "model": "gemini-3.6-flash",
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
    google_api_key: str | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Config":
        data = dict(_DEFAULTS)
        if path is not None and Path(path).exists():
            with open(path, encoding="utf-8") as f:
                user_data = yaml.safe_load(f) or {}
            data = _deep_merge(data, user_data)
        return cls(
            raw=data,
            github_token=os.environ.get("GITHUB_TOKEN"),
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
        )

    @property
    def has_llm_credentials(self) -> bool:
        return bool(self.google_api_key)

    def get(self, *keys: str, default: Any = None) -> Any:
        node: Any = self.raw
        for key in keys:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node
