"""Orchestrator / Agent Scheduler (docs/specs/agent-orchestrator.md,
docs/diagrams/workflow.md).

Coordinates the full pipeline: parse -> guardrail -> retrieve -> tools
(parallel) -> agents (parallel) -> aggregate -> report -> output guardrail
-> publish. Implements the failure modes/fallbacks from
docs/system-design.md § 7.
"""
from __future__ import annotations

import dataclasses
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Optional

from . import guardrail
from .agents import ALL_AGENTS, AgentContext
from .aggregator import aggregate
from .config import Config
from .diff_parser import chunk_diff, parse_diff, total_changed_lines
from .job_store import InMemoryJobStore
from .llm_client import LLMClient, LLMUnavailableError
from .models import Finding, Job, JobStatus
from .observability import get_logger, log_event, timed_step
from .report import generate_report
from .retriever import retrieve
from .tools_runner import run_tools


@dataclasses.dataclass
class PipelineResult:
    job: Job
    findings: list[Finding]
    report_markdown: str


def _run_agents_for_chunk(
    context: AgentContext, llm_client: Optional[LLMClient], logger: logging.Logger
) -> tuple[list[Finding], bool, list[str]]:
    """Runs all analysis agents in parallel for one diff chunk.

    Returns (findings, llm_unavailable, agents_used).
    """
    if llm_client is None:
        log_event(logger, "llm_unavailable", reason="no API key configured")
        return [], True, []

    findings: list[Finding] = []
    llm_unavailable = False
    agents_used: list[str] = []

    with ThreadPoolExecutor(max_workers=len(ALL_AGENTS)) as executor:
        future_to_agent = {
            executor.submit(agent_cls().run, context, llm_client): agent_cls().name
            for agent_cls in ALL_AGENTS
        }
        for future in as_completed(future_to_agent):
            agent_name = future_to_agent[future]
            try:
                findings.extend(future.result())
                agents_used.append(agent_name)
            except LLMUnavailableError as exc:
                llm_unavailable = True
                log_event(logger, "agent_llm_unavailable", agent=agent_name, detail=str(exc))
            except Exception as exc:  # noqa: BLE001 — one agent failing must not sink the run
                log_event(logger, "agent_error", agent=agent_name, detail=str(exc))

    return findings, llm_unavailable, agents_used


def run_pipeline(
    pr_ref: str,
    diff_text: str,
    job: Job,
    job_store: InMemoryJobStore,
    config: Config,
    repo_root: Path,
    llm_client: Optional[LLMClient],
    publish_fn: Optional[Callable[[str], None]] = None,
    logger: Optional[logging.Logger] = None,
) -> PipelineResult:
    logger = logger or get_logger(config.get("logging", "level", default="INFO"))
    started_at = time.monotonic()
    run_timeout = config.get("orchestrator", "run_timeout_seconds", default=300)

    job_store.set_status(job.job_id, JobStatus.RUNNING)
    log_event(logger, "PR_ANALYSIS_STARTED", job_id=job.job_id, pr_ref=pr_ref)

    partial_notes: list[str] = []
    llm_unavailable_overall = False
    agents_used: set[str] = set()
    tools_used: set[str] = set()
    all_findings: list[Finding] = []

    with timed_step(logger, "parse_diff", job_id=job.job_id):
        file_diffs = parse_diff(diff_text)

    max_diff_lines = config.get("diff", "max_diff_lines", default=2000)
    chunk_size = config.get("diff", "chunk_size_lines", default=500)
    if total_changed_lines(file_diffs) > max_diff_lines:
        chunks = chunk_diff(file_diffs, chunk_size)
        partial_notes.append(
            f"PR diff exceeds {max_diff_lines} lines — analyzed in {len(chunks)} chunks."
        )
    else:
        chunks = [file_diffs] if file_diffs else []

    enabled_tools = config.get("tools", "enabled", default=[])
    tools_timeout = config.get("tools", "timeout_seconds", default=15)

    for chunk_index, chunk in enumerate(chunks):
        if time.monotonic() - started_at > run_timeout:
            partial_notes.append("Run-level timeout reached — remaining chunks were not analyzed.")
            log_event(logger, "run_timeout", job_id=job.job_id, chunk_index=chunk_index)
            break

        # Guardrail pre-filter — mask secrets, neutralize prompt-injection
        # attempts, before anything reaches the retriever or the LLM.
        sanitized_chunk = []
        for fd in chunk:
            clean_patch, gr_report = guardrail.pre_filter(fd.patch)
            if gr_report.masked_secrets_count or gr_report.flagged_injection_snippets:
                log_event(
                    logger,
                    "guardrail_pre_filter_triggered",
                    job_id=job.job_id,
                    file=fd.path,
                    secrets_masked=gr_report.masked_secrets_count,
                    injection_snippets=len(gr_report.flagged_injection_snippets),
                )
            sanitized_chunk.append(dataclasses.replace(fd, patch=clean_patch))

        with timed_step(logger, "retrieve_context", job_id=job.job_id, chunk=chunk_index):
            retrieved_context = retrieve(sanitized_chunk, repo_root)

        with timed_step(logger, "run_tools", job_id=job.job_id, chunk=chunk_index):
            tool_results = run_tools(enabled_tools, sanitized_chunk, repo_root, tools_timeout)

        chunk_tool_findings: list[Finding] = []
        for tr in tool_results:
            chunk_tool_findings.extend(tr.findings)
            tools_used.add(tr.tool)
            if tr.status in ("partial", "error"):
                partial_notes.append(f"Tool '{tr.tool}' status={tr.status}: {tr.detail}")
        all_findings.extend(chunk_tool_findings)

        agent_context = AgentContext(
            file_diffs=sanitized_chunk,
            tool_findings=chunk_tool_findings,
            retrieved_context=retrieved_context,
        )
        with timed_step(logger, "run_agents", job_id=job.job_id, chunk=chunk_index):
            agent_findings, chunk_llm_unavailable, chunk_agents_used = _run_agents_for_chunk(
                agent_context, llm_client, logger
            )
        all_findings.extend(agent_findings)
        agents_used.update(chunk_agents_used)
        llm_unavailable_overall = llm_unavailable_overall or chunk_llm_unavailable

    if llm_unavailable_overall:
        partial_notes.append("LLM reasoning was unavailable for at least part of this run.")

    with timed_step(logger, "aggregate", job_id=job.job_id):
        ranked_findings = aggregate(all_findings)

    report_markdown = generate_report(
        ranked_findings, partial_notes=partial_notes, llm_unavailable=llm_unavailable_overall
    )

    # Guardrail Output Check — never publish a report that leaks a secret.
    checked_report, was_clean = guardrail.output_check(report_markdown)
    if not was_clean:
        log_event(logger, "guardrail_output_check_masked_secret", job_id=job.job_id)
    report_markdown = checked_report

    if publish_fn is not None:
        try:
            publish_fn(report_markdown)
        except Exception as exc:  # noqa: BLE001 — publishing failure shouldn't lose the computed report
            partial_notes.append(f"Failed to publish report: {exc}")
            log_event(logger, "publish_failed", job_id=job.job_id, detail=str(exc))

    duration_ms = int((time.monotonic() - started_at) * 1000)
    final_status = JobStatus.COMPLETED_PARTIAL if partial_notes else JobStatus.COMPLETED

    if llm_client is not None:
        # getattr, not a hard attribute access: real LLMClient always
        # provides these (see llm_client.py), but lightweight test doubles
        # used as `llm_client` in unit tests may not implement them.
        job.llm_input_tokens = getattr(llm_client, "total_input_tokens", 0)
        job.llm_output_tokens = getattr(llm_client, "total_output_tokens", 0)
        job.llm_cost_rub = getattr(llm_client, "total_cost_rub", 0.0)

    job.status = final_status
    job.agents_used = sorted(agents_used)
    job.tools_used = sorted(tools_used)
    job.duration_ms = duration_ms
    job.report_markdown = report_markdown
    job_store.update(job)

    log_event(
        logger,
        "PR_ANALYSIS_COMPLETED",
        job_id=job.job_id,
        pr_ref=pr_ref,
        status=final_status.value,
        agents_used=job.agents_used,
        tools_used=job.tools_used,
        duration_ms=duration_ms,
        llm_input_tokens=job.llm_input_tokens,
        llm_output_tokens=job.llm_output_tokens,
        llm_cost_rub=round(job.llm_cost_rub, 6),
    )

    return PipelineResult(job=job, findings=ranked_findings, report_markdown=report_markdown)


