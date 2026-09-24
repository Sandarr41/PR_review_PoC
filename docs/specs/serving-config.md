# Спецификация: Serving / Config

Роль в архитектуре — [system-design.md, раздел 2](../system-design.md#2-модули-и-их-роли).

## Запуск (PoC)

- Точки входа: (а) CLI/скрипт с PR URL для ручного запуска на демо; (б) минимальный REST-эндпоинт (webhook или ручной POST) для async-режима, описанного в product-proposal.md.
- Оркестратор и агенты запускаются как единый процесс на PoC-этапе, без выделенного message broker; допускается in-process очередь задач (например asyncio task queue) вместо внешнего брокера — упрощение, приемлемое для PoC-масштаба (1–2 недели, 2–3 человека).

## Конфигурация

- Конфиг-файл (yaml/.env) задаёт: список включённых инструментов анализа, таймауты по шагам, лимиты diff size, модель LLM и её параметры (temperature, max_tokens), пороги Guardrail (например список regex для secret scanning).
- Разные окружения (demo/dev) — через отдельные конфиг-файлы, без хардкода значений в коде.

## Секреты

- GitHub token и LLM API key передаются через переменные окружения / secret-менеджер, не хранятся в репозитории и не логируются (governance.md).
- Секреты процесса-оркестратора недоступны sandboxed subprocess'ам static analysis инструментов (см. [specs/tools-api.md](tools-api.md)).

## Версии моделей

- Версия LLM-модели фиксируется в конфиге и логируется в metadata каждого run — для воспроизводимости eval-результатов (см. [specs/observability-evals.md](observability-evals.md)).
- Обновление версии модели — отдельный конфигурационный релиз, сопровождается прогоном eval-набора (governance.md, раздел 6) перед раскаткой на демо.

## LLM endpoint

Система обращается к LLM через один протокол — OpenAI Chat Completions API (`openai` SDK) — по трём переменным окружения:

| Переменная | Назначение |
| --- | --- |
| `LLM_BASE_URL` | URL эндпоинта (например `https://polza.ai/api/v1`) |
| `LLM_AUTH_TOKEN` | Токен, передаётся как `Authorization: Bearer <token>` — ставит сама SDK `openai` |
| `LLM_MODEL` | Имя модели на этом эндпоинте — переопределяет `llm.model` из `config.yaml` |

Все три должны быть заданы вместе (`Config.has_llm_credentials`, `src/pr_review_agent/config.py`). Без них — fallback-режим (docs/system-design.md § 7).

**Провайдер по умолчанию — [Polza.ai](https://polza.ai/docs)**: унифицированный прокси перед ~400 моделями (OpenAI, Anthropic, Google, Meta и др.) через один OpenAI-совместимый API — `POST {LLM_BASE_URL}/chat/completions`, стандартная форма `choices[].message.content` / `usage.prompt_tokens`/`completion_tokens`. Полное имя модели включает провайдера, например `openai/gpt-4o` или `anthropic/claude-sonnet-5` (см. каталог на `polza.ai/models`).

**Ограничение:** эндпоинт должен реально говорить по протоколу OpenAI Chat Completions — это не универсальный переходник на любую форму API (например, «сырой» Gemini или Anthropic Messages API без OpenAI-совместимой обёртки не подключить этим клиентом).

**Стоимость — реальная, от провайдера, не оценка.** Polza (и агрегаторы такого рода в целом) возвращают фактическую стоимость вызова прямо в ответе — `usage.cost_rub` (или `usage.cost`), расширение поверх стандартной схемы OpenAI. `LLMClient` читает это поле напрямую и накапливает в `total_cost_rub` (`estimated_cost_rub()`) — статической таблицы цен по моделям больше нет (`pricing.py` был удалён): её просто невозможно поддерживать вручную для агрегатора с ~400 моделями и изменяющимися ценами. См. `docs/economics.md`.

## История: раньше был выбор Google Gemini / Anthropic напрямую

До перехода на Polza.ai система умела говорить напрямую с Google Gemini API (`google-genai` SDK, `GOOGLE_API_KEY`) и с Anthropic Messages API (`anthropic` SDK, `LLM_PROTOCOL=anthropic`) — обе интеграции были проверены вживую (в частности, эмпирически подтверждённая деталь: у Gemini API-ключ передаётся заголовком `x-goog-api-key`, а `Authorization: Bearer` ломает аутентификацию с `401`, так как сервер Google пытается разобрать его как OAuth2-токен). Этот путь убран в пользу единого OpenAI-совместимого протокола — прямой доступ к конкретному провайдеру теперь не отличается от доступа через агрегатор.
