"""Agent- and technical-tier metrics from docs/product-proposal.md's
"Метрики" section, beyond the product-tier recall/precision@5 already in
run_eval.py. Split out so run_eval.py's main() stays a thin driver.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from pr_review_agent.config import Config
from pr_review_agent.diff_parser import parse_diff
from pr_review_agent.job_store import InMemoryJobStore
from pr_review_agent.llm_client import LLMUnavailableError
from pr_review_agent.models import JobStatus
from pr_review_agent.orchestrator import JobManager, run_pipeline
from pr_review_agent.tools_runner import expected_tools_for_language, run_tools


def tool_selection_accuracy(cases: list[dict], config: Config) -> dict:
    """Агентная метрика "Корректность выбора инструментов" (product-proposal.md):
    сопоставление выбранных инструментов с эталонным маппингом «тип файла →
    ожидаемые инструменты» (tools_runner._TOOL_LANGUAGES). Не использует LLM —
    чисто детерминированная проверка tools_runner + language detection.

    Каждый файл прогоняется через run_tools() ИЗОЛИРОВАННО (список из одного
    FileDiff), чтобы "какие инструменты реально не были skipped" однозначно
    относилось именно к этому файлу, а не ко всему diff разом.
    """
    enabled_tools = config.get("tools", "enabled", default=[])
    timeout = config.get("tools", "timeout_seconds", default=15)
    total_files = 0
    correct_files = 0
    details = []

    for case in cases:
        case_dir: Path = case["_dir"]
        diff_text = (case_dir / case["diff_file"]).read_text(encoding="utf-8")
        repo_root = case_dir / case["repo_dir"]
        file_diffs = parse_diff(diff_text)

        for fd in file_diffs:
            total_files += 1
            expected = expected_tools_for_language(fd.language, enabled_tools)
            results = run_tools(enabled_tools, [fd], repo_root, timeout)
            actual = {r.tool for r in results if r.status != "skipped"}
            is_correct = actual == expected
            correct_files += int(is_correct)
            details.append(
                {
                    "case": case["name"],
                    "file": fd.path,
                    "language": fd.language,
                    "expected": sorted(expected),
                    "actual": sorted(actual),
                    "correct": is_correct,
                }
            )

    accuracy = correct_files / total_files if total_files else 1.0
    return {"accuracy": accuracy, "total_files": total_files, "details": details}


def pipeline_stability(cases: list[dict], config: Config, stub_llm_factory, n_runs: int = 5) -> dict:
    """Агентная / техническая метрика "Стабильность пайплайна" /
    "Успешность обработки PR" (product-proposal.md): доля запусков без
    критической ошибки. Прогоняется в детерминированном --llm stub режиме
    (n_runs повторов на каждый кейс) — без сетевых вызовов, без риска
    rate-limit-шума, специально чтобы измерять стабильность ПАЙПЛАЙНА, а не
    стабильность внешнего API.
    """
    total_runs = 0
    successful_runs = 0

    for case in cases:
        case_dir: Path = case["_dir"]
        diff_text = (case_dir / case["diff_file"]).read_text(encoding="utf-8")
        repo_root = case_dir / case["repo_dir"]

        for _ in range(n_runs):
            store = InMemoryJobStore()
            job = store.create(f"stability:{case['name']}")
            llm_client = stub_llm_factory(case)
            result = run_pipeline(
                pr_ref=job.pr_ref,
                diff_text=diff_text,
                job=job,
                job_store=store,
                config=config,
                repo_root=repo_root,
                llm_client=llm_client,
            )
            total_runs += 1
            successful_runs += int(result.job.status != JobStatus.FAILED)

    return {"success_rate": successful_runs / total_runs if total_runs else 1.0, "total_runs": total_runs}


_JUDGE_SYSTEM_PROMPT = (
    "You are grading ONE code-review finding against a rubric, on a scale of "
    "1 (poor) to 5 (excellent). Score three dimensions mentally — clarity "
    "(is the explanation understandable), correctness (is the technical "
    "claim accurate for the shown diff), relevance (does it matter for this "
    "specific change) — then respond with ONLY a single integer 1-5, "
    "nothing else."
)


def reasoning_quality(llm_client, finding_message: str, diff_text: str) -> dict:
    """Агентная метрика "Качество reasoning" (product-proposal.md): требует
    экспертную оценку или LLM-as-judge по рубрике 1-5. Эта функция реализует
    LLM-as-judge вариант — один дополнительный вызов той же модели с ролью
    рецензента. Требует реальный LLM (--llm real); в stub/off режиме
    вызывающий код должен пропустить эту метрику и явно пометить её как
    "не измерено", а не подставлять выдуманное число.
    """
    user_content = f"Diff:\n{diff_text}\n\nFinding to grade:\n{finding_message}"
    try:
        raw = llm_client.complete(_JUDGE_SYSTEM_PROMPT, user_content)
    except LLMUnavailableError as exc:
        return {"score": None, "error": str(exc)}

    digits = "".join(ch for ch in raw if ch.isdigit())
    score = int(digits[0]) if digits else None
    return {"score": score, "raw_response": raw}


def measure_report_latency_ms(case: dict, config: Config, stub_llm_factory, n_runs: int = 5) -> dict:
    """Техническая метрика "p95 latency получения готового отчёта"
    (product-proposal.md target: <90s / <180s). Deterministic (--llm stub),
    so this measures the *pipeline's own* overhead (parsing, guardrail,
    tools, aggregation, report generation) rather than network variance —
    a fair, reproducible number distinct from the free-tier-throttled
    numbers in eval/README.md.
    """
    case_dir: Path = case["_dir"]
    diff_text = (case_dir / case["diff_file"]).read_text(encoding="utf-8")
    repo_root = case_dir / case["repo_dir"]

    durations_ms = []
    for _ in range(n_runs):
        store = InMemoryJobStore()
        job = store.create(f"latency:{case['name']}")
        llm_client = stub_llm_factory(case)
        result = run_pipeline(
            pr_ref=job.pr_ref,
            diff_text=diff_text,
            job=job,
            job_store=store,
            config=config,
            repo_root=repo_root,
            llm_client=llm_client,
        )
        durations_ms.append(result.job.duration_ms)

    durations_ms.sort()
    p95_index = min(len(durations_ms) - 1, int(round(0.95 * (len(durations_ms) - 1))))
    return {"p95_ms": durations_ms[p95_index], "samples_ms": durations_ms}


def measure_ack_latency_ms(config: Config, n_runs: int = 10) -> dict:
    """Техническая метрика "p95 latency подтверждения приёма запроса (ack)"
    (product-proposal.md target: <2s). Measures JobManager.submit()'s own
    wall-clock cost — the "return immediately, run in background" contract
    at the heart of the async architecture (system-design.md § 1) — with no
    GitHub/LLM I/O involved, since ack must not wait on either.
    """
    manager = JobManager(config, max_workers=4)
    durations_ms = []
    try:
        for i in range(n_runs):
            start = time.monotonic()
            manager.submit(
                pr_ref=f"ack-test-{i}",
                diff_text="",
                repo_root=Path("."),
                llm_client=None,
            )
            durations_ms.append((time.monotonic() - start) * 1000)
    finally:
        manager.shutdown(wait=True)

    durations_ms.sort()
    p95_index = min(len(durations_ms) - 1, int(round(0.95 * (len(durations_ms) - 1))))
    return {"p95_ms": durations_ms[p95_index], "samples_ms": durations_ms}
