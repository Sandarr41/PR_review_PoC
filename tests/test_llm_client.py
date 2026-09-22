import pytest
from google.genai import errors, types

from pr_review_agent.llm_client import LLMClient, LLMUnavailableError


class _FakeCandidate:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason


class _FakeUsage:
    def __init__(self, prompt_token_count=10, candidates_token_count=5):
        self.prompt_token_count = prompt_token_count
        self.candidates_token_count = candidates_token_count


class _FakeResponse:
    def __init__(self, text="ok", finish_reason=types.FinishReason.STOP, candidates=None, usage_metadata=None):
        self.text = text
        self.candidates = candidates if candidates is not None else [_FakeCandidate(finish_reason)]
        self.usage_metadata = usage_metadata if usage_metadata is not None else _FakeUsage()


class _FakeModels:
    def __init__(self, result):
        self._result = result

    def generate_content(self, **kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeGenaiClient:
    def __init__(self, result):
        self.models = _FakeModels(result)


@pytest.fixture
def llm_client():
    return LLMClient(api_key="test-key")


def test_complete_returns_text_from_response(llm_client):
    llm_client._client = _FakeGenaiClient(_FakeResponse(text="[]"))
    assert llm_client.complete("system", "user") == "[]"


def test_complete_raises_on_safety_block(llm_client):
    llm_client._client = _FakeGenaiClient(
        _FakeResponse(text=None, finish_reason=types.FinishReason.SAFETY)
    )
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")


def test_complete_raises_when_no_candidates(llm_client):
    llm_client._client = _FakeGenaiClient(_FakeResponse(candidates=[]))
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")


def test_complete_raises_on_client_error(llm_client):
    llm_client._client = _FakeGenaiClient(errors.ClientError(429, {"message": "rate limited"}))
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")


def test_complete_raises_on_server_error(llm_client):
    llm_client._client = _FakeGenaiClient(errors.ServerError(503, {"message": "unavailable"}))
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")


def test_complete_returns_empty_string_when_text_is_none_but_stop(llm_client):
    llm_client._client = _FakeGenaiClient(_FakeResponse(text=None, finish_reason=types.FinishReason.STOP))
    assert llm_client.complete("system", "user") == ""


def test_complete_records_real_token_usage(llm_client):
    llm_client._client = _FakeGenaiClient(
        _FakeResponse(text="[]", usage_metadata=_FakeUsage(prompt_token_count=120, candidates_token_count=30))
    )
    llm_client.complete("system", "user")
    assert llm_client.total_input_tokens == 120
    assert llm_client.total_output_tokens == 30
    assert llm_client.total_calls == 1


def test_usage_accumulates_across_multiple_calls(llm_client):
    llm_client._client = _FakeGenaiClient(
        _FakeResponse(text="[]", usage_metadata=_FakeUsage(prompt_token_count=100, candidates_token_count=20))
    )
    llm_client.complete("system", "user1")
    llm_client.complete("system", "user2")
    assert llm_client.total_input_tokens == 200
    assert llm_client.total_output_tokens == 40
    assert llm_client.total_calls == 2


def test_estimated_cost_usd_reflects_recorded_usage(llm_client):
    llm_client.model = "gemini-3.6-flash"
    llm_client._client = _FakeGenaiClient(
        _FakeResponse(text="[]", usage_metadata=_FakeUsage(prompt_token_count=1_000_000, candidates_token_count=1_000_000))
    )
    llm_client.complete("system", "user")
    assert llm_client.estimated_cost_usd() == pytest.approx(0.75 + 3.75)


def test_failed_call_does_not_record_usage(llm_client):
    llm_client._client = _FakeGenaiClient(errors.ServerError(503, {"message": "unavailable"}))
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")
    assert llm_client.total_calls == 0
