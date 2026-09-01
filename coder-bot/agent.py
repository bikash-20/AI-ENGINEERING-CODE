"""Agent loop: talks to OpenAI-compatible APIs (OpenRouter or local Ollama),
executes tool calls, and returns the final text + a brief summary.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from openai import OpenAI

from config import Settings
from tools import TOOL_DEFINITIONS, call_tool, _preview_write, _diff_preview

# --------------------------------------------------------------------------- #
# System prompt for the coding model
# --------------------------------------------------------------------------- #
#
# We use a single, strongly-tool-anchored prompt because:
#   * Cloud models (claude, gpt-4, poolside, qwen-2.5-72b) reliably call tools
#     when given any reasonable prompt.
#   * Local models (qwen2.5-coder:7b, phi4-mini, gemma3:4b) often just *describe*
#     what they would do in plain text unless explicitly told to *use the tool*.
# The prompt below is aggressive about tool use so the same text works for both.
#
# If you want a softer cloud-only prompt, branch on `settings.provider` and
# pick between two strings.

CODER_SYSTEM_PROMPT = """\
You are Coder Bot, a careful senior software engineer working inside a sandboxed
project directory. You have REAL tools to read, write, edit, create, and search
files, and to run shell commands. Every write/delete/shell call goes through a
user confirmation gate unless the session is in "auto" mode.

CRITICAL — how to use tools:
- You MUST call the provided tools to make real changes. Do NOT just describe
  what you would do in prose. If the user asks you to create a file, the
  assistant message MUST contain a `write_file` tool call, not a code block.
- When you need information that lives in a file, call `read_file` first.
- Prefer small, targeted `edit_file` calls over rewriting whole files.
- Chain multiple tool calls in a single turn when the next call depends on the
  result of the previous one.
- When a tool result is an error, read the error, fix the cause, and try again.
- Never end a turn with a tool call unless more work is still pending.

Working principles:
1. Read a file with `read_file` before editing it. Never invent file contents.
2. Prefer tiny, targeted edits over large rewrites.
3. Before calling tools, briefly state in one sentence what you are doing.
4. For shell commands, prefer running them inside the project root and keep
   outputs short. Do not run destructive commands.
5. Ask clarifying questions only when truly blocked. Otherwise make a reasonable
   choice and proceed.
6. When finished, give a concise plain-text summary of what you actually changed.
"""


# Tokens that hint the model is describing instead of calling a tool — used to
# print a gentle nudge when local models fall back to plain text.
_DESCRIBE_INSTEAD_OF_CALL = re.compile(
    r"\b(i('ll| will) (create|write|make|add|run|edit|open|read)|"
    r"here('s| is) the (file|code|script)|"
    r"i('ve| have) (created|written|made|added)|"
    r"the file (now )?contains|"
    r"i (created|wrote|made|added))",
    re.IGNORECASE,
)


# --------------------------------------------------------------------------- #
# Agent state
# --------------------------------------------------------------------------- #

@dataclass
class TurnResult:
    final_text: str = ""
    tool_summary: str = ""
    tool_call_count: int = 0
    round_count: int = 0
    tool_names: List[str] = field(default_factory=list)
    missed_tool_call: bool = False  # True if we suspect the model described
                                    # instead of calling a tool.


# --------------------------------------------------------------------------- #
# Connection + retry
# --------------------------------------------------------------------------- #

def _chat_kwargs(model: str, messages: List[Dict[str, Any]],
                 tools: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Build the kwargs for `client.chat.completions.create`.

    Non-streaming is used because mixing streamed text with tool_calls is
    awkward across providers; we just print the assembled response.
    """
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    return kwargs


def _looks_like_404(e: Exception) -> bool:
    msg = str(e).lower()
    return any(tok in msg for tok in (
        "404", "not found", "model_not_found", "this model is unavailable",
    ))


def _looks_like_local_unreachable(e: Exception) -> bool:
    """Detect connection refused / DNS / localhost-shaped errors."""
    msg = str(e).lower()
    return any(tok in msg for tok in (
        "connection refused", "connectionerror", "connect call failed",
        "failed to connect", "name or service not known", "errno 61",
        "[errno 111]",  # Linux connection refused
    ))


