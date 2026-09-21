import pytest

from pr_review_agent import github_client as gh_module
from pr_review_agent.github_client import GitHubAPIError, GitHubClient, PRRef, parse_pr_url
from pr_review_agent.report import MARKER


class _FakeResponse:
    def __init__(self, status_code=200, text="", json_data=None):
        self.status_code = status_code
        self.text = text
        self._json_data = json_data if json_data is not None else {}

    def json(self):
        return self._json_data


def test_parse_pr_url_extracts_owner_repo_number():
    pr = parse_pr_url("https://github.com/octocat/hello-world/pull/42")
    assert pr == PRRef(owner="octocat", repo="hello-world", number=42)


def test_parse_pr_url_rejects_non_pr_url():
    with pytest.raises(ValueError):
        parse_pr_url("https://github.com/octocat/hello-world")


def test_get_pr_diff_returns_text_on_success(monkeypatch):
    def fake_request(method, url, **kwargs):
        assert method == "GET"
        return _FakeResponse(status_code=200, text="diff --git a/x b/x\n")

    monkeypatch.setattr(gh_module.requests, "request", fake_request)
    client = GitHubClient(token="t")
    result = client.get_pr_diff(PRRef("o", "r", 1))
    assert "diff --git" in result


def test_get_pr_diff_raises_on_404(monkeypatch):
    monkeypatch.setattr(gh_module.requests, "request", lambda *a, **k: _FakeResponse(status_code=404))
    client = GitHubClient(token="t")
    with pytest.raises(GitHubAPIError):
        client.get_pr_diff(PRRef("o", "r", 1))


def test_get_pr_diff_retries_on_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr(gh_module.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def fake_request(method, url, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            return _FakeResponse(status_code=503, text="server error")
        return _FakeResponse(status_code=200, text="ok diff")

    monkeypatch.setattr(gh_module.requests, "request", fake_request)
    client = GitHubClient(token="t", max_retries=3)
    result = client.get_pr_diff(PRRef("o", "r", 1))
    assert result == "ok diff"
    assert calls["n"] == 2


def test_get_pr_diff_fails_after_exhausting_retries(monkeypatch):
    monkeypatch.setattr(gh_module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(gh_module.requests, "request", lambda *a, **k: _FakeResponse(status_code=503))
    client = GitHubClient(token="t", max_retries=2)
    with pytest.raises(GitHubAPIError):
        client.get_pr_diff(PRRef("o", "r", 1))


def test_publish_comment_creates_new_when_none_exists(monkeypatch):
    posted = {}

    def fake_request(method, url, **kwargs):
        if method == "GET":
            return _FakeResponse(status_code=200, json_data=[])
        if method == "POST":
            posted["body"] = kwargs["json"]["body"]
            return _FakeResponse(status_code=201)
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(gh_module.requests, "request", fake_request)
    client = GitHubClient(token="t")
    client.publish_comment(PRRef("o", "r", 1), f"{MARKER}\nhello")
    assert posted["body"] == f"{MARKER}\nhello"


def test_publish_comment_updates_existing_marked_comment(monkeypatch):
    patched = {}

    def fake_request(method, url, **kwargs):
        if method == "GET":
            return _FakeResponse(status_code=200, json_data=[{"id": 99, "body": f"{MARKER}\nold"}])
        if method == "PATCH":
            patched["url"] = url
            patched["body"] = kwargs["json"]["body"]
            return _FakeResponse(status_code=200)
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(gh_module.requests, "request", fake_request)
    client = GitHubClient(token="t")
    client.publish_comment(PRRef("o", "r", 1), f"{MARKER}\nnew")
    assert patched["url"].endswith("/comments/99")
    assert patched["body"] == f"{MARKER}\nnew"
