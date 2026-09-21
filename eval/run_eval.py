#!/usr/bin/env python
"""Eval harness (docs/specs/observability-evals.md, governance.md § 6).

Runs the full pipeline against a small set of PR fixtures with known
("seeded") issues and reports recall and precision@5 against the targets
defined in docs/product-proposal.md.

Modes (--llm):
  stub  (default) — deterministic canned agent output from each case's
                     `stub_llm_response`, for reproducible CI-free runs.
  off              — no LLM at all (tools-only fallback path).
  real             — actual Google Gemini API call (requires GOOGLE_API_KEY).

NOTE on precision@5: the product metric is defined against human-labeled
"useful/not useful" judgments. This harness approximates it by treating a
finding as a true positive when it matches a case's seeded ground-truth
issue (file + category, line within ±2) — a reasonable automated proxy for
a synthetic benchmark, not a substitute for the human eval described in
product-proposal.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from pr_review_agent.aggregator import aggregate  # noqa: E402
from pr_review_agent.agents import ALL_AGENTS  # noqa: E402
from pr_review_agent.config import Config  # noqa: E402
from pr_review_agent.job_store import InMemoryJobStore  # noqa: E402
from pr_review_agent.llm_client import LLMClient  # noqa: E402
from pr_review_agent.orchestrator import run_pipeline  # noqa: E402

DATASET_DIR = Path(__file__).parent / "dataset"

RECALL_TARGET = 0.6
PRECISION_AT_5_TARGET = 0.8

# A short, distinctive marker from each agent's role_prompt (src/pr_review_agent/agents/*.py),
# used by StubLLMClient to tell which agent is calling without changing the
# LLMClient interface. Real agents write substantively different text per
# role; a stub that echoed the same string to all four would make every
# case's near-identical duplicate get collapsed to one (arbitrary) category
# by the aggregator's cross-category merge — a stub artifact, not something
# a real model does.
_AGENT_ROLE_MARKERS = {agent_cls().name: agent_cls().role_prompt[:20] for agent_cls in ALL_AGENTS}


class StubLLMClient:
    """Returns a canned response per agent role — see module docstring and
    `case.json`'s optional `stub_agent` (only that agent gets
    `stub_llm_response`; every other agent gets "[]"). When `stub_agent` is
    absent, every agent gets the same response (fine when that response is
    itself "[]", as in the sql_injection case, which relies on bandit alone)."""

    def __init__(self, response: str, only_for_agent: str | None = None):
        self._response = response
        self._only_for_agent = only_for_agent

    def complete(self, system_prompt: str, user_content: str) -> str:
        if self._only_for_agent is None:
            return self._response
        marker = _AGENT_ROLE_MARKERS[self._only_for_agent]
        return self._response if marker in system_prompt else "[]"


def _matches(finding, seeded: dict) -> bool:
    if finding.file != seeded["file"] or finding.category.value != seeded["category"]:
        return False
    if finding.line is None or seeded.get("line") is None:
        return True
    return abs(finding.line - seeded["line"]) <= 2


def _load_cases() -> list[dict]:
    cases = []
    for case_dir in sorted(DATASET_DIR.iterdir()):
        case_file = case_dir / "case.json"
        if case_file.exists():
            case = json.loads(case_file.read_text(encoding="utf-8"))
            case["_dir"] = case_dir
            cases.append(case)
    return cases


def run_case(case: dict, llm_mode: str, config: Config) -> dict:
    case_dir: Path = case["_dir"]
    diff_text = (case_dir / case["diff_file"]).read_text(encoding="utf-8")
    repo_root = case_dir / case["repo_dir"]

    if llm_mode == "off":
        llm_client = None
    elif llm_mode == "real":
        if not config.has_llm_credentials:
            raise SystemExit("GOOGLE_API_KEY not set — cannot run --llm real")
        llm_client = LLMClient(
            model=config.get("llm", "model", default="gemini-3.6-flash"),
            temperature=config.get("llm", "temperature", default=0.2),
            max_tokens=config.get("llm", "max_tokens", default=4096),
            api_key=config.google_api_key,
        )
    else:
        llm_client = StubLLMClient(case["stub_llm_response"], only_for_agent=case.get("stub_agent"))

    job_store = InMemoryJobStore()
    job = job_store.create(f"eval:{case['name']}")
    result = run_pipeline(
        pr_ref=job.pr_ref,
        diff_text=diff_text,
        job=job,
        job_store=job_store,
        config=config,
        repo_root=repo_root,
        llm_client=llm_client,
    )

    seeded_issues = case["seeded_issues"]
    matched_seeded = [s for s in seeded_issues if any(_matches(f, s) for f in result.findings)]
    recall = len(matched_seeded) / len(seeded_issues) if seeded_issues else 1.0

    top5 = aggregate(result.findings, top_n=5)
    true_positives_in_top5 = sum(1 for f in top5 if any(_matches(f, s) for s in seeded_issues))
    precision_at_5 = true_positives_in_top5 / len(top5) if top5 else 0.0

    return {
        "name": case["name"],
        "recall": recall,
        "precision_at_5": precision_at_5,
        "findings_count": len(result.findings),
        "status": result.job.status.value,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", choices=["stub", "off", "real"], default="stub")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()

    config = Config.load(args.config)
    cases = _load_cases()
    rows = [run_case(case, args.llm, config) for case in cases]

    print(f"{'case':<20} {'recall':>8} {'precision@5':>12} {'findings':>9} {'status':>18}")
    for row in rows:
        print(
            f"{row['name']:<20} {row['recall']:>8.2f} {row['precision_at_5']:>12.2f} "
            f"{row['findings_count']:>9} {row['status']:>18}"
        )

    avg_recall = sum(r["recall"] for r in rows) / len(rows)
    avg_precision = sum(r["precision_at_5"] for r in rows) / len(rows)
    print("-" * 70)
    print(f"{'AVERAGE':<20} {avg_recall:>8.2f} {avg_precision:>12.2f}")
    print()
    print(
        f"Target: recall >= {RECALL_TARGET} -> "
        f"{'PASS' if avg_recall >= RECALL_TARGET else 'FAIL'}"
    )
    print(
        f"Target: precision@5 >= {PRECISION_AT_5_TARGET} -> "
        f"{'PASS' if avg_precision >= PRECISION_AT_5_TARGET else 'FAIL'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
