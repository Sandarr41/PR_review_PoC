# Data Flow Diagram — что хранится, что логируется, что маскируется

```mermaid
flowchart LR
  U[Пользователь: PR URL]

  subgraph External["Внешние сервисы"]
    GH[(GitHub API)]
    LLM[(LLM Provider)]
  end

  subgraph System["PR Review Agent"]
    ING[PR Ingestion]
    PARSE[Diff Parser]
    MASK["Guardrail Pre-Filter\n(маскирование секретов,\nфильтрация инструкций)"]
    RET[Context Retriever]
    TOOLS[Tool Integration Layer]
    AGENTS[Analysis Agents]
    AGG[Aggregation]
    REP[Report Generator]
    OUTCHK["Guardrail Output Check"]
    PUB[Report Publisher]
    LOG[(Логи / метрики)]
    JOB[(Job Store)]
  end

  U -->|PR URL| ING
  ING <-->|diff, metadata| GH
  ING --> PARSE
  PARSE --> MASK
  MASK -->|код без секретов, без suspicious-инструкций| RET
  RET --> TOOLS
  RET --> AGENTS
  TOOLS -->|находки инструментов| AGENTS
  AGENTS <-->|prompts / completions, без сырых секретов| LLM
  AGENTS --> AGG
  AGG --> REP
  REP --> OUTCHK
  OUTCHK -->|отчёт без секретов/PII| PUB
  PUB -->|комментарий| GH

  ING -.->|pr_id, timestamp, статус| LOG
  TOOLS -.->|agents_used, tools_used, duration| LOG
  AGENTS -.->|agent outcomes, без сырого кода| LOG
  PUB -.->|status, duration_ms| LOG
  ING -.->|обновления статуса| JOB

  classDef sensitive fill:#f66,stroke:#900,color:#fff;
  classDef stored fill:#69c,stroke:#048,color:#fff;
  class MASK,OUTCHK sensitive
  class LOG,JOB stored
```

**Не сохраняется и не логируется** (см. [governance.md](../governance.md), раздел 2–3): полный код PR, секреты/токены/API-ключи, сырой LLM prompt целиком. В Job Store и логах хранятся только метаданные run и агрегированные результаты анализа, ограниченное время (например 30 дней).

**Точки маскирования/проверки** выделены на диаграмме: Guardrail Pre-Filter (до LLM) и Guardrail Output Check (до публикации) — единственные места, где данные покидают систему в сторону LLM Provider или GitHub.
