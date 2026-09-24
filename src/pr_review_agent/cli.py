"""CLI entry point — demo / manual-run interface for the PoC.

Examples:
    python -m pr_review_agent.cli analyze --diff-file sample.diff --repo-root .
    python -m pr_review_agent.cli analyze --pr-url https://github.com/owner/repo/pull/1 --publish
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .github_client import GitHubAPIError, GitHubClient, parse_pr_url
from .job_store import InMemoryJobStore
from .llm_client import LLMClient
from .models import JobStatus
from .observability import get_logger, log_event
from .orchestrator import run_pipeline


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr_review_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze a single PR (demo/manual run)")
    source = analyze.add_mutually_exclusive_group(required=True)
    source.add_argument("--diff-file", type=str, help="Path to a local unified-diff (.diff) file")
    source.add_argument("--pr-url", type=str, help="GitHub PR URL, e.g. https://github.com/o/r/pull/1")

    analyze.add_argument("--repo-root", type=str, default=".", help="Local checkout used by tools/retriever")
    analyze.add_argument("--config", type=str, default=None, help="Path to config.yaml")
    analyze.add_argument("--pr-ref", type=str, default=None, help="Label for local-diff runs")
    analyze.add_argument("--no-llm", action="store_true", help="Force tools-only fallback mode")
    analyze.add_argument("--publish", action="store_true", help="Publish the report as a PR comment (--pr-url only)")

    return parser


def main(argv: list[str] | None = None) -> int:
    # Reports may contain Unicode (Cyrillic docs, warning glyphs); Windows
    # consoles often default to a non-UTF-8 codepage that can't print them.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command != "analyze":
        parser.print_help()
        return 1

    config = Config.load(args.config)
    logger = get_logger(config.get("logging", "level", default="INFO"))
    repo_root = Path(args.repo_root).resolve()
    job_store = InMemoryJobStore()

    github_client: GitHubClient | None = None
    publish_fn = None

    if args.pr_url:
        pr = parse_pr_url(args.pr_url)
        github_client = GitHubClient(
            token=config.github_token,
            api_base_url=config.get("github", "api_base_url", default="https://api.github.com"),
            timeout_seconds=config.get("github", "request_timeout_seconds", default=10),
            max_retries=config.get("github", "max_retries", default=3),
            retry_backoff_seconds=tuple(config.get("github", "retry_backoff_seconds", default=[1, 2, 4])),
        )
        pr_ref = pr.slug
        job = job_store.create(pr_ref)
        try:
            diff_text = github_client.get_pr_diff(pr)
        except GitHubAPIError as exc:
            job_store.set_status(job.job_id, JobStatus.FAILED, error=str(exc))
            log_event(logger, "PR_ANALYSIS_FAILED", job_id=job.job_id, pr_ref=pr_ref, error=str(exc))
            print(f"Error fetching PR: {exc}", file=sys.stderr)
            return 1
        if args.publish:
            publish_fn = lambda text: github_client.publish_comment(pr, text)  # noqa: E731
    else:
        diff_path = Path(args.diff_file)
        diff_text = diff_path.read_text(encoding="utf-8")
        pr_ref = args.pr_ref or f"local-diff:{diff_path.name}"
        job = job_store.create(pr_ref)

    llm_client = None
    if not args.no_llm and config.has_llm_credentials:
        llm_client = LLMClient(
            model=config.resolved_llm_model(default="openai/gpt-4o"),
            temperature=config.get("llm", "temperature", default=0.2),
            max_tokens=config.get("llm", "max_tokens", default=4096),
            request_timeout_seconds=config.get("llm", "request_timeout_seconds", default=30),
            max_retries=config.get("llm", "max_retries", default=1),
            requests_per_minute=config.get("llm", "requests_per_minute", default=5),
            api_key=config.llm_auth_token,
            base_url=config.llm_base_url,
        )

    result = run_pipeline(
        pr_ref=pr_ref,
        diff_text=diff_text,
        job=job,
        job_store=job_store,
        config=config,
        repo_root=repo_root,
        llm_client=llm_client,
        publish_fn=publish_fn,
        logger=logger,
    )

    print(result.report_markdown)
    print(
        f"\n[job {result.job.job_id}] status={result.job.status.value} duration_ms={result.job.duration_ms} "
        f"llm_input_tokens={result.job.llm_input_tokens} llm_output_tokens={result.job.llm_output_tokens} "
        f"llm_cost_rub={result.job.llm_cost_rub:.6f}",
        file=sys.stderr,
    )
    return 0 if result.job.status != JobStatus.FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())
