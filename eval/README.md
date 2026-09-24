# Eval harness

Operationalizes **all three metric tiers** from
[`docs/product-proposal.md`](../docs/product-proposal.md)'s "Метрики"
section — product, agent, and technical, not just recall/precision@5 — and
the eval policy from [`docs/governance.md`](../docs/governance.md) § 6.
See [`docs/demo-report.md`](../docs/demo-report.md) for a consolidated
summary of the current numbers against every target.

```bash
python eval/run_eval.py --llm stub                # all 3 tiers, deterministic (default)
python eval/run_eval.py --llm off                 # tools-only fallback path
python eval/run_eval.py --llm real                # + real LLM calls (needs LLM_BASE_URL/LLM_AUTH_TOKEN);
                                                   # required for the reasoning-quality metric
python eval/run_eval.py --tier product            # just the product tier (recall/precision@5)
python eval/run_eval.py --tier agent              # just tool-selection / stability / reasoning
python eval/run_eval.py --tier technical          # just latency / success-rate
```

Metric logic beyond the product tier lives in `eval/metrics.py` (kept
separate so `run_eval.py`'s `main()` stays a thin driver over the three
`print_*_metrics` functions).

## Dataset

Three tiny synthetic PRs under `eval/dataset/`, each with a seeded, known issue:

- `sql_injection` — a SQL-injection bug catchable by static analysis alone (bandit).
- `none_dereference` — a `None`-attribute logic bug that static tools miss;
  finding it requires LLM reasoning (`bug_agent`).
- `javascript_eval` — `eval(userInput)` in a `.js` file. Exists specifically
  to give the tool-selection-accuracy metric a genuine negative case:
  semgrep (the only tool with JS support) must catch it, and pylint/flake8/
  bandit (Python-only) must be skipped, not silently run-and-find-nothing.

## Metrics

### Product (per-case, in `run_eval.py`)

- **Recall** — fraction of seeded issues matched by at least one reported finding
  (file + category equal, line within ±2). Target ≥ 0.6.
- **Precision@5** — of the top 5 aggregated findings, the fraction that match a
  seeded issue. Target ≥ 0.8. **This is an automated proxy**: the metric as
  defined in product-proposal.md is scored against human "useful/not useful"
  labels; here a seeded ground-truth match stands in for that judgment so the
  harness can run without a human in the loop. Treat it as a lower bound.
- **Снижение времени ревью** — genuinely not measured here. product-proposal.md
  defines it as a before/after comparison of human review time, which needs
  actual reviewers; the harness reports this explicitly rather than
  fabricating a substitute number.

### Agent (`eval/metrics.py`)

- **Корректность выбора инструментов** — deterministic, no LLM: for each
  file, does the actually-invoked (non-skipped) tool set exactly match
  `tools_runner.expected_tools_for_language`? Target ≥ 0.95.
- **Стабильность пайплайна** — fraction of `n_runs` repeated `--llm stub`
  runs per case that don't end in `JobStatus.FAILED`. Target ≥ 0.95.
- **Качество reasoning** — LLM-as-judge: one extra real LLM call (via the
  configured `LLM_BASE_URL`/`LLM_AUTH_TOKEN`/`LLM_MODEL`) scoring a finding
  1-5 against a clarity/correctness/relevance rubric. Only runs under
  `--llm real`; in `stub`/`off` mode the harness says so instead of
  inventing a score.

### Technical (`eval/metrics.py`)

- **p95 latency ack** — wall-clock cost of `JobManager.submit()` itself (job
  creation + handoff to the executor), with no GitHub/LLM I/O — the actual
  "return immediately" contract, not diluted by network variance. Target < 2s.
- **p95 latency report** — `--llm stub` pipeline duration (parse → guardrail
  → tools → agents → aggregate → report), i.e. the pipeline's own overhead
  in isolation from real-LLM network/rate-limit variance. Target < 90s
  (PR ≤ 500 lines).
- **Успешность обработки PR** — same computation as pipeline stability,
  reported here because product-proposal.md lists it under "Технические
  метрики" too.
- **Максимальный размер PR** — not re-measured here; covered by
  `tests/test_orchestrator.py::test_pipeline_chunks_large_diff`.

## History: precision@5 was failing, and why

The first working version of this harness measured Precision@5 = 0.20-0.38
against the ≥ 0.8 target, for two distinct, verified causes (both still
worth knowing even though they're now fixed — they explain *why* the
aggregator looks the way it does):

