# Быстрый старт

Практический гайд: куда вставить свои `URL`/`токен`/`модель`, как выбрать PR для ревью и как всё это запустить. Общее описание архитектуры — [system-design.md](system-design.md); справочник по всем полям конфига — [specs/serving-config.md](specs/serving-config.md).

## 0. Установка (один раз)

```bash
python -m venv .venv && source .venv/Scripts/activate   # Windows Git Bash; на macOS/Linux — .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 1. Куда вставить URL, токен и модель

Всё — в файле `.env` (создан на шаге 0, в `.gitignore`, наружу не уйдёт). Три поля, всегда вместе:

```bash
# .env
LLM_BASE_URL=https://polza.ai/api/v1
LLM_AUTH_TOKEN=ваш_ключ
LLM_MODEL=openai/gpt-4o
```

По умолчанию — [Polza.ai](https://polza.ai/docs): один OpenAI-совместимый API-ключ (берётся в консоли, `polza.ai/dashboard`) даёт доступ к ~400 моделям (OpenAI, Anthropic, Google, Meta и др.) через единый протокол Chat Completions. Имя модели включает провайдера — например `openai/gpt-4o`, `anthropic/claude-sonnet-5`; полный каталог — `polza.ai/models`.

Если хотите другую модель — впишите её вместо `LLM_MODEL`, либо задайте дефолт в конфиге:

```bash
# config/config.yaml (скопируйте из config/config.example.yaml, если ещё не создан)
llm:
  model: "anthropic/claude-sonnet-5"
```

**Не только Polza**: `LLM_BASE_URL` может указывать на любой другой эндпоинт, говорящий по протоколу OpenAI Chat Completions (`/chat/completions`, `choices[].message.content`) — подробности в `docs/specs/serving-config.md`, раздел «LLM endpoint».

### Если LLM вообще не настроен

Ничего не сломается — система автоматически переходит в fallback-режим: отчёт формируется только по находкам static-анализа (pylint/flake8/bandit/semgrep), без LLM-рассуждений. Это штатное поведение (`docs/system-design.md` § 7), не ошибка.

### GitHub-токен (отдельно, опционально)

```bash
# .env
GITHUB_TOKEN=ваш_github_pat
```

Нужен, только если:
- репозиторий **приватный** (права `pull_requests:read`);
- вы хотите **опубликовать** отчёт комментарием в PR (флаг `--publish`, права `pull_requests:write`).

Для публичного репозитория без публикации токен не нужен — анонимный доступ к GitHub API работает в пределах его лимита (~60 запросов/час).

## 2. Как выбрать PR для ревью

Нужна ссылка вида `https://github.com/<owner>/<repo>/pull/<номер>` — любой реальный PR, открытый или уже смёрженный.

**Для реального анализа нужен ещё и локальный checkout репозитория** на ревизии этого PR — Tool Integration Layer и Context Retriever работают с файлами на диске, а не только с текстом diff'а:

```bash
git clone https://github.com/<owner>/<repo>.git /path/to/checkout
cd /path/to/checkout
git fetch origin pull/<номер>/head:pr-<номер>
git checkout pr-<номер>
```

Если checkout не дать (или дать пустую папку) — пайплайн всё равно отработает и вернёт отчёт, но инструменты статического анализа будут помечены `skipped` («no matching-language files») — вы получите только LLM-часть анализа (если LLM настроен), без pylint/flake8/bandit/semgrep.

**Практический совет по выбору PR для первого раза:**
- берите **маленький** PR (десятки строк, 1-2 файла) — быстрее и почти бесплатно (реальная стоимость по замерам — [docs/economics.md](economics.md): $0.004 на 12-строчный diff, ≈$0.04 на 500 строк);
- Python или JavaScript — под них есть реальные static-analysis инструменты (semgrep дополнительно поддерживает JS);
- если реального PR под рукой нет — в репозитории уже есть готовый пример: `tests/fixtures/sample.diff` + локальный checkout `demo/` (см. раздел 3 ниже, «Без реального PR»).

## 3. Как запустить

Два способа — выберите под задачу.

### Способ 1 — CLI (быстрее всего, один запуск = один отчёт в терминал)

```bash
PYTHONPATH=src python -m pr_review_agent.cli analyze \
  --pr-url https://github.com/<owner>/<repo>/pull/<номер> \
  --repo-root /path/to/checkout
```

- Добавьте `--publish`, если хотите, чтобы отчёт опубликовался комментарием в самом PR (нужен `GITHUB_TOKEN` с правом записи) — **осторожно**, это реальное публичное действие в чужом/рабочем репозитории.
- Добавьте `--no-llm`, чтобы намеренно проверить только tools-only режим.
- Уберите `LLM_*` из `.env`, и агенты автоматически не запустятся (см. раздел 1, «Если LLM вообще не настроен») — без флага `--no-llm`.

#### Без реального PR (офлайн-демо на встроенном примере)

```bash
PYTHONPATH=src python -m pr_review_agent.cli analyze \
  --diff-file tests/fixtures/sample.diff \
  --repo-root demo
```

### Способ 2 — асинхронный HTTP API (ack сразу, отчёт — по готовности)

```bash
uvicorn pr_review_agent.gateway:app --app-dir src --reload
```

Затем в другом терминале:

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -H "Content-Type: application/json" \
  -d '{"pr_url": "https://github.com/<owner>/<repo>/pull/<номер>", "repo_root": "/path/to/checkout"}'
# -> {"job_id": "...", "status": "queued"}

curl http://127.0.0.1:8000/jobs/<job_id>
# -> статус (queued/running/completed/completed_partial/failed) + report_markdown, когда готово
```

Реальный пример такого прогона (публичный PR, живой сервер) — [docs/demo-report.md](demo-report.md), раздел 1.

### Проверить, что всё вообще работает (без PR и без LLM)

```bash
PYTHONPATH=src pytest -q
```

90+ тестов должны пройти за секунды — если да, окружение настроено правильно и можно переходить к реальному PR.
