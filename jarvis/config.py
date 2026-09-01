import os
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/chat")
REQUEST_TIMEOUT = 25  # seconds

# Cascade: tried top to bottom, first success wins. Add/reorder here only —
# no other file needs to change when the lineup changes.
TIERS: list[tuple[str, str]] = [
    ("openrouter", "nvidia/nemotron-3.5-lightning:free"),
    ("openrouter", "openai/gpt-oss-20b:free"),
    ("openrouter", "liquid/lfm-2.5-2.6b:free"),
    ("openrouter", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    ("openrouter", "google/gemma-4-31b-it:free"),
    ("ollama", "phi4-mini"),
    ("ollama", "gemma3:4b"),
    ("ollama", "qwen2.5-coder:7b"),
]

SYSTEM_PROMPT = "You are Jarvis, a concise and helpful assistant."

# Short display alias per tier, keyed by "kind:model" (matches cascade's label).
# Anything not listed here just falls back to its model slug — see main.display_name().
DISPLAY_NAMES: dict[str, str] = {
    "openrouter:nvidia/nemotron-3.5-lightning:free": "MEMO",
    "openrouter:openai/gpt-oss-20b:free": "OSS",
    "openrouter:liquid/lfm-2.5-2.6b:free": "LFM",
    "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free": "ULTRA",
    "openrouter:google/gemma-4-31b-it:free": "GEMMA4",
    "ollama:phi4-mini": "PHI",
    "ollama:gemma3:4b": "GEMMA",
    "ollama:qwen2.5-coder:7b": "QWEN",
}