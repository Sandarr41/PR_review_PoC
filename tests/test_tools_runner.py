from pathlib import Path

from pr_review_agent import tools_runner
from pr_review_agent.tools_runner import run_tools


def test_run_tools_with_no_files_returns_skipped():
    results = run_tools(["pylint", "flake8", "bandit"], [], Path("."))
    assert {r.tool for r in results} == {"pylint", "flake8", "bandit"}
    assert all(r.status == "skipped" for r in results)


def test_run_tools_ignores_unknown_tool_name(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    results = run_tools(["pylint", "not-a-real-tool"], [f], tmp_path)
    assert {r.tool for r in results} == {"pylint"}


def test_run_tools_marks_missing_binary_as_skipped(tmp_path: Path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(tools_runner.shutil, "which", lambda _tool: None)
    results = run_tools(["pylint"], [f], tmp_path)
    assert results[0].status == "skipped"


def test_run_tools_finds_real_issues_end_to_end(tmp_path: Path):
    """Integration test against the actual pylint/flake8/bandit binaries."""
    f = tmp_path / "risky.py"
    f.write_text(
        "import os\n"
        "password = 'hunter2'\n"
        "eval('1 + 1')\n",
        encoding="utf-8",
    )
    results = run_tools(["pylint", "flake8", "bandit"], [f], tmp_path, timeout_seconds=30)
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