class JobManager:
    """Implements the async ack/job model from docs/system-design.md § 1:
    ``submit`` returns immediately with a job_id (queued), while the
    pipeline runs in a background thread."""

    def __init__(self, config: Config, job_store: Optional[InMemoryJobStore] = None, max_workers: int = 4):
        self.config = config
        self.job_store = job_store or InMemoryJobStore()
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._futures: dict[str, Future] = {}
        self.logger = get_logger(config.get("logging", "level", default="INFO"))

    def submit(
        self,
        pr_ref: str,
        diff_text: str,
        repo_root: Path,
        llm_client: Optional[LLMClient],
        publish_fn: Optional[Callable[[str], None]] = None,
    ) -> Job:
        """For callers that already have the diff text in hand (e.g. the CLI
        reading a local .diff file) — no I/O happens on this call."""
        job = self.job_store.create(pr_ref)
        self._submit_job(job, lambda: diff_text, repo_root, llm_client, publish_fn)
        return job

    def submit_deferred(
        self,
        pr_ref: str,
        fetch_diff: Callable[[], str],
        repo_root: Path,
        llm_client: Optional[LLMClient],
        publish_fn: Optional[Callable[[str], None]] = None,
    ) -> Job:
        """For callers where fetching the diff is itself I/O (the GitHub API)
        that must NOT block the ack — this is what the async gateway uses.
        ``fetch_diff`` runs inside the background thread, after the job is
        already created and this call has returned (docs/system-design.md
        § 1: "ack сразу, не дожидаясь завершения анализа" — that includes
        not waiting on PR ingestion, not just on the LLM)."""
        job = self.job_store.create(pr_ref)
        self._submit_job(job, fetch_diff, repo_root, llm_client, publish_fn)
        return job

    def _submit_job(
        self,
        job: Job,
        fetch_diff: Callable[[], str],
        repo_root: Path,
        llm_client: Optional[LLMClient],
        publish_fn: Optional[Callable[[str], None]],
    ) -> None:
        def _run() -> PipelineResult:
            diff_text = fetch_diff()  # runs in the worker thread, not on the ack path
            return run_pipeline(
                job.pr_ref,
                diff_text,
                job,
                self.job_store,
                self.config,
                repo_root,
                llm_client,
                publish_fn,
                self.logger,
            )

        future = self._executor.submit(_run)

        def _on_error(fut: Future) -> None:
            exc = fut.exception()
            if exc is not None:
                self.job_store.set_status(job.job_id, JobStatus.FAILED, error=str(exc))
                log_event(self.logger, "PR_ANALYSIS_FAILED", job_id=job.job_id, error=str(exc))

        future.add_done_callback(_on_error)
        self._futures[job.job_id] = future

    def get_job(self, job_id: str) -> Optional[Job]:
        return self.job_store.get(job_id)

    def shutdown(self, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait)
