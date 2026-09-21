# System Design — PR Review Agent

Связанные документы: [product-proposal.md](product-proposal.md) (цель, метрики, ограничения), [governance.md](governance.md) (риски, политики, guardrails).

Диаграммы: [docs/diagrams/](diagrams). Технические спецификации модулей: [docs/specs/](specs).

## 1. Ключевые архитектурные решения

| # | Решение | Обоснование |
| - | --- | --- |
| 1 | Асинхронная job-based архитектура: быстрый ack, отчёт публикуется позже | Синхронный p95 < 20 с нереалистичен при нескольких последовательных LLM-вызовах (product-proposal.md → Ограничения → Latency) |
| 2 | Оркестратор + набор специализированных агентов; независимые агенты запускаются параллельно | Снижает latency пайплайна, изолирует отказ одного агента от остальных |
| 3 | Tools-first, LLM-second: static analysis — источник фактов, LLM — reasoning и объяснения | Снижает риск галлюцинаций (governance.md, риск «Галлюцинации LLM») |
| 4 | Лёгкий retrieval-контур (symbol resolution + опциональный embedding-поиск) вместо полноценного RAG по всему репозиторию | PoC-бюджет ограничен; полноценный индекс репозитория избыточен для diff-центричной задачи |
| 5 | Guardrail pre-/post-filter вокруг LLM-вызовов (маскирование секретов, фильтрация подозрительных инструкций в коде, проверка выходного отчёта) | Защита от утечки секретов и prompt injection (governance.md, разделы 3–4) |
| 6 | Система не имеет прав на запись в репозиторий — только чтение diff и публикация комментария | Human-in-the-loop (governance.md, раздел 5) |
| 7 | Состояние существует только на время жизни одного job, без долгосрочной памяти между PR | Минимизация данных (governance.md, раздел 3), упрощение PoC-скоупа |

## 2. Модули и их роли

| Модуль | Роль | Спецификация |
| --- | --- | --- |
| API / Webhook Gateway | Принимает запрос на анализ PR, валидирует вход, возвращает ack, создаёт job | [specs/serving-config.md](specs/serving-config.md) |
| Job Store | Хранит состояние job (queued/running/completed/failed) на время его жизни | [specs/memory-context.md](specs/memory-context.md) |
| PR Ingestion Module | Получает diff и метаданные PR через GitHub API | [specs/tools-api.md](specs/tools-api.md) |
| Diff Parser | Разбивает diff по файлам, определяет язык, готовит чанки для больших PR | — |
| Guardrail Pre-Filter | Маскирует секреты, фильтрует подозрительные инструкции в комментариях кода перед LLM | [specs/agent-orchestrator.md](specs/agent-orchestrator.md) |
| Context Retriever | Резолвит символы из diff, подтягивает связанные файлы/тесты/конвенции проекта | [specs/retriever.md](specs/retriever.md) |
| Tool Integration Layer | Запускает pylint, flake8, semgrep, bandit в sandboxed subprocess | [specs/tools-api.md](specs/tools-api.md) |
| Orchestrator / Agent Scheduler | Координирует пайплайн, параллелизм, таймауты, retry/fallback | [specs/agent-orchestrator.md](specs/agent-orchestrator.md) |
| Analysis Agents (Bug / Security / Quality / Test-coverage) | LLM-backed reasoning над diff + tool outputs + retrieved context | [specs/agent-orchestrator.md](specs/agent-orchestrator.md) |
| Aggregation Module | Дедуп и ранжирование находок всех агентов | — |
| Report Generator | Формирует итоговый markdown-отчёт | — |
| Guardrail Output Check | Проверяет отчёт на секреты/некорректный контент перед публикацией | [specs/agent-orchestrator.md](specs/agent-orchestrator.md) |
| Report Publisher | Публикует комментарий в PR | [specs/tools-api.md](specs/tools-api.md) |
| Observability & Eval Layer | Логи, трейсы, метрики, eval harness | [specs/observability-evals.md](specs/observability-evals.md) |

Диаграммы состава: [C4 Container](diagrams/c4-container.md), [C4 Component (Orchestrator)](diagrams/c4-component.md).

## 3. Основной workflow выполнения задачи

1. Пользователь отправляет ссылку на PR → валидация входа.
2. Ack пользователю, job переходит в статус `queued`.
3. PR Ingestion получает diff и метаданные через GitHub API.
4. Diff Parser разбивает diff по файлам; PR свыше 2000 строк — chunking.
5. Guardrail Pre-Filter маскирует секреты и фильтрует подозрительные комментарии.
6. Context Retriever подтягивает связанный контекст (символы, тесты, конвенции).
7. Tool Integration Layer параллельно запускает static analysis инструменты.
8. Analysis Agents параллельно выполняют reasoning над diff + tool outputs + retrieved context.
9. Aggregation Module объединяет и ранжирует находки.
10. Report Generator формирует отчёт; Guardrail Output Check проверяет его перед публикацией.
11. Report Publisher публикует комментарий в PR; job переходит в статус `completed`.

