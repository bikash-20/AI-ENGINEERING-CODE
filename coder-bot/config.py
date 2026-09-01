"""Centralised configuration for the coder bot.

Two providers are supported, selectable via the `PROVIDER` env var or the
`--provider` CLI flag:

  * "cloud"  — OpenRouter (https://openrouter.ai/api/v1).
                Requires OPENROUTER_API_KEY in .env.
                Model is taken from CLOUD_MODEL (or --model when in cloud mode).
  * "local"  — A local Ollama server running its OpenAI-compatible endpoint
                at http://localhost:11434/v1. No real API key is needed;
                we pass a dummy value because the OpenAI SDK refuses empty
                strings. Model is taken from LOCAL_MODEL.

Everything else (sandbox root, ask/auto mode, history, voice) is provider-
agnostic. Settings is a frozen dataclass, so changing a value means building
a new instance with `dataclasses.replace(...)`.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from the project root (the folder containing this file's parent).
_PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(_PROJECT_ROOT / ".env")


# --------------------------------------------------------------------------- #
# Provider registry
# --------------------------------------------------------------------------- #

VALID_PROVIDERS = ("cloud", "local")

#: Default base URLs for each provider.
BASE_URLS: dict[str, str] = {
    "cloud": "https://openrouter.ai/api/v1",
    "local": "http://localhost:11434/v1",
}


def _env(key: str, default: str | None = None) -> str | None:
    val = os.getenv(key)
    if val is None or val == "":
        return default
    return val


# --------------------------------------------------------------------------- #
# Settings dataclass
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Settings:
    # Provider identity
    provider: str                       # "cloud" | "local"

    # Effective connection (resolved from provider)
    api_key: str                        # may be a dummy "ollama" for local
    base_url: str

    # Model selection
    model: str                          # the active model id
    cloud_model: str                    # last-known CLOUD_MODEL setting
    local_model: str                    # last-known LOCAL_MODEL setting

    # Sandbox + behaviour (provider-agnostic)
    project_root: Path
    mode: str                           # "ask" | "auto"
    max_tool_rounds: int
    cmd_timeout: int
    history_file: Path
    # STT (microphone INPUT) — opt-in per-turn via `voice`.
    voice_enabled: bool
    # TTS (speaker OUTPUT) — opt-in via CODER_TTS=1 or `say on`. When off,
    # the bot never reads responses aloud automatically.
    output_voice_enabled: bool

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #
    @classmethod
    def load(cls) -> "Settings":
        # --- provider selection -----------------------------------------
        provider = (_env("PROVIDER", "cloud") or "cloud").lower().strip()
        if provider not in VALID_PROVIDERS:
            raise RuntimeError(
                f"PROVIDER={provider!r} is not valid. Use one of: {VALID_PROVIDERS}."
            )

        # --- model selection per provider -------------------------------
        # Defaults are picked so the bot is useful out-of-the-box on either
        # provider. Override via CLOUD_MODEL / LOCAL_MODEL or the CLI.
        cloud_model = _env(
            "CLOUD_MODEL",
            # Poolside Laguna S 2.1 — currently a free coding-agent model on
            # OpenRouter. Browse https://openrouter.ai/models?max_price=0 for
            # the current free list.
            "poolside/laguna-s-2.1:free",
        )
        local_model = _env(
            "LOCAL_MODEL",
            # qwen2.5-coder:7b is the strongest small coding model with
            # reliable tool-calling for this bot. Other options you have:
            # phi4-mini:latest, gemma3:4b.
            "qwen2.5-coder:7b",
        )

        # The active model is whichever provider we're on.
        model = cloud_model if provider == "cloud" else local_model

        # --- API key handling -------------------------------------------
        if provider == "cloud":
            api_key = _env("OPENROUTER_API_KEY", "") or ""
            if not api_key:
                raise RuntimeError(
                    "PROVIDER=cloud but OPENROUTER_API_KEY is not set. "
                    "Either add it to .env or switch to PROVIDER=local."
                )
        else:
            # Ollama ignores the key, but the OpenAI SDK requires non-empty.
            api_key = _env("OLLAMA_API_KEY", "ollama") or "ollama"

        # --- sandbox + behaviour (unchanged) ----------------------------
        root = _env("CODER_ROOT", ".")
        project_root = Path(root).resolve()

        mode = (_env("CODER_MODE", "ask") or "ask").lower()
        if mode not in {"ask", "auto"}:
            mode = "ask"

        max_rounds = int(_env("CODER_MAX_TOOL_ROUNDS", "15") or 15)
        timeout = int(_env("CODER_CMD_TIMEOUT", "30") or 30)

        hist = _env("CODER_HISTORY_FILE")
        history_file = (
            Path(hist).resolve() if hist else (project_root / ".coder_history.json")
        )

        # Voice off by default; user can toggle per turn with /voice.
        voice_enabled = (_env("CODER_VOICE", "0") or "0") == "1"

        # TTS (output speech) is OFF by default too. The bot only speaks
        # automatically when this is on, or when the user runs `say on`.
        output_voice_enabled = (_env("CODER_TTS", "0") or "0") == "1"

        return cls(
            provider=provider,
            api_key=api_key,
            base_url=BASE_URLS[provider],
            model=model,
            cloud_model=cloud_model,
            local_model=local_model,
            project_root=project_root,
            mode=mode,
            max_tool_rounds=max_rounds,
            cmd_timeout=timeout,
            history_file=history_file,
            voice_enabled=voice_enabled,
            output_voice_enabled=output_voice_enabled,
        )


def settings() -> Settings:
    """Return a fresh Settings snapshot (cheap to construct)."""
    return Settings.load()


# --------------------------------------------------------------------------- #
# Convenience: build a (key, base_url, model) tuple for the active provider.
# --------------------------------------------------------------------------- #

def resolve_connection(s: Settings) -> tuple[str, str, str]:
    """Return (api_key, base_url, model) for the active provider.

    Centralised so callers don't have to re-do the provider → base_url switch.
    """
    return s.api_key, s.base_url, s.model