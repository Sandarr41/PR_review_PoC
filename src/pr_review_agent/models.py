from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_PARTIAL = "completed_partial"
    FAILED = "failed"


class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_RANK = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}


class Category(str, Enum):
    BUG = "bug"
    SECURITY = "security"
    QUALITY = "quality"
    TESTING = "testing"


@dataclass
class Finding:
    """A single potential issue surfaced by a tool or an agent.

    See docs/specs/agent-orchestrator.md and docs/diagrams/data-flow.md.
    """

    file: str
    category: Category
    message: str
    source: str  # e.g. "pylint", "bandit", "bug_agent", "security_agent"
    severity: Severity = Severity.MEDIUM
    line: Optional[int] = None
    explanation: Optional[str] = None

    def dedup_key(self) -> tuple:
        return (self.file, self.line, self.category, self.message.strip().lower())


@dataclass
class FileDiff:
    path: str
    language: str
    patch: str
    added_lines: list[int] = field(default_factory=list)
    removed_lines: list[int] = field(default_factory=list)
    is_new_file: bool = False
    is_deleted_file: bool = False


@dataclass
class ToolResult:
    tool: str
    findings: list[Finding]
    status: str  # "ok" | "partial" | "error" | "skipped"
    detail: Optional[str] = None


@dataclass
class RetrievedContext:
    related_symbols: dict[str, str] = field(default_factory=dict)  # symbol -> location
    related_tests: list[str] = field(default_factory=list)
    conventions_excerpt: Optional[str] = None


@dataclass
class Job:
    job_id: str
    pr_ref: str
    status: JobStatus = JobStatus.QUEUED
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    agents_used: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    report_markdown: Optional[str] = None
    # Real measured LLM usage for this run (docs/economics.md) — 0 when the
    # run used no LLM (--no-llm / fallback), never a guess.
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    llm_cost_usd: float = 0.0

    def touch(self, status: Optional[JobStatus] = None) -> None:
        if status is not None:
            self.status = status
        self.updated_at = datetime.now(timezone.utc)
