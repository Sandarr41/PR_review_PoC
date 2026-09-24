from dotenv import load_dotenv as real_load_dotenv

from pr_review_agent import config as config_module
from pr_review_agent.config import Config


def test_load_reads_llm_trio_from_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://polza.ai/api/v1")
    monkeypatch.setenv("LLM_AUTH_TOKEN", "proxy-token")
    monkeypatch.setenv("LLM_MODEL", "openai/gpt-4o")
    config = Config.load(None)
    assert config.llm_base_url == "https://polza.ai/api/v1"
    assert config.llm_auth_token == "proxy-token"
    assert config.llm_model_override == "openai/gpt-4o"
    assert config.has_llm_credentials is True


def test_no_credentials_means_no_llm_credentials(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_AUTH_TOKEN", raising=False)
    config = Config.load(None)
    assert config.has_llm_credentials is False


def test_base_url_without_token_is_not_enough(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://polza.ai/api/v1")
    monkeypatch.delenv("LLM_AUTH_TOKEN", raising=False)
    config = Config.load(None)
    assert config.has_llm_credentials is False


def test_token_without_base_url_is_not_enough(monkeypatch):
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("LLM_AUTH_TOKEN", "proxy-token")
    config = Config.load(None)
    assert config.has_llm_credentials is False


def test_resolved_llm_model_prefers_env_override(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "env-model")
    config = Config.load(None)
    assert config.resolved_llm_model(default="fallback-model") == "env-model"


def test_resolved_llm_model_falls_back_to_config_yaml_value(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    config = Config.load(None)
    config.raw["llm"]["model"] = "yaml-model"
    assert config.resolved_llm_model(default="fallback-model") == "yaml-model"


def test_resolved_llm_model_falls_back_to_hardcoded_default(monkeypatch):
    monkeypatch.delenv("LLM_MODEL", raising=False)
    config = Config.load(None)
    del config.raw["llm"]["model"]
    assert config.resolved_llm_model(default="fallback-model") == "fallback-model"


# --- .env auto-loading -------------------------------------------------
#
# The bug these two guard against: filling in `.env` and running the CLI
# without first manually `source .env`-ing it into the shell used to
# silently do nothing — Config never saw the values, and the LLM was never
# called, with no obvious error pointing at why. Every other test in this
# file runs with real dotenv loading stubbed out (tests/conftest.py,
# `_no_real_dotenv`) so it can't pick up this repo's own real .env; these
# two restore the real function to test the loading mechanism itself.

def test_env_file_is_loaded_automatically_without_manual_sourcing(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "load_dotenv", real_load_dotenv)
    monkeypatch.delenv("LLM_AUTH_TOKEN", raising=False)
    (tmp_path / ".env").write_text("LLM_AUTH_TOKEN=from-dotenv-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = Config.load(None)

    assert config.llm_auth_token == "from-dotenv-file"


def test_env_file_does_not_override_an_already_set_variable(tmp_path, monkeypatch):
    monkeypatch.setattr(config_module, "load_dotenv", real_load_dotenv)
    monkeypatch.setenv("LLM_AUTH_TOKEN", "from-real-shell-env")
    (tmp_path / ".env").write_text("LLM_AUTH_TOKEN=from-dotenv-file\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    config = Config.load(None)

    assert config.llm_auth_token == "from-real-shell-env"
