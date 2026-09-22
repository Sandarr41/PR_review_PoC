# PR-review Agent

Агентная система для автоматического анализа Pull request. Помогает разработчикам и ревьверам находить:

* Потенциальные баги
* Проблемы безопасности
* Несоответствие стилю
* Плохие практики
* Недостающие тесты

Система анализирует diff PR, запускает агентов и формирует отчёт о ревью

## Проблема

Code-review часто страдает от:

* Пропуска багов
* Субъективности
* Времязатратности для ревьюера

LLM может помогать, но не имеет за собой контекста, от чего часто галлюцинирует или даёт ответ, неподходящий для рабочих реалий (стиль, тесты)

Поэтому нужен агентный пайплайн с инструментами 

## Задача проекта

Создать PoC системы, которая:

1. Автоматически анализирует Pull Request
2. Выявляет потенциальные проблемы
3. Объясняет их
4. Предлагает улучшения

## Для кого 

* Разработчики
* Команды стартапов
* Open-source проекты

## Use-case сценарий

1. Пользователь вводит url GitHub PR
2. Система:
   - загружает diff
   - анализирует код
3. Выводит отчёт

Например:
```

PR Review Summary

Potential Bugs
- Possible None dereference in user_service.py:42

Security
- Unsanitized input passed to SQL query

Code Quality
- Function exceeds recommended complexity

Testing
- No tests found for new function create_user()
  
```

## Out-of-Scope

PoC не будет:

* Автоматически мёрджить PR
* Исправлять код
* Запускать CI/CD

## Документация

* [docs/product-proposal.md](docs/product-proposal.md) — цель, метрики, ограничения, use-case/edge-case
* [docs/governance.md](docs/governance.md) — риски, политика логов/данных, guardrails
* [docs/system-design.md](docs/system-design.md) — архитектура: модули, workflow, state/memory, retrieval, failure modes
* [docs/economics.md](docs/economics.md) — экономическая модель: измеренная стоимость токенов, формула на PR, сценарии по объёму
* [docs/diagrams/](docs/diagrams) — C4 Context/Container/Component, workflow и data-flow диаграммы
* [docs/specs/](docs/specs) — технические спецификации каждого модуля
* [docs/demo-report.md](docs/demo-report.md) — итоговый отчёт: e2e-сценарий, метрики vs цели, честный статус реализации

## Запуск (PoC)

```bash
python -m venv .venv && source .venv/Scripts/activate  # Windows Git Bash; используйте .venv/bin/activate на macOS/Linux
pip install -r requirements.txt
cp .env.example .env   # заполните GITHUB_TOKEN / GOOGLE_API_KEY (оба опциональны для демо)
```

### Демо на локальном diff (без GitHub/LLM)

```bash
PYTHONPATH=src python -m pr_review_agent.cli analyze \
  --diff-file tests/fixtures/sample.diff \
  --repo-root demo \
  --no-llm
```

`demo/` — локальный «checkout» с post-PR версией файлов из `sample.diff`,
поэтому Tool Integration Layer (`pylint`/`flake8`/`bandit`) работает по-настоящему.
Без `--no-llm` и с заданным `GOOGLE_API_KEY` агенты (`bug`/`security`/
`quality`/`test_coverage`) дополнительно рассуждают над diff через Google Gemini API.

### Анализ реального GitHub PR

```bash
PYTHONPATH=src python -m pr_review_agent.cli analyze \
  --pr-url https://github.com/<owner>/<repo>/pull/<n> \
  --repo-root /path/to/local/checkout \
  [--publish]
```

### Async API (опционально)

```bash
uvicorn pr_review_agent.gateway:app --app-dir src --reload
# POST /analyze {"pr_url": "..."}  -> {"job_id": "...", "status": "queued"}
# GET  /jobs/{job_id}              -> статус + отчёт, когда готов
```

### Тесты и eval

```bash
PYTHONPATH=src pytest -q          # unit + integration тесты (см. tests/)
python eval/run_eval.py --llm stub   # eval harness, см. eval/README.md
```

## Структура репозитория

```
src/pr_review_agent/   # реализация: orchestrator, agents, tools_runner, retriever, guardrail, ...
tests/                 # pytest-тесты (диапазон: diff parser -> guardrail -> orchestrator e2e)
eval/                  # eval harness + датасет с seeded-багами (см. eval/README.md)
demo/                  # локальный "checkout" для офлайн-демо
config/                # config.example.yaml (см. docs/specs/serving-config.md)
docs/                  # Milestone 1 и 2 документация (см. выше)
```
