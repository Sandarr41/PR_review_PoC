from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_real_dotenv(monkeypatch):
    """Config.load() auto-loads .env (see config.py) — without this, every
    test run would pick up whatever real credentials/proxy settings happen
    to be sitting in this repo's own .env, making tests non-deterministic
    and prone to slow/failing background network calls. Tests that need
    credentials must set them explicitly via monkeypatch.setenv."""
    monkeypatch.setattr("pr_review_agent.config.load_dotenv", lambda *a, **k: None)


@pytest.fixture
def sample_diff_text() -> str:
    return (FIXTURES_DIR / "sample.diff").read_text(encoding="utf-8")


@pytest.fixture
def sample_diff_with_secret_text() -> str:
    return (FIXTURES_DIR / "sample_with_secret.diff").read_text(encoding="utf-8")
