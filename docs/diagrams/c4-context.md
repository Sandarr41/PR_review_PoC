# C4 — Context Diagram

Система, пользователь и внешние сервисы. Подробности архитектурных решений — [system-design.md](../system-design.md).

```mermaid
C4Context
  title System Context — PR Review Agent

  Person(dev, "Разработчик / Ревьюер", "Отправляет PR на анализ, читает отчёт")

  System(prAgent, "PR Review Agent", "Асинхронно анализирует Pull Request и публикует review report")

  System_Ext(github, "GitHub", "Хостинг репозиториев, PR API, комментарии")
  System_Ext(llm, "LLM Provider (Google Gemini API)", "Reasoning, объяснения, генерация отчёта")

  Rel(dev, github, "Открывает PR, запрашивает анализ")
  Rel(github, prAgent, "PR event / diff (webhook или ручной запуск)")
  Rel(prAgent, github, "Получает diff и метаданные, публикует комментарий")
  Rel(prAgent, llm, "Запросы на reasoning по коду")
  Rel(dev, github, "Читает review-комментарий")
```

**Границы системы:** PR Review Agent не имеет прав на изменение кода, мердж или CI/CD — только чтение diff и публикация комментария (см. [governance.md](../governance.md), раздел 5).
