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
    Component(circuitBreaker, "Circuit Breaker / Rate Limiter", "Ограничивает вызовы GitHub API и LLM API")
  }

  Component_Ext(tools, "Tool Integration Layer")
  Component_Ext(retriever, "Context Retriever")
  Component_Ext(guardrail, "Guardrail Pre-Filter")
  Component_Ext(aggregator, "Aggregation Module")

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

  Rel(scheduler, circuitBreaker, "проверка лимитов перед внешним вызовом")
  Rel(blackboard, aggregator, "передача findings по завершении run")
```

**Stop condition:** Aggregation запускается, когда все агенты завершены либо истёк run-level timeout — не блокируется одним зависшим агентом (см. [specs/agent-orchestrator.md](../specs/agent-orchestrator.md)).
