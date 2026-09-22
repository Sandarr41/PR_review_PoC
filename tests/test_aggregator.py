from pr_review_agent.aggregator import aggregate
from pr_review_agent.models import Category, Finding, Severity


def _f(**kwargs) -> Finding:
    defaults = dict(
        file="a.py", category=Category.BUG, message="msg", source="bug_agent", severity=Severity.MEDIUM, line=1
    )
    defaults.update(kwargs)
    return Finding(**defaults)


def test_aggregate_dedups_identical_findings():
    findings = [_f(), _f()]
    result = aggregate(findings)
    assert len(result) == 1


def test_aggregate_prefers_tool_over_agent_on_same_finding():
    tool_finding = _f(source="bandit", severity=Severity.LOW)
    agent_finding = _f(source="security_agent", severity=Severity.LOW)
    result = aggregate([agent_finding, tool_finding])
    assert len(result) == 1
    assert result[0].source == "bandit"


def test_aggregate_ranks_by_severity():
    low = _f(line=1, message="low issue", severity=Severity.LOW)
    critical = _f(line=2, message="critical issue", severity=Severity.CRITICAL)
    medium = _f(line=3, message="medium issue", severity=Severity.MEDIUM)
    result = aggregate([low, critical, medium])
    assert [f.severity for f in result] == [Severity.CRITICAL, Severity.MEDIUM, Severity.LOW]


def test_aggregate_top_n_limits_results():
    findings = [_f(line=i, message=f"issue {i}") for i in range(10)]
    result = aggregate(findings, top_n=5)
    assert len(result) == 5


def test_aggregate_empty_input():
    assert aggregate([]) == []


def test_aggregate_merges_same_bug_reported_across_categories():
    """The exact scenario from eval/README.md's real-Gemini run: bug_agent,
    security_agent and quality_agent each independently describe the same
    SQL injection at the same line, in their own words."""
    bug = _f(
        line=7,
        category=Category.BUG,
        source="bug_agent",
        message="SQL injection vulnerability: user_id is concatenated directly into "
        "the query string without parameterization. Use cursor.execute(...) instead.",
    )
    security = _f(
        line=7,
        category=Category.SECURITY,
        source="security_agent",
        message="SQL injection vulnerability: user_id is concatenated directly into the "
        "query string without sanitization or parameterization. An attacker can inject "
        "arbitrary SQL.",
    )
    quality = _f(
        line=7,
        category=Category.QUALITY,
        source="quality_agent",
        message="SQL injection vulnerability: user_id is concatenated directly into the "
        "query string. Use parameterized queries: cursor.execute(...)",
    )
    result = aggregate([bug, security, quality])
    assert len(result) == 1
    # security/bug are more actionable than a generic quality note about the same line
    assert result[0].category in (Category.SECURITY, Category.BUG)


def test_aggregate_merges_tool_and_agent_report_of_same_bug_and_keeps_explanation():
    tool_finding = _f(
        line=7,
        category=Category.SECURITY,
        source="bandit",
        severity=Severity.HIGH,
        message="B608 Possible SQL injection vector through string-based query construction.",
    )
    agent_finding = _f(
        line=7,
        category=Category.SECURITY,
        source="security_agent",
        severity=Severity.CRITICAL,
        message="SQL injection vulnerability: user_id is concatenated directly into the "
        "query string without sanitization or parameterization.",
    )
    result = aggregate([tool_finding, agent_finding])
    assert len(result) == 1
    assert result[0].source == "bandit"  # tool wins as canonical (tools-first)
    assert result[0].explanation is not None
    assert "concatenated" in result[0].explanation  # agent's detail wasn't discarded


def test_aggregate_does_not_merge_unrelated_findings_that_share_a_generic_word():
    module_docstring = _f(line=1, category=Category.QUALITY, source="pylint", message="Missing module docstring")
    unrelated_low_severity = _f(
        line=2, category=Category.BUG, source="bug_agent", message="Off-by-one issue in loop bound"
    )
    result = aggregate([module_docstring, unrelated_low_severity])
    assert len(result) == 2


def test_aggregate_does_not_merge_across_distant_lines():
    early = _f(line=1, message="SQL injection vulnerability due to string concatenation in query")
    late = _f(line=50, message="SQL injection vulnerability due to string concatenation in query")
    result = aggregate([early, late])
    assert len(result) == 2


def test_aggregate_top_n_excludes_pure_lint_style_noise():
    real_bug = _f(line=7, category=Category.SECURITY, source="bandit", severity=Severity.HIGH, message="SQL injection")
    style_1 = _f(line=1, category=Category.QUALITY, source="pylint", severity=Severity.LOW, message="Missing module docstring")
    style_2 = _f(line=1, category=Category.QUALITY, source="pylint", severity=Severity.LOW, message="Missing function docstring")

    top = aggregate([real_bug, style_1, style_2], top_n=5)
    assert top == [real_bug]

    full_report_list = aggregate([real_bug, style_1, style_2])
    assert len(full_report_list) == 3  # style findings still present for the full report


def test_aggregate_top_n_falls_back_to_style_noise_when_nothing_else_exists():
    style_1 = _f(line=1, category=Category.QUALITY, source="pylint", severity=Severity.LOW, message="Missing module docstring")
    assert aggregate([style_1], top_n=5) == []
