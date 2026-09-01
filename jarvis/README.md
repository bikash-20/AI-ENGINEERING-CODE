# jarvis

A small CLI assistant with a **provider cascade** — it tries a list of LLMs in
order and the first one that connects wins. Streaming tokens are printed live.

## Run it

```bash
cd jarvis
python3 -m pip install -r requirements.txt
cp .env.example .env   # then put your OPENROUTER_API_KEY in .env
python3 main.py
```

Requires Python 3.10+ (uses `list[dict]` and `tuple[str, str] | None` syntax).

## What it teaches

| File | Concern |
|---|---|
| `config.py` | Declarative tier list, display names, system prompt. No logic. |
| `providers.py` | I/O adapters — one opener + one line-parser per backend. |
| `cascade.py` | Fall-through policy. Skips remaining OpenRouter tiers on a 429, since a free-tier quota exhaustion is account-wide. |
| `main.py` | REPL. Slash commands: `/model`, `/auto`, `/status`, `/help`, `/quit`. |

## Cascade order

Tried top to bottom, first success wins:

1. `openrouter:nvidia/nemotron-3.5-lightning:free` — MEMO
2. `openrouter:openai/gpt-oss-20b:free` — OSS
3. `openrouter:liquid/lfm-2.5-2.6b:free` — LFM
4. `openrouter:nvidia/nemotron-3-ultra-550b-a55b:free` — ULTRA
5. `openrouter:google/gemma-4-31b-it:free` — GEMMA4
6. `ollama:phi4-mini` — PHI
7. `ollama:gemma3:4b` — GEMMA
8. `ollama:qwen2.5-coder:7b` — QWEN

Edit `config.TIERS` to add or reorder — no other file needs to change.

## Commands

| Command | Effect |
|---|---|
| `/model <kind:model>` | Pin a single tier, skipping the cascade |
| `/auto` | Restore cascade mode |
| `/status` | Show current mode and cascade order |
| `/help` | List commands |
| `/quit` | Exit |
