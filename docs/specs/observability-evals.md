# Спецификация: Observability / Evals

Роль в архитектуре — [system-design.md, раздел 2](../system-design.md#2-модули-и-их-роли). Целевые значения метрик — product-proposal.md, раздел «Метрики».

## Логи и трейсы

- Структурированные логи по каждому run: `job_id`, `pr_id`, timestamps, `agents_used`, `tools_used`, статус, `duration_ms` (формат — governance.md, раздел 2).
- Трейс по шагам pipeline (ingestion → parse → retrieve → tools → agents → aggregate → report → publish) с длительностью каждого шага — основа для расчёта latency-метрик.
- **Не логируется:** полный код PR, секреты, токены, сырой LLM prompt целиком (governance.md).

## Метрики

- **Технические:** p95 latency (ack и отчёт), success rate, tool failure rate, LLM fallback rate — сбор по каждому run, агрегация за период (product-proposal.md, Технические метрики).
- **Агентные:** частота fallback/partial результатов, per-agent latency, per-agent error rate.
- **Продуктовые** (через eval-прогоны, не в проде): precision@5, recall на seeded-bug benchmark, оценка качества reasoning (product-proposal.md, Продуктовые/Агентные метрики).

## Eval harness

- Отдельный набор тестовых PR (реальные + synthetic/seeded баги) для регулярного прогона вне прод-трафика.
- Eval-прогон фиксирует версию модели, версию промптов/конфига и результаты по всем метрикам — для сравнения между релизами (governance.md, раздел 6).
- Триггеры прогона: перед релизом новой версии промпта/модели, периодически (например раз в неделю на демо-этапе) для отслеживания регрессий.

## Алерты (PoC-уровень)

- Падение success rate ниже порога (< 90%) или превышение latency SLO фиксируется в логах для ручного разбора; полноценный алертинг — вне PoC-скоупа (см. README.md, Out-of-Scope).