def _extract_suggested_slug(e: Exception) -> Optional[str]:
    """OpenRouter-style errors sometimes suggest a paid slug; surface it."""
    msg = str(e)
    # Look for "use this slug instead: <slug>"
    m = re.search(r"use this slug instead:\s*([^\s,'\"]+)", msg, re.IGNORECASE)
    if m:
        return m.group(1)
    # Fall back to a generic "use instead: <slug>"
    m = re.search(r"use instead:\s*([^\s,'\"]+)", msg, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _chat_with_retry(client: OpenAI, settings: Settings,
                     messages: List[Dict[str, Any]],
                     tools: Optional[List[Dict[str, Any]]]) -> Any:
    """Retry wrapper that is provider-aware:

    * Connection errors to the local Ollama server fail fast after 1 retry —
      they won't fix themselves by waiting.
    * 404 / "model not found" / "model unavailable" errors are permanent:
      surface the API's suggested slug if available, then re-raise.
    * Everything else (rate limit, 5xx, transient network) gets up to 3
      attempts with exponential backoff.
    """
    is_local = settings.provider == "local"
    max_attempts = 2 if is_local else 3

    last_err: Optional[Exception] = None
    for i in range(max_attempts):
        try:
            return client.chat.completions.create(
                **_chat_kwargs(settings.model, messages, tools)
            )
        except Exception as e:
            last_err = e

            # Permanent errors — don't retry.
            if _looks_like_404(e):
                suggested = _extract_suggested_slug(e)
                if suggested:
                    raise RuntimeError(
                        f"Model {settings.model!r} was rejected by the API, "
                        f"which suggests instead: {suggested!r}. "
                        f"Set CLOUD_MODEL={suggested} in .env, or run with "
                        f"--model {suggested}."
                    ) from e
                raise RuntimeError(
                    f"Model {settings.model!r} was rejected by the API "
                    f"(404 / not found / unavailable). "
                    f"Check spelling or browse available models."
                ) from e

            # Local connection errors — fail fast after 1 retry.
            if is_local and _looks_like_local_unreachable(e):
                if i == 0:
                    print(
                        f"[retry] Ollama unreachable ({type(e).__name__}). "
                        f"Retrying once, then giving up."
                    )
                    time.sleep(1)
                    continue
                raise RuntimeError(
                    f"Could not reach local Ollama server at "
                    f"{settings.base_url}. "
                    f"Start it with `ollama serve` (and pull a model with "
                    f"`ollama pull {settings.model}`). "
                    f"Underlying error: {type(e).__name__}: {e}"
                ) from e

            # Hard errors that won't be fixed by retrying.
            msg = str(e).lower()
            if any(tok in msg for tok in (
                "401", "invalid api key", "unauthorized",
                "context_length_exceeded",
            )):
                raise

            # Transient — back off and retry.
            if i == max_attempts - 1:
                break
            wait = 2 ** i
            print(
                f"[retry {i + 1}/{max_attempts}] {type(e).__name__}: {e}. "
                f"Sleeping {wait}s..."
            )
            time.sleep(wait)

    assert last_err is not None
    raise last_err


# --------------------------------------------------------------------------- #
# Confirmation + logging
# --------------------------------------------------------------------------- #

def make_confirmer(mode: str,
                   get_mode: Callable[[], str],
                   speak: Optional[Callable[[str], None]] = None
                   ) -> Callable[[str, Dict[str, Any]], bool]:
    """Return a `confirm(name, args) -> bool` callable honouring the current mode."""

    def _ask(name: str, args: Dict[str, Any]) -> bool:
        current = get_mode()
        print()
        print(f"─── tool call: {name} ───")
        if name in {"write_file", "edit_file"}:
            path = args.get("path", "?")
            if name == "write_file":
                print(f"target : {path}")
                print("preview:")
                for line in _preview_write(Path(path), args.get("content", "")).splitlines():
                    print(f"  | {line}")
            else:
                old, new = args.get("old_str", ""), args.get("new_str", "")
                # For confirmation we just show the new snippet + a small marker.
                print(f"target : {path}")
                print("change :")
                for line in new.splitlines():
                    print(f"  + {line}")
        elif name in {"delete_file", "delete_folder"}:
            print(f"target : {args.get('path', '?')}")
            print(f"action : {name} (irreversible)")
        elif name == "run_command":
            print(f"cmd    : {args.get('command', '?')}")
            if args.get("cwd"):
                print(f"cwd    : {args['cwd']}")
        else:
            print(json.dumps(args, indent=2)[:600])

        if current == "auto":
            print("mode   : AUTO — proceeding without prompt")
            return True

        try:
            ans = input("proceed? [y/N]: ").strip().lower()
        except EOFError:
            ans = "n"
        ok = ans in {"y", "yes"}
        if not ok and speak:
            speak("Cancelled.")
        return ok

    return _ask


# --------------------------------------------------------------------------- #
# "Did the model actually call a tool?" detection
# --------------------------------------------------------------------------- #

def _looks_like_describing(text: str) -> bool:
    """Heuristic: did the model just describe what it would do?

    Local models sometimes answer in text without any tool_calls. We print a
    gentle note so the user knows to try again / switch provider.
    """
    if not text:
        return False
    return bool(_DESCRIBE_INSTEAD_OF_CALL.search(text))


# --------------------------------------------------------------------------- #
# Main agent turn
# --------------------------------------------------------------------------- #

def run_turn(
    *,
    client: OpenAI,
    settings: Settings,
    messages: List[Dict[str, Any]],
    get_mode: Callable[[], str],
    speak: Optional[Callable[[str], None]] = None,
    on_round_summary: Optional[Callable[[int, Dict[str, int]], None]] = None,
) -> TurnResult:
    """Drive one user turn: keep calling the model until it stops emitting
    tool_calls or we hit the round cap.

    `messages` is mutated in place.
    """
    confirm = make_confirmer(settings.mode, get_mode, speak)
    result = TurnResult()

    for round_idx in range(settings.max_tool_rounds):
        result.round_count = round_idx + 1

        try:
            response = _chat_with_retry(
                client, settings, messages, TOOL_DEFINITIONS,
            )
        except Exception as e:
            msg = f"[error] {e}"
            print(msg)
            if speak:
                speak("API call failed.")
            result.final_text = msg
            return result

        choice = response.choices[0]
        assistant_msg = choice.message

        # 1) Stream the text part (if any) so the user sees live output.
        text_chunk = assistant_msg.content or ""
        if text_chunk:
            print(text_chunk, end="", flush=True)
            if not text_chunk.endswith("\n"):
                print()

        # 2) Convert the assistant message into the dict shape we keep.
        assistant_dict: Dict[str, Any] = {
            "role": "assistant",
            "content": assistant_msg.content or "",
        }
        tool_calls = assistant_msg.tool_calls or []
        if tool_calls:
            assistant_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in tool_calls
            ]
        messages.append(assistant_dict)

        # 3) If no tool calls, we're done with this turn.
        if not tool_calls:
            # Diagnostic: if the model is on a local provider and just described
            # what it would do, print a gentle nudge so the user can react.
            if settings.provider == "local" and _looks_like_describing(text_chunk):
                result.missed_tool_call = True
                print(
                    "\n[note] model replied without using tools — local models "
                    "sometimes describe what they would do instead of calling "
                    "them. Try a more explicit instruction (e.g. start with "
                    "\"Use write_file to…\"), or switch to `use cloud` for "
                    "more reliable tool use."
                )
            result.final_text = assistant_msg.content or ""
            return result

        # 4) Execute each tool call, append tool results.
        summary_counts: Dict[str, int] = {}
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            name = tc.function.name
            result.tool_call_count += 1
            result.tool_names.append(name)
            summary_counts[name] = summary_counts.get(name, 0) + 1

            tool_msg = call_tool(
                name, args, settings.project_root,
                settings.cmd_timeout, confirm,
            )
            tool_msg["tool_call_id"] = tc.id
            messages.append(tool_msg)

            # Show a brief, readable trace so the user sees what happened.
            preview = (tool_msg["content"] or "").splitlines()
            print(f"  ↳ {name}: {preview[0] if preview else '(no output)'}")

        result.tool_summary = _summarize(summary_counts)
        if on_round_summary:
            on_round_summary(round_idx + 1, summary_counts)

    # Round cap reached.
    warn = (
        f"\n[warn] hit max_tool_rounds={settings.max_tool_rounds}; "
        "stopping so the loop doesn't run forever."
    )
    print(warn)
    result.final_text = (result.final_text + warn).strip()
    return result


def _summarize(counts: Dict[str, int]) -> str:
    """Build a one-line summary of what happened in a round."""
    parts = []
    for name, n in counts.items():
        plural = "" if n == 1 else "s"
        parts.append(f"{n} {name}{plural}")
    return ", ".join(parts) or "no tools"