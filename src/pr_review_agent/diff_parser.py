"""Diff Parser module (docs/system-design.md, раздел 2).

Разбивает unified diff (как отдаёт GitHub API в формате .diff) по файлам,
определяет язык и готовит чанки для больших PR.
"""
from __future__ import annotations

import re

from .models import FileDiff

_DIFF_GIT_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
_HUNK_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,\d+)? \+(?P<new_start>\d+)(?:,\d+)? @@")

_LANGUAGE_BY_EXT = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".rb": "ruby",
    ".rs": "rust",
    ".md": "markdown",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
}


def detect_language(path: str) -> str:
    for ext, lang in _LANGUAGE_BY_EXT.items():
        if path.endswith(ext):
            return lang
    return "unknown"


def parse_diff(diff_text: str) -> list[FileDiff]:
    """Parses a unified diff (GitHub `.diff` format) into per-file FileDiff objects."""
    if not diff_text.strip():
        return []

    lines = diff_text.splitlines()
    files: list[FileDiff] = []

    current_path: str | None = None
    current_patch_lines: list[str] = []
    current_added: list[int] = []
    current_removed: list[int] = []
    is_new_file = False
    is_deleted_file = False
    new_line_no = 0
    old_line_no = 0

    def flush():
        nonlocal current_path, current_patch_lines, current_added, current_removed
        nonlocal is_new_file, is_deleted_file
        if current_path is not None:
            files.append(
                FileDiff(
                    path=current_path,
                    language=detect_language(current_path),
                    patch="\n".join(current_patch_lines),
                    added_lines=list(current_added),
                    removed_lines=list(current_removed),
                    is_new_file=is_new_file,
                    is_deleted_file=is_deleted_file,
                )
            )
        current_path = None
        current_patch_lines = []
        current_added = []
        current_removed = []
        is_new_file = False
        is_deleted_file = False

    for line in lines:
        header_match = _DIFF_GIT_RE.match(line)
        if header_match:
            flush()
            current_path = header_match.group("b")
            continue

        if current_path is None:
            continue  # preamble before the first `diff --git` header

        if line.startswith("new file mode"):
            is_new_file = True
        elif line.startswith("deleted file mode"):
            is_deleted_file = True

        hunk_match = _HUNK_RE.match(line)
        if hunk_match:
            old_line_no = int(hunk_match.group("old_start"))
            new_line_no = int(hunk_match.group("new_start"))
            current_patch_lines.append(line)
            continue

        if line.startswith("+") and not line.startswith("+++"):
            current_added.append(new_line_no)
            new_line_no += 1
        elif line.startswith("-") and not line.startswith("---"):
            current_removed.append(old_line_no)
            old_line_no += 1
        elif not line.startswith(("+++", "---", "index ", "similarity index", "rename ")):
            new_line_no += 1
            old_line_no += 1

        current_patch_lines.append(line)

    flush()
    return files


def total_changed_lines(file_diffs: list[FileDiff]) -> int:
    return sum(len(f.added_lines) + len(f.removed_lines) for f in file_diffs)


def chunk_diff(file_diffs: list[FileDiff], chunk_size_lines: int) -> list[list[FileDiff]]:
    """Splits file diffs into chunks whose cumulative changed-line count stays
    under ``chunk_size_lines`` (docs/specs/memory-context.md — Chunking)."""
    if chunk_size_lines <= 0:
        return [file_diffs] if file_diffs else []

    chunks: list[list[FileDiff]] = []
    current_chunk: list[FileDiff] = []
    current_size = 0

    for fd in file_diffs:
        fd_size = len(fd.added_lines) + len(fd.removed_lines)
        if current_chunk and current_size + fd_size > chunk_size_lines:
            chunks.append(current_chunk)
            current_chunk = []
            current_size = 0
        current_chunk.append(fd)
        current_size += fd_size

    if current_chunk:
        chunks.append(current_chunk)

    return chunks
