from pathlib import Path

import pytest

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_diff_text() -> str:
    return (FIXTURES_DIR / "sample.diff").read_text(encoding="utf-8")


@pytest.fixture
def sample_diff_with_secret_text() -> str:
    return (FIXTURES_DIR / "sample_with_secret.diff").read_text(encoding="utf-8")
