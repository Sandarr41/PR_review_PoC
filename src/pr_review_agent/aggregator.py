"""Aggregation Module (docs/system-design.md § 2).

Deduplicates findings coming from multiple tools/agents and ranks them so
the report — and the precision@5 product metric (product-proposal.md) —
can surface the most important issues first.

Two dedup passes, in order:
  1. Exact dedup by (file, line, category, normalized message) — catches
     literal duplicates.
  2. Near-duplicate merge across categories/sources at the same location —
     catches the case where bug_agent, security_agent and quality_agent (or
     an agent and a tool) each independently describe the *same* underlying
     issue in their own words. Without this, one real bug fills 3-4 top-N
     slots instead of 1, which is the dominant cause of low precision@N
     observed in eval/README.md.
"""
from __future__ import annotations

import dataclasses
import re

from .models import SEVERITY_RANK, Category, Finding, Severity

# Preference order when the same finding is reported by several sources —
# a tool-confirmed finding is kept over a pure LLM guess (docs/system-design.md
# § 1, decision 3: "tools-first, LLM-second").
_TOOL_SOURCES = {"bandit", "pylint", "flake8"}

_SOURCE_PRIORITY = {
    "bandit": 0,
    "pylint": 0,
    "flake8": 0,
    "bug_agent": 1,
    "security_agent": 1,
    "quality_agent": 1,
    "test_coverage_agent": 1,
}

# Used only to pick a canonical category when the same issue is classified
# differently by different agents (e.g. a SQL injection reported under both
# "bug" and "security") — security/bug findings are more actionable than a
# generic quality note about the same line.
_CATEGORY_PRIORITY = {
    Category.SECURITY: 0,
    Category.BUG: 1,
    Category.TESTING: 2,
    Category.QUALITY: 3,
}

_LINE_MERGE_TOLERANCE = 2
# Both gates must pass — Jaccard alone lets a couple of generic shared words
# ("issue", "missing") falsely match unrelated findings; requiring an
# absolute minimum of shared *significant* words as well filters that out
# while still catching real duplicates (see tests/test_aggregator.py for the
# concrete false-positive this was tuned against).
_SIMILARITY_THRESHOLD = 0.2
_MIN_SHARED_WORDS = 3

_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "to", "of",
    "in", "on", "for", "and", "or", "this", "that", "it", "its", "into",
    "with", "without", "via", "use", "using", "used", "instead", "can",
    "consider", "you", "your", "should", "will", "not", "no", "so", "as",
    "at", "by", "from", "if", "when", "than", "then", "which", "e", "g",
    "issue", "issues", "problem", "problems", "error", "errors", "found",
    "code", "line", "lines",
}

_WORD_RE = re.compile(r"[a-zA-Z']+")


def _significant_words(message: str) -> set[str]:
    words = _WORD_RE.findall(message.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _is_near_duplicate(a: str, b: str) -> bool:
    """True when two finding messages plausibly describe the same
    underlying issue — a lightweight, dependency-free stand-in for semantic
    similarity, good enough to catch "same bug, different wording" without
    an extra LLM call (which would defeat the point of a cheap aggregation
    step)."""
    words_a, words_b = _significant_words(a), _significant_words(b)
    if not words_a or not words_b:
        return False
    shared = words_a & words_b
    if len(shared) < _MIN_SHARED_WORDS:
        return False
    jaccard = len(shared) / len(words_a | words_b)
    return jaccard >= _SIMILARITY_THRESHOLD


def _is_style_noise(finding: Finding) -> bool:
    """Pure lint-style nits (docstrings, formatting) that are real but not
    the kind of thing precision@N should be scored against — they shouldn't
    compete with actual bugs/security/test-coverage findings for top-N slots
    (eval/README.md, "pylint-noise" finding)."""
    return finding.source in _TOOL_SOURCES and finding.category == Category.QUALITY and finding.severity == Severity.LOW


def _exact_dedup(findings: list[Finding]) -> list[Finding]:
    deduped: dict[tuple, Finding] = {}
    for finding in findings:
        key = finding.dedup_key()
        existing = deduped.get(key)
        if existing is None:
            deduped[key] = finding
            continue
        existing_priority = _SOURCE_PRIORITY.get(existing.source, 2)
        new_priority = _SOURCE_PRIORITY.get(finding.source, 2)
        if new_priority < existing_priority:
            deduped[key] = finding
        elif new_priority == existing_priority and SEVERITY_RANK[finding.severity] < SEVERITY_RANK[existing.severity]:
            deduped[key] = finding
    return list(deduped.values())


def _merge_cluster(cluster: list[Finding]) -> Finding:
    canonical = min(
        cluster,
        key=lambda f: (
            _SOURCE_PRIORITY.get(f.source, 2),
            _CATEGORY_PRIORITY.get(f.category, 9),
            SEVERITY_RANK[f.severity],
        ),
    )
    if len(cluster) == 1:
        return canonical

    # A terse tool finding ("B608 Possible SQL injection...") merged with a
    # richer agent explanation of the same issue shouldn't lose that
    # explanation — attach the most detailed alternate description instead
    # of silently discarding it.
    if canonical.explanation is None:
        alternates = [f.message for f in cluster if f is not canonical and f.message != canonical.message]
        if alternates:
            best = max(alternates, key=len)
            canonical = dataclasses.replace(canonical, explanation=best)
    return canonical


def _near_duplicate_merge(findings: list[Finding]) -> list[Finding]:
    """Clusters findings at the same file within ±_LINE_MERGE_TOLERANCE lines
    whose messages are similar enough to plausibly describe the same
    underlying issue, then collapses each cluster to one canonical Finding.
    Deliberately not restricted to same-category — that's the whole point
    (a bug reported as both "bug" and "security" is still one bug)."""
    order = sorted(range(len(findings)), key=lambda i: (findings[i].file, findings[i].line or 0))
    used = [False] * len(findings)
    result: list[Finding] = []

    for pos, i in enumerate(order):
        if used[i]:
            continue
        cluster = [i]
        used[i] = True
        for j in order[pos + 1:]:
            if used[j]:
                continue
            fi, fj = findings[i], findings[j]
            if fi.file != fj.file:
                continue
            if fi.line is not None and fj.line is not None and abs(fi.line - fj.line) > _LINE_MERGE_TOLERANCE:
                continue
            if _is_near_duplicate(fi.message, fj.message):
                cluster.append(j)
                used[j] = True
        result.append(_merge_cluster([findings[k] for k in cluster]))

    return result


def aggregate(findings: list[Finding], top_n: int | None = None) -> list[Finding]:
    """Dedups exact duplicates, merges near-duplicates across categories/
    sources at the same location, and ranks by severity then source
    reliability. If ``top_n`` is given, returns only the top N *substantive*
    findings (pure lint-style noise is excluded from top-N — see
    ``_is_style_noise`` — but still appears in the unranked full list used
    for the report)."""
    merged = _near_duplicate_merge(_exact_dedup(findings))

    ranked = sorted(
        merged,
        key=lambda f: (SEVERITY_RANK[f.severity], _SOURCE_PRIORITY.get(f.source, 2), f.file, f.line or 0),
    )

    if top_n is None:
        return ranked

    substantive = [f for f in ranked if not _is_style_noise(f)]
    return substantive[:top_n]
