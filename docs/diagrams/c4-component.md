# C4 — Component Diagram (Orchestrator)

Внутреннее устройство Orchestrator — ядра системы. Правила переходов и stop condition — [specs/agent-orchestrator.md](../specs/agent-orchestrator.md).

```mermaid
C4Component
  title Component Diagram — Orchestrator

  Container_Boundary(orchestrator, "Orchestrator") {
    Component(scheduler, "Agent Scheduler", "Управляет очередью и параллельным запуском агентов, таймауты, retry")
    Component(blackboard, "Run State (Blackboard)", "Общее состояние run: diff-чанки, tool outputs, retrieved context, findings")
    Component(bugAgent, "Bug Detection Agent", "LLM-backed")
    Component(secAgent, "Security Agent", "LLM-backed")
    Component(qualAgent, "Code Quality Agent", "LLM-backed")
    Component(testAgent, "Test Coverage Agent", "LLM-backed")
  }

  Component_Ext(tools, "Tool Integration Layer")
  Component_Ext(retriever, "Context Retriever")
  Component_Ext(guardrail, "Guardrail Pre-Filter")
  Component_Ext(aggregator, "Aggregation Module")
  Component_Ext(rateLimiter, "Rate Limiter (in LLM Client)")

  Rel(scheduler, bugAgent, "dispatch")
  Rel(scheduler, secAgent, "dispatch")
  Rel(scheduler, qualAgent, "dispatch")
  Rel(scheduler, testAgent, "dispatch")

  Rel(bugAgent, blackboard, "read/write findings")
  Rel(secAgent, blackboard, "read/write findings")
  Rel(qualAgent, blackboard, "read/write findings")
  Rel(testAgent, blackboard, "read/write findings")

  Rel(blackboard, tools, "читает tool outputs")
  Rel(blackboard, retriever, "читает retrieved context")
  Rel(guardrail, blackboard, "маскирует секреты до записи в blackboard")

  Rel(bugAgent, rateLimiter, "acquire() перед каждым LLM-вызовом")
  Rel(blackboard, aggregator, "передача findings по завершении run")
```

**Stop condition:** Aggregation запускается, когда все агенты завершены либо истёк run-level timeout — не блокируется одним зависшим агентом (см. [specs/agent-orchestrator.md](../specs/agent-orchestrator.md)).

**Rate limiting — где на самом деле реализовано.** Более ранняя версия этой диаграммы рисовала единый "Circuit Breaker / Rate Limiter" как часть Orchestrator, проверяющий лимиты перед любым внешним вызовом. По факту throttling реализован отдельно для каждого внешнего клиента, а не единым компонентом оркестратора:
- **LLM API** — `RateLimiter` (sliding-window, `src/pr_review_agent/rate_limiter.py`), подключён внутри `LLMClient.complete()` (`src/pr_review_agent/llm_client.py`). Добавлен по итогам реального инцидента — Gemini free tier вернул `429 RESOURCE_EXHAUSTED` под параллельной нагрузкой 4 агентов (`eval/README.md`, "Operational finding"); дефолт 5 запросов/мин подобран под наблюдаемый лимит.
- **GitHub API** — retry с exponential backoff внутри `GitHubClient` (`src/pr_review_agent/github_client.py`), не rate limiter в строгом смысле (нет client-side throttling до превышения лимита GitHub, только реакция на 5xx/429 после факта).

Ни то, ни другое не является полноценным circuit breaker (нет состояния open/half-open/closed с автоматическим восстановлением) — это осознанное упрощение для PoC-масштаба, отражённое здесь, а не молчаливое расхождение с кодом.
