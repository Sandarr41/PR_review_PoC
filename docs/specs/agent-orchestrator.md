# Спецификация: Agent / Orchestrator

Роль в архитектуре — [system-design.md, раздел 3 и 9](../system-design.md#3-основной-workflow-выполнения-задачи). Диаграммы — [C4 Component](../diagrams/c4-component.md), [Workflow](../diagrams/workflow.md).

## Шаги (pipeline)

1. Validate request
2. Ingest PR
3. Parse diff
4. Guardrail pre-filter
5. Retrieve context
6. Run tools (параллельно)
7. Run analysis agents (параллельно)
8. Aggregate
9. Generate report
10. Guardrail output check
11. Publish

## Правила переходов (transition rules)

- Шаги 6 и 7 частично параллельны для независимых агентов (Bug / Security / Quality / Test-coverage), но каждый агент внутри себя ждёт релевантные ему tool outputs перед reasoning (например, Security Agent ждёт результаты bandit/semgrep).
- Переход к шагу 8 (Aggregation) происходит после завершения всех агентов либо истечения run-level таймаута — какое условие наступит раньше; один зависший агент не блокирует остальных.
- Guardrail-шаги (4 и 10) обязательны и не могут быть отключены конфигурацией.

## Stop condition

- Run считается завершённым, когда Aggregation получил результаты (полные либо partial) от всех агентов, либо истёк run-level timeout. Soft-target на latency — p95 90с/180с (product-proposal.md); hard timeout выставляется с запасом (например 5 минут).
- При превышении hard timeout run принудительно завершается с partial-результатами; `job.status=completed(partial)` с соответствующей пометкой в отчёте.

## Retry / fallback

- **GitHub API:** retry с backoff (см. [specs/tools-api.md](tools-api.md)); после исчерпания попыток — `job.status=failed`.
- **Tool failure:** изоляция — сбой одного инструмента не останавливает остальные, результат помечается `partial`.
- **LLM failure:** 1 retry, затем fallback на tools-only отчёт (без LLM-объяснений, только находки инструментов с пометкой «LLM reasoning unavailable»).
- **Агент, не уложившийся в таймаут:** его результат исключается из aggregation и отмечается в отчёте как «не проанализировано».

## Точки контроля

- Валидация входа (шаг 1).
- Guardrail pre-filter перед любым обращением к LLM (шаг 4).
- Проверка размера diff / необходимость chunking (после шага 3).
- Guardrail output check перед публикацией (шаг 10) — единственная точка, определяющая, что увидит пользователь и репозиторий.
