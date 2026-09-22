#!/usr/bin/env python
"""Eval harness (docs/specs/observability-evals.md, governance.md § 6).

Covers all three metric tiers defined in docs/product-proposal.md's
"Метрики" section — product, agent, and technical — not just recall/
precision@5. Where a metric genuinely requires a human (review-time
reduction), this harness says so explicitly instead of fabricating a number.

Modes (--llm):
  stub  (default) — deterministic canned agent output from each case's
                     `stub_llm_response`, for reproducible CI-free runs.
                     Used for every metric except reasoning quality.
  off              — no LLM at all (tools-only fallback path).
  real             — actual Google Gemini API call (requires GOOGLE_API_KEY).
                     Required for the reasoning-quality (LLM-as-judge) metric.

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

import metrics  # noqa: E402  (eval/metrics.py, sibling module)

DATASET_DIR = Path(__file__).parent / "dataset"

# Targets copied verbatim from docs/product-proposal.md's "Метрики" tables.
TARGETS = {
    "recall": 0.6,
    "precision_at_5": 0.8,
    "tool_selection_accuracy": 0.95,
    "pipeline_stability": 0.95,
    "reasoning_quality": 4.0,  # out of 5
    "ack_p95_ms": 2000,
    "report_p95_ms_small": 90_000,  # PR <= 500 lines
    "success_rate": 0.95,
}

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


def _make_llm_client(llm_mode: str, config: Config, case: dict | None = None):
    if llm_mode == "off":
        return None
    if llm_mode == "real":
        if not config.has_llm_credentials:
            raise SystemExit("GOOGLE_API_KEY not set — cannot run --llm real")
        return LLMClient(
            model=config.get("llm", "model", default="gemini-3.6-flash"),
            temperature=config.get("llm", "temperature", default=0.2),
            max_tokens=config.get("llm", "max_tokens", default=4096),
            requests_per_minute=config.get("llm", "requests_per_minute", default=5),
            api_key=config.google_api_key,
        )
    return StubLLMClient(case["stub_llm_response"], only_for_agent=case.get("stub_agent"))


def run_case(case: dict, llm_mode: str, config: Config) -> dict:
    case_dir: Path = case["_dir"]
    diff_text = (case_dir / case["diff_file"]).read_text(encoding="utf-8")
    repo_root = case_dir / case["repo_dir"]
    llm_client = _make_llm_client(llm_mode, config, case)

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


def _pass(value, target, higher_is_better=True) -> str:
    ok = (value >= target) if higher_is_better else (value <= target)
    return "PASS" if ok else "FAIL"


def print_product_metrics(cases: list[dict], llm_mode: str, config: Config) -> None:
    print("=" * 78)
    print("ПРОДУКТОВЫЕ МЕТРИКИ (product-proposal.md)")
    print("=" * 78)

    rows = [run_case(case, llm_mode, config) for case in cases]
    print(f"{'case':<20} {'recall':>8} {'precision@5':>12} {'findings':>9} {'status':>18}")
    for row in rows:
        print(
            f"{row['name']:<20} {row['recall']:>8.2f} {row['precision_at_5']:>12.2f} "
            f"{row['findings_count']:>9} {row['status']:>18}"
        )

    avg_recall = sum(r["recall"] for r in rows) / len(rows)
    avg_precision = sum(r["precision_at_5"] for r in rows) / len(rows)
    print("-" * 78)
    print(f"{'AVERAGE':<20} {avg_recall:>8.2f} {avg_precision:>12.2f}")
    print()
    print(f"Релевантность (precision@5) >= {TARGETS['precision_at_5']} -> "
          f"{_pass(avg_precision, TARGETS['precision_at_5'])}  (measured: {avg_precision:.2f})")
    print(f"Полнота (recall)            >= {TARGETS['recall']} -> "
          f"{_pass(avg_recall, TARGETS['recall'])}  (measured: {avg_recall:.2f})")
    print(
        "Снижение времени ревью       >= 20%  -> НЕ ИЗМЕРЯЕТСЯ этим harness'ом — "
        "определена в product-proposal.md как сравнение времени человека-ревьюера "
        "до/после, требует пользовательского исследования, не автоматизируема без людей."
    )


def print_agent_metrics(cases: list[dict], llm_mode: str, config: Config) -> None:
    print()
    print("=" * 78)
    print("АГЕНТНЫЕ МЕТРИКИ")
    print("=" * 78)

    tool_result = metrics.tool_selection_accuracy(cases, config)
    print(
        f"Корректность выбора инструментов: {tool_result['accuracy']:.2f} "
        f"({tool_result['total_files']} файлов) — target >= {TARGETS['tool_selection_accuracy']} -> "
        f"{_pass(tool_result['accuracy'], TARGETS['tool_selection_accuracy'])}"
    )
    for d in tool_result["details"]:
        marker = "OK" if d["correct"] else "MISMATCH"
        print(f"    [{marker}] {d['case']}/{d['file']} ({d['language']}): expected={d['expected']} actual={d['actual']}")

    def stub_factory(case):
        return StubLLMClient(case["stub_llm_response"], only_for_agent=case.get("stub_agent"))

    stability = metrics.pipeline_stability(cases, config, stub_factory, n_runs=5)
    print(
        f"Стабильность пайплайна: {stability['success_rate']:.2f} "
        f"({stability['total_runs']} прогонов, --llm stub) — target >= {TARGETS['pipeline_stability']} -> "
        f"{_pass(stability['success_rate'], TARGETS['pipeline_stability'])}"
    )

    if llm_mode == "real":
        judge_case = next(c for c in cases if c["name"] == "none_dereference")
        diff_text = (judge_case["_dir"] / judge_case["diff_file"]).read_text(encoding="utf-8")
        llm_client = _make_llm_client("real", config)
        result = metrics.reasoning_quality(llm_client, judge_case["stub_llm_response"], diff_text)
        if result["score"] is None:
            print(f"Качество reasoning: НЕ ИЗМЕРЕНО — LLM-judge вызов не удался: {result.get('error')}")
        else:
            print(
                f"Качество reasoning (LLM-as-judge): {result['score']}/5 — "
                f"target >= {TARGETS['reasoning_quality']} -> "
                f"{_pass(result['score'], TARGETS['reasoning_quality'])}"
            )
    else:
        print(
            "Качество reasoning: НЕ ИЗМЕРЕНО в этом режиме — метрика по определению "
            "product-proposal.md требует экспертной оценки или LLM-as-judge, т.е. "
            "реального вызова модели. Запустите с --llm real."
        )


def print_technical_metrics(cases: list[dict], config: Config) -> None:
    print()
    print("=" * 78)
    print("ТЕХНИЧЕСКИЕ МЕТРИКИ")
    print("=" * 78)

    def stub_factory(case):
        return StubLLMClient(case["stub_llm_response"], only_for_agent=case.get("stub_agent"))

    ack = metrics.measure_ack_latency_ms(config, n_runs=10)
    print(
        f"p95 latency ack: {ack['p95_ms']:.1f} ms — target < {TARGETS['ack_p95_ms']} ms -> "
        f"{_pass(ack['p95_ms'], TARGETS['ack_p95_ms'], higher_is_better=False)}"
    )

    small_case = next(c for c in cases if c["name"] == "sql_injection")
    report_latency = metrics.measure_report_latency_ms(small_case, config, stub_factory, n_runs=5)
    print(
        f"p95 latency готового отчёта (небольшой PR, --llm stub, локальный пайплайн): "
        f"{report_latency['p95_ms']:.1f} ms — target < {TARGETS['report_p95_ms_small']} ms -> "
        f"{_pass(report_latency['p95_ms'], TARGETS['report_p95_ms_small'], higher_is_better=False)}"
    )
    print(
        "    ПРИМЕЧАНИЕ: это latency локального пайплайна (parse -> guardrail -> "
        "tools -> stub-агенты -> aggregate -> report), БЕЗ сетевого времени "
        "реального LLM API — реальная latency с Gemini выше и подвержена "
        "rate-limit-паузам (см. eval/README.md)."
    )

    stability = metrics.pipeline_stability(cases, config, stub_factory, n_runs=5)
    print(
        f"Успешность обработки PR: {stability['success_rate']:.2f} — "
        f"target >= {TARGETS['success_rate']} -> "
        f"{_pass(stability['success_rate'], TARGETS['success_rate'])}"
    )
    print(
        "Максимальный размер PR (2000 строк, chunking): проверяется unit-тестом "
        "test_pipeline_chunks_large_diff (tests/test_orchestrator.py), не здесь."
    )


def main() -> int:
    # Same Windows-console-codepage issue as cli.py: Cyrillic output needs
    # UTF-8 stdout, which isn't the default on a lot of Windows terminals.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser()
    parser.add_argument("--llm", choices=["stub", "off", "real"], default="stub")
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--tier",
        choices=["product", "agent", "technical", "all"],
        default="all",
        help="Which metric tier(s) to run (default: all three, matching product-proposal.md).",
    )
    args = parser.parse_args()

    config = Config.load(args.config)
    cases = _load_cases()

    if args.tier in ("product", "all"):
        print_product_metrics(cases, args.llm, config)
    if args.tier in ("agent", "all"):
        print_agent_metrics(cases, args.llm, config)
    if args.tier in ("technical", "all"):
        print_technical_metrics(cases, config)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
