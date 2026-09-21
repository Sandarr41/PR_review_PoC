from pathlib import Path

from pr_review_agent.diff_parser import parse_diff
from pr_review_agent.retriever import retrieve


def test_retrieve_resolves_symbol_referenced_in_diff(tmp_path: Path):
    (tmp_path / "helpers.py").write_text("def foo():\n    return 1\n", encoding="utf-8")

    diff_text = (
        "diff --git a/main.py b/main.py\n"
        "--- a/main.py\n+++ b/main.py\n@@ -0,0 +1,2 @@\n"
        "+def run():\n+    return foo()\n"
    )
    file_diffs = parse_diff(diff_text)

    context = retrieve(file_diffs, tmp_path)
    assert "foo" in context.related_symbols
    assert context.related_symbols["foo"].startswith("helpers.py:")


def test_retrieve_does_not_resolve_symbol_defined_within_the_diff_itself(tmp_path: Path):
    diff_text = (
        "diff --git a/main.py b/main.py\n"
        "--- a/main.py\n+++ b/main.py\n@@ -0,0 +1,2 @@\n"
        "+def foo():\n+    return foo_helper()\n"
    )
    file_diffs = parse_diff(diff_text)
    context = retrieve(file_diffs, tmp_path)
    assert "foo" not in context.related_symbols


def test_retrieve_finds_related_test_file(tmp_path: Path):
    (tmp_path / "test_helpers.py").write_text("def test_foo(): ...\n", encoding="utf-8")
    diff_text = (
        "diff --git a/helpers.py b/helpers.py\n"
        "--- a/helpers.py\n+++ b/helpers.py\n@@ -0,0 +1,1 @@\n+x = 1\n"
    )
    file_diffs = parse_diff(diff_text)
    context = retrieve(file_diffs, tmp_path)
    assert "test_helpers.py" in context.related_tests


def test_retrieve_gracefully_skips_unparsable_python_file(tmp_path: Path):
    (tmp_path / "broken.py").write_text("def bad(:\n", encoding="utf-8")
    diff_text = (
        "diff --git a/main.py b/main.py\n"
        "--- a/main.py\n+++ b/main.py\n@@ -0,0 +1,1 @@\n+bad()\n"
    )
    file_diffs = parse_diff(diff_text)
    context = retrieve(file_diffs, tmp_path)  # must not raise
    assert context.related_symbols == {}


def test_retrieve_on_missing_repo_root_returns_empty_context(tmp_path: Path):
    missing = tmp_path / "does-not-exist"
    diff_text = (
        "diff --git a/main.py b/main.py\n"
        "--- a/main.py\n+++ b/main.py\n@@ -0,0 +1,1 @@\n+foo()\n"
    )
    file_diffs = parse_diff(diff_text)
    context = retrieve(file_diffs, missing)
    assert context.related_symbols == {}
    assert context.related_tests == []
