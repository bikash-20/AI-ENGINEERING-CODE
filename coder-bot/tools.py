"""Tool implementations and OpenAI/JSON-Schema tool definitions.

Each tool is a plain function that:
  - takes a `sandbox: Path` plus its own args,
  - returns a JSON-serialisable result string (short, for the model),
  - raises nothing — errors are caught and returned as error strings.

A separate `TOOL_DEFINITIONS` list exposes the same tools in OpenAI
function-calling schema format for the chat completions API.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List

# --------------------------------------------------------------------------- #
# Sandbox helpers
# --------------------------------------------------------------------------- #

# Commands we will never run, even in auto mode.
_DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\s+/\b",
    r"\brm\s+-rf\s+~",
    r"\bmkfs(\.|\b)",
    r"\bdd\s+if=.*of=/dev/",
    r":\(\)\s*\{.*\};:",          # fork bomb
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bhalt\b",
    r"\bformat\s+[a-zA-Z]:",      # windows format c:
    r"\bdel\s+/[sq]\b",           # windows del /s /q
]


def _resolve_in_sandbox(path: str, sandbox: Path) -> Path:
    """Resolve `path` against the sandbox and reject escapes."""
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = (sandbox / p).resolve()
    else:
        p = p.resolve()
    sandbox_resolved = sandbox.resolve()
    try:
        p.relative_to(sandbox_resolved)
    except ValueError:
        raise PermissionError(
            f"Path '{path}' resolves outside the project root "
            f"({sandbox_resolved}). Refusing to operate."
        )
    return p


def _preview_write(path: Path, content: str, max_lines: int = 20) -> str:
    """Return a short preview suitable for confirmation prompts."""
    lines = content.splitlines()
    head = "\n".join(lines[:max_lines])
    extra = f"\n... ({len(lines) - max_lines} more lines)" if len(lines) > max_lines else ""
    return head + extra


def _diff_preview(old: str, new: str, path: Path, max_lines: int = 40) -> str:
    diff = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"{path} (before)",
        tofile=f"{path} (after)",
        n=2,
    )
    text = "".join(diff)
    if len(text) > 2000:
        text = text[:2000] + f"\n... ({len(text) - 2000} more bytes of diff)"
    return text or "(no textual change)"


def _is_dangerous(command: str) -> bool:
    cmd = command.lower()
    return any(re.search(p, cmd) for p in _DANGEROUS_PATTERNS)


# --------------------------------------------------------------------------- #
# Tool implementations
# --------------------------------------------------------------------------- #

def read_file(path: str, sandbox: Path) -> str:
    try:
        p = _resolve_in_sandbox(path, sandbox)
        if not p.exists():
            return f"ERROR: file not found: {p}"
        if p.is_dir():
            return f"ERROR: {p} is a directory, not a file"
        text = p.read_text(encoding="utf-8", errors="replace")
        # Add line numbers so the model can refer to them in edits.
        numbered = "\n".join(
            f"{i + 1:>4}  {line}" for i, line in enumerate(text.splitlines())
        )
        return f"--- {p} ---\n{numbered}\n--- end ({len(text)} bytes) ---"
    except PermissionError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR reading {path}: {type(e).__name__}: {e}"


def write_file(path: str, content: str, sandbox: Path) -> str:
    try:
        p = _resolve_in_sandbox(path, sandbox)
        existed = p.exists()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        verb = "updated" if existed else "created"
        return f"OK: {verb} {p} ({len(content)} bytes)"
    except PermissionError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR writing {path}: {type(e).__name__}: {e}"


def edit_file(path: str, old_str: str, new_str: str, sandbox: Path) -> str:
    try:
        p = _resolve_in_sandbox(path, sandbox)
        if not p.exists():
            return f"ERROR: file not found: {p}"
        text = p.read_text(encoding="utf-8", errors="replace")
        count = text.count(old_str)
        if count == 0:
            return (
                f"ERROR: old_str not found in {p}. "
                "Re-read the file and try a smaller, unique snippet."
            )
        if count > 1:
            return (
                f"ERROR: old_str matches {count} places in {p}. "
                "Make it more specific (include more surrounding context) so it matches once."
            )
        new_text = text.replace(old_str, new_str, 1)
        p.write_text(new_text, encoding="utf-8")
        return f"OK: edited {p} ({len(new_text)} bytes)"
    except PermissionError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR editing {path}: {type(e).__name__}: {e}"


def list_dir(path: str, sandbox: Path) -> str:
    try:
        p = _resolve_in_sandbox(path, sandbox)
        if not p.exists():
            return f"ERROR: directory not found: {p}"
        if not p.is_dir():
            return f"ERROR: not a directory: {p}"
        entries = []
        for entry in sorted(p.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())):
            tag = "/" if entry.is_dir() else ""
            size = "" if entry.is_dir() else f"  {entry.stat().st_size:>8}B"
            entries.append(f"{entry.name}{tag}{size}")
        if not entries:
            return f"(empty directory: {p})"
        return f"--- {p} ---\n" + "\n".join(entries)
    except PermissionError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR listing {path}: {type(e).__name__}: {e}"


def create_folder(path: str, sandbox: Path) -> str:
    try:
        p = _resolve_in_sandbox(path, sandbox)
        p.mkdir(parents=True, exist_ok=True)
        return f"OK: ensured folder {p}"
    except PermissionError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR creating folder {path}: {type(e).__name__}: {e}"


def delete_file(path: str, sandbox: Path) -> str:
    try:
        p = _resolve_in_sandbox(path, sandbox)
        if not p.exists():
            return f"ERROR: file not found: {p}"
        if p.is_dir():
            return f"ERROR: {p} is a directory; use delete_folder"
        p.unlink()
        return f"OK: deleted file {p}"
    except PermissionError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR deleting {path}: {type(e).__name__}: {e}"


def delete_folder(path: str, sandbox: Path) -> str:
    try:
        p = _resolve_in_sandbox(path, sandbox)
        if not p.exists():
            return f"ERROR: folder not found: {p}"
        if not p.is_dir():
            return f"ERROR: {p} is a file; use delete_file"
        shutil.rmtree(p)
        return f"OK: deleted folder {p}"
    except PermissionError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR deleting folder {path}: {type(e).__name__}: {e}"


def run_command(command: str, cwd: str | None, sandbox: Path,
                timeout: int) -> str:
    try:
        if _is_dangerous(command):
            return (
                "ERROR: refusing to run command — matches a dangerous pattern "
                f"(rm -rf /, format, shutdown, etc.): {command!r}"
            )
        cwd_path = None
        if cwd:
            cwd_path = _resolve_in_sandbox(cwd, sandbox)
        else:
            cwd_path = sandbox
        # Use shlex so quoting works as expected on POSIX.
        argv = shlex.split(command) if os.name != "nt" else None
        result = subprocess.run(
            argv if argv is not None else command,
            shell=argv is None,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (result.stdout or "").rstrip()
        err = (result.stderr or "").rstrip()
        # Truncate huge outputs so the model doesn't blow up its context.
        if len(out) > 4000:
            out = out[:4000] + f"\n... (truncated, full stdout was {len(out)} chars)"
        if len(err) > 4000:
            err = err[:4000] + f"\n... (truncated, full stderr was {len(err)} chars)"
        return (
            f"exit_code={result.returncode}\n"
            f"stdout:\n{out or '(empty)'}\n"
            f"stderr:\n{err or '(empty)'}"
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s: {command!r}"
    except PermissionError as e:
        return f"ERROR: {e}"
    except Exception as e:
        return f"ERROR running command: {type(e).__name__}: {e}"


def search_files(pattern: str, path: str, sandbox: Path) -> str:
    """Grep-like recursive search.

    `pattern` is a Python regex. We cap matches to keep output bounded.
    """
    try:
        root = _resolve_in_sandbox(path, sandbox)
        if not root.exists():
            return f"ERROR: path not found: {root}"
        rx = re.compile(pattern)
        matches: List[str] = []
        max_hits = 200
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            try:
                for i, line in enumerate(
                    p.read_text(encoding="utf-8", errors="replace").splitlines(),
                    start=1,
                ):
                    if rx.search(line):
                        matches.append(f"{p}:{i}: {line.rstrip()}")
                        if len(matches) >= max_hits:
                            matches.append(f"... (truncated at {max_hits} hits)")
                            return "\n".join(matches)
            except (OSError, UnicodeError):
                continue
        return "\n".join(matches) if matches else "(no matches)"
    except PermissionError as e:
        return f"ERROR: {e}"
    except re.error as e:
        return f"ERROR: invalid regex {pattern!r}: {e}"
    except Exception as e:
        return f"ERROR searching: {type(e).__name__}: {e}"


# --------------------------------------------------------------------------- #
# OpenAI-style tool definitions
# --------------------------------------------------------------------------- #

TOOL_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a UTF-8 text file. Returns the contents with line numbers "
                "(use the numbers to build unique snippets for edit_file). "
                "Always read a file before editing it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the project root, or absolute."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or overwrite a file with the given content. Prefer "
                "edit_file for small targeted changes to existing files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "Find-and-replace a unique snippet in a file. The old_str must "
                "match exactly once in the file or the call fails. Re-read the "
                "file if the edit fails and retry with more surrounding context."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_str": {"type": "string"},
                    "new_str": {"type": "string"},
                },
                "required": ["path", "old_str", "new_str"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List a directory's entries (folders marked with trailing /).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_folder",
            "description": "Create a folder (and parents) if it doesn't exist.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a single file. Will fail loudly if the path is a directory.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_folder",
            "description": "Recursively delete a folder and all its contents.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command and capture its stdout, stderr, and exit code. "
                "The command runs with cwd inside the project root. A small "
                "blocklist prevents obviously destructive commands."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "cwd": {"type": "string", "description": "Optional working directory."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": (
                "Regex search across all files under a path. Returns up to 200 "
                "matching lines in `path:lineno: text` form."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python regex."},
                    "path": {"type": "string", "description": "Directory to search; default '.'."},
                },
                "required": ["pattern"],
            },
        },
    },
]


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #

def call_tool(name: str, arguments: Dict[str, Any], sandbox: Path,
              timeout: int, confirm: Callable[[str], bool]) -> Dict[str, Any]:
    """Run a tool by name, applying the confirmation hook for sensitive ops.

    Returns a dict suitable for appending as a `role: tool` message.
    """
    args = arguments or {}

    # Confirmation gate. Ask the user for any write/delete/shell op.
    sensitive = name in {
        "write_file", "edit_file", "delete_file", "delete_folder", "run_command"
    }
    if sensitive and not confirm(name, args):
        return {
            "role": "tool",
            "tool_call_id": "",  # filled by caller
            "name": name,
            "content": "USER DECLINED — the operation was cancelled.",
        }

    try:
        if name == "read_file":
            content = read_file(args.get("path", ""), sandbox)
        elif name == "write_file":
            content = write_file(args.get("path", ""), args.get("content", ""), sandbox)
        elif name == "edit_file":
            content = edit_file(
                args.get("path", ""),
                args.get("old_str", ""),
                args.get("new_str", ""),
                sandbox,
            )
        elif name == "list_dir":
            content = list_dir(args.get("path", "."), sandbox)
        elif name == "create_folder":
            content = create_folder(args.get("path", ""), sandbox)
        elif name == "delete_file":
            content = delete_file(args.get("path", ""), sandbox)
        elif name == "delete_folder":
            content = delete_folder(args.get("path", ""), sandbox)
        elif name == "run_command":
            content = run_command(
                args.get("command", ""),
                args.get("cwd"),
                sandbox,
                timeout,
            )
        elif name == "search_files":
            content = search_files(
                args.get("pattern", ""),
                args.get("path", "."),
                sandbox,
            )
        else:
            content = f"ERROR: unknown tool '{name}'"
    except Exception as e:  # last-resort safety net
        content = f"ERROR (uncaught): {type(e).__name__}: {e}"

    return {
        "role": "tool",
        "tool_call_id": "",  # filled by caller
        "name": name,
        "content": content,
    }