Полная схема с ветками ошибок: [diagrams/workflow.md](diagrams/workflow.md).

## 4. State / Memory / Context handling

- **Job state** — конечный автомат `queued → running → completed | failed`, хранится в Job Store на ограниченный срок (согласовано с политикой логов, governance.md).
- **Working memory одного run** — общий «blackboard»-объект, доступный всем агентам оркестратора в рамках одного run (diff-чанки, tool outputs, retrieved context, findings агентов); уничтожается по завершении run.
- **Context budget** — на run выделяется бюджет токенов, приоритет: (1) относящийся к находке фрагмент diff, (2) находки инструментов, (3) retrieved context — последний урезается первым при нехватке бюджета.
- **Отсутствие долгосрочной памяти** — PoC не хранит контекст между PR и между запусками одного PR, кроме статуса job и агрегированных результатов (governance.md, раздел 3 «Ограниченное хранение»).

Подробнее: [specs/memory-context.md](specs/memory-context.md).

## 5. Retrieval-контур

Retriever дополняет diff контекстом, которого нет в самом diff: определения вызываемых функций/классов, связанные тестовые файлы, конвенции проекта (README/CONTRIBUTING). Индекс строится «на лету» на ревизии PR через symbol resolution (AST-based), без персистентной vector DB; опциональный in-memory embedding-поиск ограничен файлами в пределах одного «прыжка» импорта и по умолчанию выключен в PoC ради соблюдения latency-бюджета. Ограничения: работает в пределах одного репозитория/PR, не резолвит сильно динамический код (`getattr`, `eval`), в этом случае возвращает пустой контекст, а не ошибку.

Подробнее: [specs/retriever.md](specs/retriever.md).

## 6. Tool / API интеграции

| Интеграция | Назначение | Ключевые ограничения контракта |
| --- | --- | --- |
| GitHub API | Получение diff/метаданных, публикация комментария | timeout 10с, 3 retry с backoff, только `pull_requests:read/write`, без прав на push/merge |
| pylint / flake8 / semgrep / bandit | Static analysis | sandboxed subprocess, timeout 15с/файл, сбой одного инструмента не блокирует остальные |
| LLM API (Google Gemini) | Reasoning, объяснения, генерация отчёта | timeout 30с, 1 retry, код передаётся только как данные, никогда как инструкции |

Подробнее, включая обработку ошибок и side effects: [specs/tools-api.md](specs/tools-api.md).

## 7. Failure modes, fallback, guardrails

| Отказ | Детект | Fallback / guardrail |
| --- | --- | --- |
| PR не найден / нет доступа | 4xx от GitHub API | job.status=failed, сообщение пользователю |
| GitHub API rate limit / 5xx | Ошибка/таймаут запроса | Retry с exponential backoff, затем failed |
| Diff больше лимита (2000 строк) | Проверка размера после парсинга | Chunking и анализ по частям |
| Static analysis инструмент упал/завис | Ненулевой exit code без отчёта / таймаут 15с | Результат инструмента отбрасывается как partial, остальные инструменты продолжают работу |
| LLM недоступен / ошибка / timeout | Ошибка запроса, 1 retry исчерпан | Fallback-отчёт только по результатам static tools, без LLM-объяснений |
| Агент не уложился в таймаут run | Run-level timeout | Результат агента исключается из aggregation, помечается «не проанализировано» |
| Секреты в diff | Regex secret scanning в Guardrail Pre-Filter | Маскирование перед отправкой в LLM (governance.md, раздел 3) |
| Prompt injection в комментариях кода | Suspicious pattern detection | Фильтрация/игнорирование инструкций, изоляция system prompt (governance.md, раздел 4) |
| Некорректный/чувствительный контент в готовом отчёте | Guardrail Output Check перед публикацией | Отчёт блокируется от публикации до исправления/маскирования |

Детальный risk register с вероятностью/влиянием/остаточным риском — governance.md, раздел 1.

## 8. Технические и операционные ограничения

Полные целевые значения и способы расчёта — product-proposal.md, разделы «Метрики» и «Ограничения». Кратко: p95 ack < 2с, p95 готового отчёта < 90с (PR ≤ 500 строк) / < 180с (до 2000 строк), success rate ≥ 95%, максимальный размер PR — 2000 строк diff, PoC реализуется за 1–2 недели командой 2–3 человека с ограниченным бюджетом на LLM-запросы.

## 9. Точки контроля

1. Валидация входного запроса (URL/доступ к PR) — до постановки job в очередь.
2. Проверка размера diff и необходимости chunking — после Diff Parser.
3. Guardrail Pre-Filter — обязательный шаг перед любым обращением к LLM.
4. Circuit breaker / rate limiter — перед каждым внешним вызовом (GitHub API, LLM API).
5. Guardrail Output Check — обязательный шаг перед публикацией отчёта; единственная точка, определяющая, что увидит пользователь и репозиторий.
6. Human-in-the-loop — система только публикует рекомендации, не изменяет код и не мерджит PR (governance.md, раздел 5).
