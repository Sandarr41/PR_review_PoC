"""PR Ingestion Module + Report Publisher (docs/specs/tools-api.md — GitHub API).

Read-only for the repository (pull_requests:read) plus posting/updating one
review comment (pull_requests:write) — no push/merge rights
(docs/governance.md § 5, Human-in-the-loop).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass

import requests

from .report import MARKER

_PR_URL_RE = re.compile(r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)")


class GitHubAPIError(Exception):
    """Raised after retries are exhausted or on a non-retryable 4xx."""


@dataclass(frozen=True)
class PRRef:
    owner: str
    repo: str
    number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


def parse_pr_url(url: str) -> PRRef:
    m = _PR_URL_RE.search(url)
    if not m:
        raise ValueError(f"not a recognizable GitHub PR URL: {url}")
    return PRRef(owner=m.group("owner"), repo=m.group("repo"), number=int(m.group("number")))


@dataclass
class GitHubClient:
    token: str | None = None
    api_base_url: str = "https://api.github.com"
    timeout_seconds: float = 10.0
    max_retries: int = 3
    retry_backoff_seconds: tuple[float, ...] = (1.0, 2.0, 4.0)

    def _headers(self, accept: str = "application/vnd.github+json") -> dict[str, str]:
        headers = {"Accept": accept}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request_with_retry(self, method: str, url: str, **kwargs) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = requests.request(method, url, timeout=self.timeout_seconds, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if response.status_code < 500 and response.status_code != 429:
                    return response  # 2xx/3xx/4xx (non-retryable) returned as-is
                last_exc = GitHubAPIError(f"{response.status_code}: {response.text[:300]}")

            if attempt < self.max_retries - 1:
                delay = self.retry_backoff_seconds[min(attempt, len(self.retry_backoff_seconds) - 1)]
                time.sleep(delay)

        raise GitHubAPIError(f"GitHub API request failed after {self.max_retries} attempts: {last_exc}")

    def get_pr_diff(self, pr: PRRef) -> str:
        url = f"{self.api_base_url}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}"
        response = self._request_with_retry("GET", url, headers=self._headers("application/vnd.github.v3.diff"))
        if response.status_code == 404:
            raise GitHubAPIError(f"PR not found or not accessible: {pr.slug}")
        if response.status_code >= 400:
            raise GitHubAPIError(f"failed to fetch diff for {pr.slug}: {response.status_code}")
        return response.text

    def get_pr_metadata(self, pr: PRRef) -> dict:
        url = f"{self.api_base_url}/repos/{pr.owner}/{pr.repo}/pulls/{pr.number}"
        response = self._request_with_retry("GET", url, headers=self._headers())
        if response.status_code == 404:
            raise GitHubAPIError(f"PR not found or not accessible: {pr.slug}")
        if response.status_code >= 400:
            raise GitHubAPIError(f"failed to fetch metadata for {pr.slug}: {response.status_code}")
        return response.json()

    def publish_comment(self, pr: PRRef, body: str) -> None:
        """Publishes the report as an issue comment on the PR, idempotently —
        a rerun updates the existing marked comment instead of creating a
        new one (docs/specs/tools-api.md — GitHub API, side effects)."""
        existing_id = self._find_existing_comment_id(pr)
        if existing_id is not None:
            url = f"{self.api_base_url}/repos/{pr.owner}/{pr.repo}/issues/comments/{existing_id}"
            response = self._request_with_retry("PATCH", url, headers=self._headers(), json={"body": body})
        else:
            url = f"{self.api_base_url}/repos/{pr.owner}/{pr.repo}/issues/{pr.number}/comments"
            response = self._request_with_retry("POST", url, headers=self._headers(), json={"body": body})

        if response.status_code >= 400:
            raise GitHubAPIError(f"failed to publish comment on {pr.slug}: {response.status_code}")

    def _find_existing_comment_id(self, pr: PRRef) -> int | None:
        url = f"{self.api_base_url}/repos/{pr.owner}/{pr.repo}/issues/{pr.number}/comments"
        response = self._request_with_retry("GET", url, headers=self._headers())
        if response.status_code >= 400:
            return None
        for comment in response.json():
            if MARKER in comment.get("body", ""):
                return comment["id"]
        return None
