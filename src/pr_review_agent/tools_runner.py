"""Tool Integration Layer (docs/specs/tools-api.md).

Runs static analysis tools (pylint, flake8, bandit) as sandboxed subprocesses
against the changed files in a local repo checkout, in parallel, isolating
the failure of one tool from the others.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .models import Category, Finding, Severity, ToolResult

_FLAKE8_LINE_RE = re.compile(r"^(?P<path>[^:]+):(?P<line>\d+):(?P<col>\d+): (?P<code>\w+) (?P<msg>.*)$")

_BANDIT_SEVERITY_MAP = {
    "HIGH": Severity.CRITICAL,
    "MEDIUM": Severity.HIGH,
    "LOW": Severity.MEDIUM,
}

_PYLINT_SEVERITY_MAP = {
    "fatal": Severity.CRITICAL,
    "error": Severity.HIGH,
    "warning": Severity.MEDIUM,
    "convention": Severity.LOW,
    "refactor": Severity.LOW,
}


def _tool_available(tool: str) -> bool:
    return shutil.which(tool) is not None


def _normalize_path(path_str: str) -> str:
    """Normalizes a tool-reported path to match the diff's own path format
    (forward slashes, no leading "./"), so Finding.dedup_key() correctly
    matches a tool finding against an agent finding at the same location."""
    normalized = path_str.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _run_pylint(files: list[str], cwd: Path, timeout: int) -> ToolResult:
    if not _tool_available("pylint"):
        return ToolResult(tool="pylint", findings=[], status="skipped", detail="pylint not installed")
    try:
        proc = subprocess.run(
            ["pylint", "--output-format=json", *files],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(tool="pylint", findings=[], status="partial", detail=f"timeout after {timeout}s")
    except FileNotFoundError:
        return ToolResult(tool="pylint", findings=[], status="skipped", detail="pylint not installed")

    # pylint exits non-zero whenever it reports findings — that's expected,
    # not a crash. Only a missing/unparsable JSON payload means the tool crashed.
    try:
        raw = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return ToolResult(tool="pylint", findings=[], status="error", detail=proc.stderr[:500])

    findings = [
        Finding(
            file=_normalize_path(item.get("path", "?")),
            line=item.get("line"),
            category=Category.QUALITY,
            severity=_PYLINT_SEVERITY_MAP.get(item.get("type", "warning"), Severity.MEDIUM),
            message=item.get("message", ""),
            source="pylint",
        )
        for item in raw
    ]
    return ToolResult(tool="pylint", findings=findings, status="ok")


def _run_flake8(files: list[str], cwd: Path, timeout: int) -> ToolResult:
    if not _tool_available("flake8"):
        return ToolResult(tool="flake8", findings=[], status="skipped", detail="flake8 not installed")
    try:
        proc = subprocess.run(
            ["flake8", *files],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(tool="flake8", findings=[], status="partial", detail=f"timeout after {timeout}s")
    except FileNotFoundError:
        return ToolResult(tool="flake8", findings=[], status="skipped", detail="flake8 not installed")

    findings = []
    for line in (proc.stdout or "").splitlines():
        m = _FLAKE8_LINE_RE.match(line)
        if not m:
            continue
        code = m.group("code")
        severity = Severity.LOW if code.startswith(("E1", "E2", "E3", "W")) else Severity.MEDIUM
        findings.append(
            Finding(
                file=_normalize_path(m.group("path")),
                line=int(m.group("line")),
                category=Category.QUALITY,
                severity=severity,
                message=f"{code} {m.group('msg')}",
                source="flake8",
            )
        )
    return ToolResult(tool="flake8", findings=findings, status="ok")


def _run_bandit(files: list[str], cwd: Path, timeout: int) -> ToolResult:
    if not _tool_available("bandit"):
        return ToolResult(tool="bandit", findings=[], status="skipped", detail="bandit not installed")
    try:
        proc = subprocess.run(
            ["bandit", "-f", "json", *files],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(tool="bandit", findings=[], status="partial", detail=f"timeout after {timeout}s")
    except FileNotFoundError:
        return ToolResult(tool="bandit", findings=[], status="skipped", detail="bandit not installed")

    try:
        raw = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return ToolResult(tool="bandit", findings=[], status="error", detail=proc.stderr[:500])

    findings = [
        Finding(
            file=_normalize_path(item.get("filename", "?")),
            line=item.get("line_number"),
            category=Category.SECURITY,
            severity=_BANDIT_SEVERITY_MAP.get(item.get("issue_severity", "MEDIUM"), Severity.MEDIUM),
            message=f"{item.get('test_id', '')} {item.get('issue_text', '')}".strip(),
            source="bandit",
        )
        for item in raw.get("results", [])
    ]
    return ToolResult(tool="bandit", findings=findings, status="ok")


_RUNNERS = {
    "pylint": _run_pylint,
    "flake8": _run_flake8,
    "bandit": _run_bandit,
}


def run_tools(
    enabled_tools: list[str],
    files: list[Path],
    cwd: Path,
    timeout_seconds: int = 15,
) -> list[ToolResult]:
    """Runs every enabled tool against ``files`` in parallel.

    A tool crashing, timing out, or being absent from PATH never raises —
    it's reflected as ToolResult.status and the other tools keep running
    (docs/system-design.md § 7, "Static analysis инструмент упал/завис").
    """
    if not files:
        return [ToolResult(tool=t, findings=[], status="skipped", detail="no files to analyze") for t in enabled_tools]

    # Pass paths relative to `cwd` rather than absolute ones: on Windows an
    # absolute path's drive-letter colon ("C:\...") breaks the simple
    # "path:line:col: ..." output parsers below, and relative paths also
    # keep Finding.file consistent with the diff's own (relative) paths.
    cwd = cwd.resolve()
    rel_files: list[str] = []
    for f in files:
        f = f.resolve()
        try:
            rel_files.append(str(f.relative_to(cwd)))
        except ValueError:
            rel_files.append(str(f))

    results: list[ToolResult] = []
    with ThreadPoolExecutor(max_workers=max(1, len(enabled_tools))) as executor:
        future_to_tool = {
            executor.submit(_RUNNERS[tool], rel_files, cwd, timeout_seconds): tool
            for tool in enabled_tools
            if tool in _RUNNERS
        }
        for future in as_completed(future_to_tool):
            tool = future_to_tool[future]
            try:
                results.append(future.result())
            except Exception as exc:  # noqa: BLE001 — isolate unexpected crashes per tool
                results.append(ToolResult(tool=tool, findings=[], status="error", detail=str(exc)))

    return results
