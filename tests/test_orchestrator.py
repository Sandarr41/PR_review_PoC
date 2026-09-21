from pathlib import Path

from pr_review_agent.config import Config
from pr_review_agent.job_store import InMemoryJobStore
from pr_review_agent.models import JobStatus
from pr_review_agent.orchestrator import run_pipeline


class FakeLLMClient:
    """Stands in for LLMClient — every agent gets the same canned finding."""

    def complete(self, system_prompt: str, user_content: str) -> str:
        return (
            '[{"file": "user_service.py", "line": 5, "severity": "high", '
            '"message": "possible SQL injection via string concatenation"}]'
        )


def _make_job(pr_ref="test/pr#1"):
    store = InMemoryJobStore()
    job = store.create(pr_ref)
    return store, job


def test_pipeline_completes_with_llm_available(tmp_path, sample_diff_text):
    store, job = _make_job()
    config = Config.load(None)

    result = run_pipeline(
        pr_ref="test/pr#1",
        diff_text=sample_diff_text,
        job=job,
        job_store=store,
        config=config,
        repo_root=tmp_path,  # empty checkout -> tools skipped, retriever finds nothing extra
        llm_client=FakeLLMClient(),
    )

    assert result.job.status == JobStatus.COMPLETED
    assert len(result.findings) > 0
    assert "Potential Bugs" in result.report_markdown or "Security" in result.report_markdown
    assert set(result.job.agents_used) == {
        "bug_agent",
        "security_agent",
        "quality_agent",
        "test_coverage_agent",
    }


def test_pipeline_falls_back_when_llm_unavailable(tmp_path, sample_diff_text):
    store, job = _make_job()
    config = Config.load(None)

    result = run_pipeline(
        pr_ref="test/pr#1",
        diff_text=sample_diff_text,
        job=job,
        job_store=store,
        config=config,
        repo_root=tmp_path,
        llm_client=None,
    )

    assert result.job.status == JobStatus.COMPLETED_PARTIAL
    assert "LLM reasoning was unavailable" in result.report_markdown
    assert result.job.agents_used == []


def test_pipeline_masks_secret_before_it_would_reach_llm(tmp_path, sample_diff_with_secret_text):
    store, job = _make_job()
    config = Config.load(None)

    captured_user_content = []

    class CapturingLLMClient(FakeLLMClient):
        def complete(self, system_prompt, user_content):
            captured_user_content.append(user_content)
            return super().complete(system_prompt, user_content)

    run_pipeline(
        pr_ref="test/pr#2",
        diff_text=sample_diff_with_secret_text,
        job=job,
        job_store=store,
        config=config,
        repo_root=tmp_path,
        llm_client=CapturingLLMClient(),
    )

    assert captured_user_content, "agents should have been invoked"
    for content in captured_user_content:
        assert "sk-abcd" not in content
        assert "ignore previous instructions" not in content.lower()


def test_pipeline_publishes_report_when_publish_fn_given(tmp_path, sample_diff_text):
    store, job = _make_job()
    config = Config.load(None)
    published = []

    run_pipeline(
        pr_ref="test/pr#3",
        diff_text=sample_diff_text,
        job=job,
        job_store=store,
        config=config,
        repo_root=tmp_path,
        llm_client=FakeLLMClient(),
        publish_fn=published.append,
    )

    assert len(published) == 1
    assert "PR Review Summary" in published[0]


def test_pipeline_chunks_large_diff(tmp_path):
    store, job = _make_job()
    config = Config.load(None)
    config.raw["diff"]["max_diff_lines"] = 1
    config.raw["diff"]["chunk_size_lines"] = 1

    diff_text = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n+++ b/a.py\n@@ -0,0 +1,2 @@\n+x = 1\n+y = 2\n"
    )

    result = run_pipeline(
        pr_ref="test/pr#4",
        diff_text=diff_text,
        job=job,
        job_store=store,
        config=config,
        repo_root=tmp_path,
        llm_client=FakeLLMClient(),
    )

    assert "exceeds 1 lines" in result.report_markdown
