"""Context Retriever (docs/specs/retriever.md).

Lightweight, PoC-scale retrieval: no persistent vector DB. Builds a symbol
index on the fly via AST parsing of the repository checkout, resolves
function/class names referenced in the diff but not defined within it, and
attaches related test files and project convention docs.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .models import FileDiff, RetrievedContext

_CALL_NAME_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
_CONVENTION_FILES = ["README.md", "CONTRIBUTING.md", ".editorconfig"]
_MAX_CONVENTIONS_CHARS = 1500
_MAX_SYMBOLS = 20


def _names_defined_in_diff(file_diffs: list[FileDiff]) -> set[str]:
    defined: set[str] = set()
    for fd in file_diffs:
        if fd.language != "python":
            continue
        for line in fd.patch.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                stripped = line[1:].strip()
                if stripped.startswith("def ") or stripped.startswith("class "):
                    m = re.match(r"(?:def|class)\s+([a-zA-Z_][a-zA-Z0-9_]*)", stripped)
                    if m:
                        defined.add(m.group(1))
    return defined


def _names_referenced_in_diff(file_diffs: list[FileDiff]) -> set[str]:
    referenced: set[str] = set()
    for fd in file_diffs:
        if fd.language != "python":
            continue
        for line in fd.patch.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                for m in _CALL_NAME_RE.finditer(line):
                    referenced.add(m.group(1))
    return referenced


def _index_symbols(repo_root: Path) -> dict[str, str]:
    """AST-based symbol index: function/class name -> "relative/path.py:line"."""
    index: dict[str, str] = {}
    if not repo_root.exists():
        return index
    for py_file in repo_root.rglob("*.py"):
        try:
            source = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError, OSError):
            continue  # graceful degradation — skip files that fail to parse
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                rel_path = py_file.relative_to(repo_root).as_posix()
                index.setdefault(node.name, f"{rel_path}:{node.lineno}")
    return index


def _find_related_tests(repo_root: Path, file_diffs: list[FileDiff]) -> list[str]:
    if not repo_root.exists():
        return []
    related: list[str] = []
    for fd in file_diffs:
        stem = Path(fd.path).stem
        for pattern in (f"test_{stem}.py", f"{stem}_test.py"):
            for match in repo_root.rglob(pattern):
                rel = match.relative_to(repo_root).as_posix()
                if rel not in related:
                    related.append(rel)
    return related


def _read_conventions(repo_root: Path) -> str | None:
    if not repo_root.exists():
        return None
    chunks = []
    for name in _CONVENTION_FILES:
        f = repo_root / name
        if f.exists() and f.is_file():
            try:
                chunks.append(f.read_text(encoding="utf-8", errors="ignore")[:_MAX_CONVENTIONS_CHARS])
            except OSError:
                continue
    return "\n---\n".join(chunks) if chunks else None


def retrieve(file_diffs: list[FileDiff], repo_root: Path) -> RetrievedContext:
    """Best-effort context retrieval. Never raises — returns an empty/partial
    RetrievedContext if the repo checkout is unavailable or symbols can't be
    resolved (e.g. highly dynamic code using getattr/eval)."""
    try:
        defined = _names_defined_in_diff(file_diffs)
        referenced = _names_referenced_in_diff(file_diffs) - defined
        symbol_index = _index_symbols(repo_root)

        related_symbols = {
            name: symbol_index[name]
            for name in list(referenced)[:_MAX_SYMBOLS]
            if name in symbol_index
        }
        related_tests = _find_related_tests(repo_root, file_diffs)
        conventions = _read_conventions(repo_root)

        return RetrievedContext(
            related_symbols=related_symbols,
            related_tests=related_tests,
            conventions_excerpt=conventions,
        )
    except Exception:
        # Retrieval is best-effort augmentation, not a required step — any
        # unexpected failure degrades to "no extra context" rather than
        # failing the run (docs/specs/retriever.md, Ограничения).
        return RetrievedContext()
