# Спецификация: Tools / APIs

Роль в архитектуре — [system-design.md, раздел 6](../system-design.md#6-tool--api-интеграции).

## GitHub API

- **Назначение:** получение diff и метаданных PR, публикация review-комментария.
- **Контракт:** REST, PAT / GitHub App token с правами `pull_requests:read`, `pull_requests:write` (только комментарии) — без прав на push/merge (governance.md, раздел 5).
- **Timeout:** 10 секунд на запрос, 3 retry с экспоненциальным backoff (1с / 2с / 4с).
- **Rate limits:** caching ответов на уровне job (diff не запрашивается повторно в рамках одного run), batching запросов метаданных.
- **Ошибки:** 4xx (PR не найден / нет доступа) → `job.status=failed`, сообщение пользователю; 5xx/timeout → retry, затем `failed`.
- **Side effects:** единственное побочное действие — публикация одного комментария на PR по завершении job; повторный запуск обновляет тот же комментарий, а не создаёт новый (идемпотентность).

## Static analysis tools (pylint, flake8, bandit, semgrep)

- **Контракт:** запуск как sandboxed subprocess с ограничением CPU/memory, только чтение файлов PR checkout, без сетевого доступа.
- **Выбор инструмента по языку:** каждый инструмент запускается только на файлах поддерживаемого им языка (`tools_runner._TOOL_LANGUAGES`) — pylint/flake8/bandit только на `.py`, semgrep на `.py` и `.js`. Несовпадающие файлы получают `status=skipped, detail="no matching-language files"`, а не тратят время на заведомо бесполезный запуск. Это реализация product-proposal.md, edge case «Поддержка разных языков», и основа агентной метрики «Корректность выбора инструментов» (`tools_runner.expected_tools_for_language`).
- **semgrep — офлайн-набор правил.** Не использует `--config auto`/Semgrep Registry (требует `semgrep login` и сетевой запрос на каждый PR — конфликтует с бюджетом latency/cost). Вместо этого — маленький встроенный ruleset (`config/semgrep-rules.yml`: обнаружение `eval`/`exec`, захардкоженных секретов), офлайн и воспроизводимый в тестах. Компромисс: уже полнота покрытия, чем полный registry, — осознанное решение уровня PoC, а не недосмотр.
- **Timeout:** 15 секунд на инструмент на файл/чанк; при превышении процесс завершается принудительно, результат помечается `partial`.
- **Ошибки:** ненулевой exit code, не являющийся валидным отчётом (краш инструмента) → результат инструмента отбрасывается, остальные инструменты продолжают работу (изоляция отказов).
- **Side effects:** отсутствуют — read-only анализ, инструменты не модифицируют код.
- **Защита:** subprocess выполняется в изолированном окружении (контейнер/sandbox) без доступа к секретам процесса-оркестратора.

## LLM API (Google Gemini API)

- **Контракт:** reasoning и объяснение находок инструментов, генерация review report; код передаётся LLM только как данные — система не выполняет инструкции, встречающиеся в коде (governance.md, раздел 4).
- **Rate limiting:** client-side sliding-window `RateLimiter` (`src/pr_review_agent/rate_limiter.py`), 5 запросов/мин по умолчанию — подобрано под реально наблюдаемый лимит бесплатного тарифа Gemini (`eval/README.md`), настраивается через `llm.requests_per_minute`.
- **Timeout:** 30 секунд на запрос агента, 1 retry (`HttpRetryOptions`, SDK `google-genai`).
- **Ошибки:** timeout/5xx (`ServerError`) → fallback на tools-only отчёт (см. system-design.md, раздел 7 «Failure modes»); 4xx (`ClientError`: invalid request, rate limit, превышение контекста) → урезание контекста (chunking) и один повторный запрос; блокировка контента (`finish_reason` = SAFETY/PROHIBITED_CONTENT/BLOCKLIST/RECITATION/SPII) обрабатывается как недоступность LLM.
- **Side effects:** отсутствуют — LLM не имеет доступа к репозиторию или внешним API, только к переданному контексту.
- **Защита:** перед отправкой — маскирование секретов и фильтрация подозрительных инструкций (Guardrail Pre-Filter); system prompt (`system_instruction`) неизменяем и не собирается из кода PR; `thinking_config.thinking_budget=0`, так как агентам нужен только структурированный JSON-вывод, а не рассуждение вслух.
