"""Guardrail Pre-/Post-Filter (docs/governance.md, разделы 3–4;
docs/system-design.md, раздел 7).

Two responsibilities:
  1. Pre-filter — mask secrets and neutralize suspicious "instructions to the
     model" hidden in code comments, before anything is sent to the LLM.
  2. Output check — scan the generated report for leaked secrets before it is
     published.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

REDACTED = "***REDACTED***"

_SECRET_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)\b(api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}['\"]?"),
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),  # generic secret-key-like tokens (e.g. sk-...)
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),  # GitHub personal access token
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key id
    re.compile(r"(?i)\b(secret|token|password|passwd)\s*[:=]\s*['\"]?[A-Za-z0-9_\-!@#$%^&*]{8,}['\"]?"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
]

_INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)ignore (all )?previous instructions"),
    re.compile(r"(?i)disregard (the )?(system|above) prompt"),
    re.compile(r"(?i)you are now\b"),
    re.compile(r"(?i)say (the code is|that (this|the) code is) (correct|safe|fine)"),
    re.compile(r"(?i)do not (report|flag|mention) (any|this) (issue|bug|vulnerability)"),
]


@dataclass
class GuardrailReport:
    masked_secrets_count: int = 0
    flagged_injection_snippets: list[str] = field(default_factory=list)


def mask_secrets(text: str, extra_patterns: list[str] | None = None) -> tuple[str, int]:
    """Replaces anything matching a secret pattern with a redaction marker.

    Returns the masked text and the number of matches redacted.
    """
    patterns = list(_SECRET_PATTERNS)
    for p in extra_patterns or []:
        patterns.append(re.compile(p))

    count = 0

    def _sub(pattern: re.Pattern, s: str) -> str:
        nonlocal count
        def repl(_m: re.Match) -> str:
            nonlocal count
            count += 1
            return REDACTED
        return pattern.sub(repl, s)

    masked = text
    for pattern in patterns:
        masked = _sub(pattern, masked)
    return masked, count


def filter_suspicious_instructions(
    text: str, extra_patterns: list[str] | None = None
) -> tuple[str, list[str]]:
    """Neutralizes lines that look like an attempt to instruct the LLM
    from within the code (prompt injection via comments).

    Matched lines are not silently dropped — they are replaced with a
    placeholder so line numbers in the diff stay stable, and the original
    snippet is returned for logging/audit (never sent to the LLM).
    """
    patterns = list(_INJECTION_PATTERNS)
    for p in extra_patterns or []:
        patterns.append(re.compile(p))

    flagged: list[str] = []
    out_lines: list[str] = []
    for line in text.splitlines():
        matched = any(p.search(line) for p in patterns)
        if matched:
            flagged.append(line.strip())
            out_lines.append("[REDACTED: suspicious instruction removed by guardrail]")
        else:
            out_lines.append(line)
    return "\n".join(out_lines), flagged


def pre_filter(diff_text: str, extra_secret_patterns: list[str] | None = None,
                extra_injection_patterns: list[str] | None = None) -> tuple[str, GuardrailReport]:
    """Runs both pre-filter steps and returns the sanitized text plus a report."""
    masked, secret_count = mask_secrets(diff_text, extra_secret_patterns)
    clean, flagged = filter_suspicious_instructions(masked, extra_injection_patterns)
    return clean, GuardrailReport(masked_secrets_count=secret_count, flagged_injection_snippets=flagged)


def output_check(report_markdown: str) -> tuple[str, bool]:
    """Guardrail Output Check — scans the final report before publication.

    Returns (possibly-masked report, was_clean). ``was_clean`` is False if
    anything had to be redacted, so the caller can log/flag it.
    """
    masked, count = mask_secrets(report_markdown)
    return masked, count == 0
