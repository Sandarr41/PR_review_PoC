import httpx2
import openai
import pytest

from pr_review_agent.llm_client import LLMClient, LLMUnavailableError


class _FakeMessage:
    def __init__(self, content="ok", refusal=None):
        self.content = content
        self.refusal = refusal


class _FakeChoice:
    def __init__(self, message=None, finish_reason="stop"):
        self.message = message if message is not None else _FakeMessage()
        self.finish_reason = finish_reason


class _FakeUsage:
    def __init__(self, prompt_tokens=10, completion_tokens=5, cost_rub=None, cost=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        if cost_rub is not None:
            self.cost_rub = cost_rub
        if cost is not None:
            self.cost = cost


class _FakeResponse:
    def __init__(self, choices=None, usage=None):
        self.choices = choices if choices is not None else [_FakeChoice()]
        self.usage = usage if usage is not None else _FakeUsage()


class _FakeCompletions:
    def __init__(self, result):
        self._result = result
        self.last_call_kwargs = None

    def create(self, **kwargs):
        self.last_call_kwargs = kwargs
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeChat:
    def __init__(self, result):
        self.completions = _FakeCompletions(result)


class _FakeOpenAIClient:
    def __init__(self, result):
        self.chat = _FakeChat(result)


def _status_error(status_code, message):
    request = httpx2.Request("POST", "https://example.com")
    response = httpx2.Response(status_code, request=request)
    return openai.APIStatusError(message, response=response, body=None)


@pytest.fixture
def llm_client():
    return LLMClient(api_key="test-key", base_url="https://polza.ai/api/v1")


def test_complete_returns_text_from_response(llm_client):
    llm_client._client = _FakeOpenAIClient(_FakeResponse(choices=[_FakeChoice(_FakeMessage(content="[]"))]))
    assert llm_client.complete("system", "user") == "[]"


def test_complete_sends_system_and_user_messages(llm_client):
    fake_client = _FakeOpenAIClient(_FakeResponse())
    llm_client._client = fake_client
    llm_client.complete("system prompt", "user content")
    messages = fake_client.chat.completions.last_call_kwargs["messages"]
    assert messages == [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "user content"},
    ]


def test_complete_raises_when_no_choices(llm_client):
    llm_client._client = _FakeOpenAIClient(_FakeResponse(choices=[]))
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")


def test_complete_raises_on_content_filter(llm_client):
    llm_client._client = _FakeOpenAIClient(
        _FakeResponse(choices=[_FakeChoice(_FakeMessage(content=None), finish_reason="content_filter")])
    )
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")


def test_complete_raises_on_refusal_field(llm_client):
    llm_client._client = _FakeOpenAIClient(
        _FakeResponse(choices=[_FakeChoice(_FakeMessage(content=None, refusal="unsafe request"))])
    )
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")


def test_complete_raises_on_status_error(llm_client):
    llm_client._client = _FakeOpenAIClient(_status_error(503, "unavailable"))
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")


def test_complete_raises_on_connection_error(llm_client):
    request = httpx2.Request("POST", "https://example.com")
    llm_client._client = _FakeOpenAIClient(openai.APIConnectionError(message="boom", request=request))
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")


def test_complete_returns_empty_string_when_content_is_none_but_stop(llm_client):
    llm_client._client = _FakeOpenAIClient(
        _FakeResponse(choices=[_FakeChoice(_FakeMessage(content=None), finish_reason="stop")])
    )
    assert llm_client.complete("system", "user") == ""


def test_complete_records_real_token_usage(llm_client):
    llm_client._client = _FakeOpenAIClient(
        _FakeResponse(usage=_FakeUsage(prompt_tokens=120, completion_tokens=30))
    )
    llm_client.complete("system", "user")
    assert llm_client.total_input_tokens == 120
    assert llm_client.total_output_tokens == 30
    assert llm_client.total_calls == 1


def test_usage_accumulates_across_multiple_calls(llm_client):
    llm_client._client = _FakeOpenAIClient(
        _FakeResponse(usage=_FakeUsage(prompt_tokens=100, completion_tokens=20))
    )
    llm_client.complete("system", "user1")
    llm_client.complete("system", "user2")
    assert llm_client.total_input_tokens == 200
    assert llm_client.total_output_tokens == 40
    assert llm_client.total_calls == 2


def test_failed_call_does_not_record_usage(llm_client):
    llm_client._client = _FakeOpenAIClient(_status_error(503, "unavailable"))
    with pytest.raises(LLMUnavailableError):
        llm_client.complete("system", "user")
    assert llm_client.total_calls == 0


# --- Real per-call cost (Polza.ai's OpenAI-shape extension) ---------------
#
# Polza (and OpenRouter-style aggregators generally) return the actual
# billed cost on usage.cost_rub / usage.cost — an extra field beyond the
# stock OpenAI schema. We read it directly instead of maintaining a static
# per-model price table, which isn't feasible across an aggregator's ~400
# models. openai's response models declare extra="allow" (verified against
# the installed SDK), so unknown fields survive as attributes.

def test_estimated_cost_rub_reflects_provider_reported_cost(llm_client):
    llm_client._client = _FakeOpenAIClient(_FakeResponse(usage=_FakeUsage(cost_rub=1.23)))
    llm_client.complete("system", "user")
    assert llm_client.estimated_cost_rub() == pytest.approx(1.23)


def test_estimated_cost_rub_falls_back_to_cost_field_when_cost_rub_absent(llm_client):
    llm_client._client = _FakeOpenAIClient(_FakeResponse(usage=_FakeUsage(cost=0.5)))
    llm_client.complete("system", "user")
    assert llm_client.estimated_cost_rub() == pytest.approx(0.5)


def test_estimated_cost_rub_is_zero_when_provider_reports_no_cost(llm_client):
    llm_client._client = _FakeOpenAIClient(_FakeResponse(usage=_FakeUsage()))
    llm_client.complete("system", "user")
    assert llm_client.estimated_cost_rub() == 0.0


def test_cost_accumulates_across_multiple_calls(llm_client):
    llm_client._client = _FakeOpenAIClient(_FakeResponse(usage=_FakeUsage(cost_rub=2.0)))
    llm_client.complete("system", "user1")
    llm_client.complete("system", "user2")
    assert llm_client.estimated_cost_rub() == pytest.approx(4.0)


def test_client_is_configured_with_api_key_and_base_url():
    client = LLMClient(api_key="proxy-token", base_url="https://polza.ai/api/v1")
    assert client._client.api_key == "proxy-token"
    assert str(client._client.base_url).rstrip("/") == "https://polza.ai/api/v1"
