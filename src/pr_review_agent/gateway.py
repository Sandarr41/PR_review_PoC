"""API / Webhook Gateway (docs/system-design.md § 2, docs/diagrams/c4-container.md).

Minimal async HTTP surface demonstrating the ack-then-poll model from
docs/system-design.md § 1: POST /analyze returns a job_id immediately
(status=queued); GET /jobs/{id} polls for the report once it's ready.

Optional — the CLI (cli.py) is the primary demo path and has no dependency
on fastapi/uvicorn. Run with: uvicorn pr_review_agent.gateway:app --reload
"""
from __future__ import annotations

from pathlib import Path

try:
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
except ImportError as exc:  # pragma: no cover - optional dependency
    raise ImportError(
        "The API gateway needs fastapi + pydantic: pip install fastapi uvicorn"
    ) from exc

from .config import Config
from .github_client import GitHubClient, parse_pr_url
from .llm_client import LLMClient
from .orchestrator import JobManager

config = Config.load("config/config.yaml" if Path("config/config.yaml").exists() else None)
job_manager = JobManager(config)
app = FastAPI(title="PR Review Agent — API Gateway")


class AnalyzeRequest(BaseModel):
    pr_url: str
    repo_root: str = "."
    publish: bool = False


class JobResponse(BaseModel):
    job_id: str
    status: str


@app.post("/analyze", response_model=JobResponse, status_code=202)
def analyze(request: AnalyzeRequest) -> JobResponse:
    # Only synchronous work on the ack path: pure input validation (point of
    # control #1, system-design.md § 9) — no network I/O. The actual GitHub
    # fetch happens inside submit_deferred()'s background thread, so a slow
    # or unreachable GitHub never delays the ack (system-design.md § 1: "ack
    # сразу, не дожидаясь завершения анализа" — this includes PR ingestion,
    # not just the LLM call). An earlier version of this handler fetched the
    # diff here, synchronously, before creating the job — measured at ~1s of
    # added ack latency against a real PR; see docs/demo-report.md.
    try:
        pr = parse_pr_url(request.pr_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    github_client = GitHubClient(
        token=config.github_token,
        api_base_url=config.get("github", "api_base_url", default="https://api.github.com"),
        timeout_seconds=config.get("github", "request_timeout_seconds", default=10),
        max_retries=config.get("github", "max_retries", default=3),
        retry_backoff_seconds=tuple(config.get("github", "retry_backoff_seconds", default=[1, 2, 4])),
    )

    llm_client = (
        LLMClient(
            model=config.resolved_llm_model(default="openai/gpt-4o"),
            temperature=config.get("llm", "temperature", default=0.2),
            max_tokens=config.get("llm", "max_tokens", default=4096),
            request_timeout_seconds=config.get("llm", "request_timeout_seconds", default=30),
            max_retries=config.get("llm", "max_retries", default=1),
            requests_per_minute=config.get("llm", "requests_per_minute", default=5),
            api_key=config.llm_auth_token,
            base_url=config.llm_base_url,
        )
        if config.has_llm_credentials
        else None
    )

    publish_fn = (lambda text: github_client.publish_comment(pr, text)) if request.publish else None

    job = job_manager.submit_deferred(
        pr_ref=pr.slug,
        fetch_diff=lambda: github_client.get_pr_diff(pr),
        repo_root=Path(request.repo_root).resolve(),
        llm_client=llm_client,
        publish_fn=publish_fn,
    )
    return JobResponse(job_id=job.job_id, status=job.status.value)


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = job_manager.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "job_id": job.job_id,
        "pr_ref": job.pr_ref,
        "status": job.status.value,
        "agents_used": job.agents_used,
        "tools_used": job.tools_used,
        "duration_ms": job.duration_ms,
        "llm_input_tokens": job.llm_input_tokens,
        "llm_output_tokens": job.llm_output_tokens,
        "llm_cost_rub": job.llm_cost_rub,
        "error": job.error,
        "report_markdown": job.report_markdown,
    }
