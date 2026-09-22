from pathlib import Path

from pr_review_agent import tools_runner
from pr_review_agent.models import FileDiff
from pr_review_agent.tools_runner import expected_tools_for_language, run_tools


def _fd(path: str, language: str = "python") -> FileDiff:
    return FileDiff(path=path, language=language, patch="")


def test_run_tools_with_no_files_returns_skipped():
    results = run_tools(["pylint", "flake8", "bandit"], [], Path("."))
    assert {r.tool for r in results} == {"pylint", "flake8", "bandit"}
    assert all(r.status == "skipped" for r in results)


def test_run_tools_ignores_unknown_tool_name(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    results = run_tools(["pylint", "not-a-real-tool"], [_fd("a.py")], tmp_path)
    assert {r.tool for r in results} == {"pylint"}


def test_run_tools_marks_missing_binary_as_skipped(tmp_path: Path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(tools_runner.shutil, "which", lambda _tool: None)
    results = run_tools(["pylint"], [_fd("a.py")], tmp_path)
    assert results[0].status == "skipped"


def test_run_tools_skips_non_python_files_for_python_only_tools(tmp_path: Path):
    """Language-aware tool selection (product-proposal.md 'Поддержка разных
    языков' edge case) — a .js file must never be handed to pylint/flake8/bandit."""
    js_file = tmp_path / "app.js"
    js_file.write_text("var x = 1;\n", encoding="utf-8")
    results = run_tools(["pylint", "flake8", "bandit"], [_fd("app.js", language="javascript")], tmp_path)
    assert all(r.status == "skipped" for r in results)
    assert all("matching-language" in (r.detail or "") for r in results)


def test_expected_tools_for_language_matches_actual_selection(tmp_path: Path):
    enabled = ["pylint", "flake8", "bandit"]
    assert expected_tools_for_language("python", enabled) == {"pylint", "flake8", "bandit"}
    assert expected_tools_for_language("javascript", enabled) == set()

    py_file = tmp_path / "a.py"
    py_file.write_text("x = 1\n", encoding="utf-8")
    results = run_tools(enabled, [_fd("a.py")], tmp_path)
    actually_used = {r.tool for r in results if r.status != "skipped"}
    assert actually_used == expected_tools_for_language("python", enabled)


def test_expected_tools_for_language_includes_semgrep_for_javascript():
    enabled = ["pylint", "flake8", "bandit", "semgrep"]
    assert expected_tools_for_language("javascript", enabled) == {"semgrep"}
    assert expected_tools_for_language("python", enabled) == {"pylint", "flake8", "bandit", "semgrep"}


def test_run_tools_finds_real_issues_end_to_end(tmp_path: Path):
    """Integration test against the actual pylint/flake8/bandit binaries."""
    f = tmp_path / "risky.py"
    f.write_text(
        "import os\n"
        "password = 'hunter2'\n"
        "eval('1 + 1')\n",
        encoding="utf-8",
    )
    results = run_tools(["pylint", "flake8", "bandit"], [_fd("risky.py")], tmp_path, timeout_seconds=30)
    by_tool = {r.tool: r for r in results}

    assert by_tool["flake8"].status == "ok"
    assert any("F401" in finding.message for finding in by_tool["flake8"].findings)

    assert by_tool["bandit"].status == "ok"
    assert len(by_tool["bandit"].findings) > 0
    assert all(finding.category.value == "security" for finding in by_tool["bandit"].findings)

    # Paths must be normalized (no "./" prefix, forward slashes) so they line
    # up with the diff's own path format for dedup in the aggregator.
    for finding in by_tool["flake8"].findings + by_tool["bandit"].findings:
        assert finding.file == "risky.py"


def test_run_semgrep_finds_issues_in_python_and_javascript(tmp_path: Path):
    """Integration test against the real semgrep binary and the bundled
    offline ruleset (config/semgrep-rules.yml) — covers the cross-language
    capability pylint/flake8/bandit don't have."""
    py_file = tmp_path / "risky.py"
    py_file.write_text("password = 'hunter2'\neval('1 + 1')\n", encoding="utf-8")
    js_file = tmp_path / "risky.js"
    js_file.write_text("var password = 'hunter2';\neval('1+1');\n", encoding="utf-8")

    results = run_tools(
        ["semgrep"],
        [_fd("risky.py", "python"), _fd("risky.js", "javascript")],
        tmp_path,
        timeout_seconds=30,
    )
    assert len(results) == 1
    result = results[0]
    assert result.status == "ok", result.detail

    files_with_findings = {f.file for f in result.findings}
    assert "risky.py" in files_with_findings
    assert "risky.js" in files_with_findings
    assert all(f.category.value == "security" for f in result.findings)


def test_run_tools_selects_semgrep_only_for_a_javascript_file(tmp_path: Path):
    js_file = tmp_path / "risky.js"
    js_file.write_text("eval('1+1');\n", encoding="utf-8")

    results = run_tools(
        ["pylint", "flake8", "bandit", "semgrep"],
        [_fd("risky.js", "javascript")],
        tmp_path,
        timeout_seconds=30,
    )
    by_tool = {r.tool: r for r in results}
    assert by_tool["pylint"].status == "skipped"
    assert by_tool["flake8"].status == "skipped"
    assert by_tool["bandit"].status == "skipped"
    assert by_tool["semgrep"].status == "ok"
    assert len(by_tool["semgrep"].findings) > 0
