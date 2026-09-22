"""Report Generator (docs/system-design.md § 2).

Formats the aggregated findings into the markdown report shape shown in
product-proposal.md's example (grouped by category).
"""
from __future__ import annotations

from .models import Category, Finding

_CATEGORY_TITLES = {
    Category.BUG: "Potential Bugs",
    Category.SECURITY: "Security",
    Category.QUALITY: "Code Quality",
    Category.TESTING: "Testing",
}

MARKER = "<!-- pr-review-agent:report -->"


def generate_report(
    findings: list[Finding],
    partial_notes: list[str] | None = None,
    llm_unavailable: bool = False,
) -> str:
    lines = [MARKER, "# PR Review Summary", ""]

    if llm_unavailable:
        lines.append(
            "> ⚠️ LLM reasoning was unavailable for this run — this report only "
            "contains findings from static analysis tools (see docs/system-design.md § 7, Failure modes)."
        )
        lines.append("")

    if partial_notes:
        for note in partial_notes:
            lines.append(f"> ⚠️ {note}")
        lines.append("")

    if not findings:
        lines.append("No issues found.")
        return "\n".join(lines)

    by_category: dict[Category, list[Finding]] = {}
    for f in findings:
        by_category.setdefault(f.category, []).append(f)

    for category in (Category.BUG, Category.SECURITY, Category.QUALITY, Category.TESTING):
        items = by_category.get(category)
        if not items:
            continue
        lines.append(f"## {_CATEGORY_TITLES[category]}")
        for item in items:
            location = f"{item.file}:{item.line}" if item.line else item.file
            lines.append(f"- [{item.severity.value}] {location} — {item.message} (`{item.source}`)")
            if item.explanation:
                # Set by aggregator._merge_cluster() when a terse tool
                # finding absorbed a richer duplicate description from an
                # agent — keep that detail instead of dropping it silently.
                lines.append(f"  ↳ {item.explanation}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"