1. **Style noise competing for top-5 slots.** With only 2-3 total findings
   per tiny fixture, low-value lint nits ("missing module docstring")
   occupied top-5 slots simply because there weren't 5 substantive findings
   to fill them — severity-only ranking doesn't remove noise, it just
   sorts it last, which doesn't help when the whole list is short.
2. **Cross-category duplication of the same bug.** A real model's
   `bug_agent`, `security_agent` and `quality_agent` each independently
   described the *same* SQL injection in their own words, at the same
   line. The old dedup key was `(file, line, category, message)`, so three
   differently-categorized reports of one bug were **not** merged — three
   top-5 slots for one issue.

## Fix applied (`src/pr_review_agent/aggregator.py`)

- **`_is_style_noise`**: pure lint-style findings (linter-sourced, category
  `quality`, severity `low`) are excluded from `top_n` selection entirely
  (they still appear in the unranked full list used for the report — no
  information is hidden from the developer, they just don't compete for
  top-5).
- **`_near_duplicate_merge`**: findings at the same file within ±2 lines
  whose messages share enough vocabulary (Jaccard ≥ 0.2 over significant
  words, *and* ≥ 3 shared significant words — both gates are needed; Jaccard
  alone let two unrelated findings that both happen to contain a generic
  word like "issue" or "missing" false-merge) are collapsed into one
  canonical finding, regardless of category. The canonical pick prefers a
  tool-confirmed finding over an agent's guess (tools-first architecture
  decision), and if the discarded duplicates had a more detailed
  description, it's kept on `Finding.explanation` and rendered in the report
  (`report.py`) instead of silently dropped.

Both are exercised by `tests/test_aggregator.py`, including the exact
false-positive ("missing module docstring" vs. an unrelated bug) the
similarity gate was tuned against, and the real cross-category SQL-injection
example captured from an earlier `--llm real` run.

## Result after the fix

| Mode | Recall | Precision@5 | vs. target (recall ≥ 0.6, precision@5 ≥ 0.8) |
| --- | --- | --- | --- |
| `--llm stub` (deterministic) | 1.00 | **1.00** | **PASS / PASS** |
| `--llm off` (tools-only) | 0.50 | 0.50 (was 0.17) | FAIL / FAIL |
| `--llm real` (Gemini, repeated runs) | 1.00 | 0.20 - 1.00, noisy | PASS / inconsistent |

`--llm stub` is the harness's reproducible, CI-safe measurement, and it now
clears both targets — the aggregator fix works as intended when the LLM
behaves deterministically. `--llm off` recall is unchanged by design (tools
still can't find a pure logic bug without reasoning — the point of the
tools-first/LLM-second architecture, `docs/system-design.md` § 1), but its
precision@5 improved 3x purely from the noise-exclusion fix, since bandit's
one real finding is no longer diluted by pylint docstring nits.

**`--llm real` is still noisy, for two reasons that are not aggregator
bugs:**

1. **Free-tier rate limiting.** Running the CLI demo once, immediately
   followed by `eval/run_eval.py --llm real` (2 cases × 4 parallel agents),
   reliably triggers `429 RESOURCE_EXHAUSTED` from the Gemini free tier on
   some agents mid-run. The orchestrator's per-agent fallback
   (`docs/system-design.md` § 7) handles this correctly — failed agents are
   excluded, the job completes as `completed_partial` — but a run with
   fewer working agents naturally has different (often worse) precision@5,
   since the fixed seeded bug then competes against whatever tools alone
   found. This is a real empirical validation of the fallback path under
   genuine throughput limits, not a mocked test — and a concrete argument
   for the rate-limit-aware backoff/queueing suggested in
   `docs/specs/agent-orchestrator.md` before treating a 429 as
   "unavailable" outright.
2. **The benchmark scores one seeded bug per case, but a real model finds
   more than one real thing.** In several `--llm real` runs, the model
   correctly additionally flagged a genuine resource leak (unclosed DB
   connection) or missing test coverage — both legitimate, both counted as
   "not a match" by this narrow single-seeded-issue metric, which drags
   precision@5 down for reasons that have nothing to do with finding
   quality. This is a limitation of the synthetic single-issue-per-case
   benchmark design, not the system: a production eval would need either
   multiple seeded issues per case or human "useful/not useful" labels (as
   product-proposal.md's actual metric definition specifies) to score this
   fairly.

**Suggested follow-up** (not implemented in this PoC): rate-limit-aware
backoff in the Agent Scheduler, and a richer eval dataset (multiple seeded
issues per case) so a real model's legitimate extra findings don't get
penalized as false positives.
