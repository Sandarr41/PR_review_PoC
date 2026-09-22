import threading
import time
from pathlib import Path

from pr_review_agent.config import Config
from pr_review_agent.models import JobStatus
from pr_review_agent.orchestrator import JobManager


class FakeLLMClient:
    def complete(self, system_prompt: str, user_content: str) -> str:
        return "[]"


def _wait_until(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_submit_returns_before_pipeline_finishes(tmp_path):
    manager = JobManager(Config.load(None), max_workers=2)
    try:
        job = manager.submit(
            pr_ref="test/pr#1",
            diff_text="diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+x = 1\n",
            repo_root=tmp_path,
            llm_client=FakeLLMClient(),
        )
        assert job.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.COMPLETED)
        assert _wait_until(lambda: manager.get_job(job.job_id).status != JobStatus.QUEUED)
    finally:
        manager.shutdown(wait=True)


def test_submit_deferred_does_not_call_fetch_diff_before_returning(tmp_path):
    manager = JobManager(Config.load(None), max_workers=2)
    fetch_started = threading.Event()
    release_fetch = threading.Event()

    def fetch_diff():
        fetch_started.set()
        release_fetch.wait(timeout=5.0)
        return "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,1 @@\n+x = 1\n"

    try:
        job = manager.submit_deferred(
            pr_ref="test/pr#2",
            fetch_diff=fetch_diff,
            repo_root=tmp_path,
            llm_client=FakeLLMClient(),
        )
        # submit_deferred must return without waiting on fetch_diff.
        assert job.job_id is not None
        release_fetch.set()
        assert _wait_until(fetch_started.is_set)
        assert _wait_until(lambda: manager.get_job(job.job_id).status == JobStatus.COMPLETED)
    finally:
        manager.shutdown(wait=True)


def test_submit_deferred_marks_job_failed_when_fetch_diff_raises(tmp_path):
    manager = JobManager(Config.load(None), max_workers=2)

    def fetch_diff():
        raise RuntimeError("GitHub is down")

    try:
        job = manager.submit_deferred(
            pr_ref="test/pr#3",
            fetch_diff=fetch_diff,
            repo_root=tmp_path,
            llm_client=FakeLLMClient(),
        )
        assert _wait_until(lambda: manager.get_job(job.job_id).status == JobStatus.FAILED)
        assert "GitHub is down" in manager.get_job(job.job_id).error
    finally:
        manager.shutdown(wait=True)
