import time

import pytest
from fastapi.testclient import TestClient

from pr_review_agent import gateway
from pr_review_agent.github_client import GitHubAPIError

SAMPLE_DIFF = (
    "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+x = 1\n"
)


class _FakeGitHubClient:
    """Stands in for GitHubClient so tests never hit the network."""

    result: str | Exception = SAMPLE_DIFF

    def __init__(self, *args, **kwargs):
        pass

    def get_pr_diff(self, pr):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def publish_comment(self, pr, body):
        pass


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(gateway, "GitHubClient", _FakeGitHubClient)
    # Force tools-only fallback, no live LLM. gateway.config is a module-level
    # singleton built at import time (before any fixture can run) via
    # Config.load(), which now auto-loads .env (config.py) — so whatever
    # real credentials happen to be in this repo's .env are already baked in
    # by the time this fixture runs. Both fields must be cleared, or
    # has_llm_credentials stays True and the test hits a real network call.
    monkeypatch.setattr(gateway.config, "llm_base_url", None)
    monkeypatch.setattr(gateway.config, "llm_auth_token", None)
    return TestClient(gateway.app)


def _poll_until_done(client, job_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/jobs/{job_id}")
        if resp.json()["status"] not in ("queued", "running"):
            return resp.json()
        time.sleep(0.05)
    raise TimeoutError(f"job {job_id} did not finish in time")


def test_analyze_returns_202_immediately(client, tmp_path):
    _FakeGitHubClient.result = SAMPLE_DIFF
    response = client.post(
        "/analyze",
        json={"pr_url": "https://github.com/o/r/pull/1", "repo_root": str(tmp_path)},
    )
    assert response.status_code == 202
    body = response.json()
    assert "job_id" in body
    assert body["status"] in ("queued", "running")


def test_analyze_rejects_malformed_pr_url(client, tmp_path):
    response = client.post("/analyze", json={"pr_url": "not-a-github-url", "repo_root": str(tmp_path)})
    assert response.status_code == 400


def test_full_ack_then_poll_cycle_completes(client, tmp_path):
    _FakeGitHubClient.result = SAMPLE_DIFF
    response = client.post(
        "/analyze",
        json={"pr_url": "https://github.com/o/r/pull/2", "repo_root": str(tmp_path)},
    )
    job_id = response.json()["job_id"]
    final = _poll_until_done(client, job_id)
    assert final["status"] in ("completed", "completed_partial")
    assert final["report_markdown"] is not None


def test_ack_succeeds_even_if_github_fetch_later_fails(client, tmp_path):
    """The whole point of submit_deferred: a GitHub failure must show up as a
    FAILED job on poll, not as a failed ack — the ack path does no I/O."""
    _FakeGitHubClient.result = GitHubAPIError("PR not found")
    response = client.post(
        "/analyze",
        json={"pr_url": "https://github.com/o/r/pull/3", "repo_root": str(tmp_path)},
    )
    assert response.status_code == 202  # ack succeeds regardless
    job_id = response.json()["job_id"]
    final = _poll_until_done(client, job_id)
    assert final["status"] == "failed"
    assert "PR not found" in final["error"]


def test_get_unknown_job_returns_404(client):
    response = client.get("/jobs/does-not-exist")
    assert response.status_code == 404
