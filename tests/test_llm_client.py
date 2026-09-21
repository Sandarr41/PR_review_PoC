import pytest
from google.genai import errors, types

from pr_review_agent.llm_client import LLMClient, LLMUnavailableError


class _FakeCandidate:
    def __init__(self, finish_reason):
        self.finish_reason = finish_reason


class _FakeResponse:
    def __init__(self, text="ok", finish_reason=types.FinishReason.STOP, candidates=None):
        self.text = text
        self.candidates = candidates if candidates is not None else [_FakeCandidate(finish_reason)]


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
