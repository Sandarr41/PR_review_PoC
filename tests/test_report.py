from pr_review_agent.models import Category, Finding, Severity
from pr_review_agent.report import MARKER, generate_report


def test_generate_report_empty_findings():
    report = generate_report([])
    assert "No issues found." in report
    assert report.startswith(MARKER)


def test_generate_report_groups_by_category():
    findings = [
        Finding(file="a.py", line=1, category=Category.BUG, message="bug msg", source="bug_agent"),
        Finding(file="b.py", line=2, category=Category.SECURITY, message="sec msg", source="bandit"),
    ]
    report = generate_report(findings)
    assert "Potential Bugs" in report
    assert "Security" in report
    assert "bug msg" in report
    assert "sec msg" in report


def test_generate_report_notes_llm_unavailable():
    report = generate_report([], llm_unavailable=True)
    assert "LLM reasoning was unavailable" in report


def test_generate_report_includes_partial_notes():
    report = generate_report([], partial_notes=["Tool 'bandit' status=error: crashed"])
    assert "bandit" in report
    assert "crashed" in report
