# coder bot 🤖

A local, agentic **coding assistant** in the spirit of Claude Code / Cursor agent
mode. It runs in your terminal and can read, write, edit, and search files; create
folders; run shell commands; and hold a multi-turn conversation with persistent
history. Two providers are supported:

- **`cloud` (default)** — talks to **OpenRouter** (OpenAI-compatible). Free and
  paid models both supported.
- **`local`** — talks to a local **Ollama** server at
  `http://localhost:11434/v1`. No API key needed.

Switch at runtime with `use cloud` / `use local`, or pick at startup with the
`--provider` flag.

---

## Features

- 🧠 **Tool-calling agent** — `read_file`, `write_file`, `edit_file`, `list_dir`,
  `create_folder`, `delete_file`, `delete_folder`, `run_command`, `search_files`.
- 🔒 **Sandboxed to a project root** — refuses paths that resolve outside it
  (no `../../etc/passwd`).
- 🛡️ **Two safety modes**
  - `ask` (default) — confirms every write/delete/shell with `y/n`.
  - `auto` — runs without asking (toggle at runtime with `auto mode`).
  - A small built-in blocklist forbids destructive commands (`rm -rf /`,
    `mkfs`, `dd`, `shutdown`, `format`, …) **even in auto mode**.
- 💬 **Persistent chat history** scoped to the project root
  (`.coder_history.json`).
- 🎤 **Optional voice I/O** per turn (`/voice` toggles; falls back to typing if
  the mic isn't available).
- 🔁 **Retries** with exponential backoff for transient OpenRouter errors.
- 🧾 **Live round summaries** like
  `─── round 2: 1 write_file, 2 edit_file, 1 run_command ───`.

---

## Setup

```bash
# 1. Clone / cd into this folder, then:
python3 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create your .env from the template
cp .env.example .env
# then edit .env and set OPENROUTER_API_KEY=sk-or-v1-...
```

You'll need an OpenRouter API key from <https://openrouter.ai/keys>.

### Pick a model

#### Cloud (OpenRouter)

Browse <https://openrouter.ai/models?max_price=0> for free tool-capable options.
Sensible free choices that work well with this bot:

| Model | Notes |
|---|---|
| `poolside/laguna-s-2.1:free` | Strongest free coding-agent model (default) |
| `inclusionai/ling-3.0-tiny:free` | Small MoE, designed for agents |
| `qwen/qwen-2.5-coder-32b-instruct:free` | Was the previous default; free tier may now require payment |

Set it in `.env` via `CLOUD_MODEL`, or pass `--model <id>`.

#### Local (Ollama)

You'll need a local Ollama install with at least one coding model pulled.
Pull a model first:

```bash
ollama pull qwen2.5-coder:7b     # best coding/tool-calling for this bot
# alternatives you can pull:
# ollama pull phi4-mini:latest
# ollama pull gemma3:4b
```

Switch with `PROVIDER=local` in `.env`, `python main.py --provider local`,
or the runtime command `use local`. The bot talks to
`http://localhost:11434/v1` and ignores the API key.

---

## Usage

```bash
python main.py                          # default settings from .env
python main.py --provider local         # start in local (Ollama) mode
python main.py --provider cloud         # start in cloud (OpenRouter) mode
python main.py --model <model-id>       # override the active model
python main.py --root /path/to/proj     # override sandbox
python main.py --auto                   # start in auto mode
python main.py --reset-history          # wipe the history file
```

### In-app commands

| Command | What it does |
|---|---|
| `help` | List commands |
| `mode` | Show current ask/auto mode |
| `auto mode` / `ask mode` | Toggle mode |
| `provider` | Show current provider + model + fallback |
| `use cloud` | Switch to OpenRouter (rebuilds client) |
| `use local` | Switch to local Ollama (rebuilds client) |
| `use model <id>` | Override the active model for the current provider |
| `root <path>` | Change sandbox root (resets history) |
| `history [N]` | Print last N turns (default 10) |
| `clear` | Reset current-session history |
| `exit` / `quit` | Leave the bot |
| `/voice` | Toggle voice input for the next turn |

### Safety

- Every **write / edit / delete / shell** call shows a preview and asks
  `proceed? [y/N]` in `ask` mode.
- In `auto` mode, the same previews are printed but no prompt is shown.
- **Always** blocked regardless of mode:
  `rm -rf /`, `rm -rf ~`, `mkfs`, `dd of=/dev/...`, fork bombs,
  `shutdown`, `reboot`, `halt`, `format c:`, `del /s /q`.

---

## Project layout

```
.
├── main.py          # REPL loop, slash-commands, history persistence
├── agent.py         # system prompt, OpenRouter call, tool-call loop, retries
├── tools.py         # tool implementations + OpenAI function schema + confirm
├── config.py        # .env / env-var settings, sandbox path resolution
├── voice_utils.py   # optional STT/TTS wrappers (degrade gracefully)
├── requirements.txt
├── .env.example
└── README.md
```

---

## How it works

1. `main.py` reads a `Settings` snapshot from `config.py`.
2. Each user turn is appended to `messages` (list of `role/content` dicts).
3. `agent.run_turn` calls `openai.ChatCompletion` via the OpenAI SDK pointed at
   `https://openrouter.ai/api/v1` with the tools from `tools.py`.
4. If the model returns `tool_calls`, we execute them, append the results as
   `role: "tool"` messages, and call the API again.
5. After up to **15** tool rounds (configurable via `CODER_MAX_TOOL_ROUNDS`)
   the final text is spoken (if TTS is available) and the history is saved.

---

## Troubleshooting

- **`OPENROUTER_API_KEY is not set`** — copy `.env.example` to `.env` and add
  your key.
- **Model returns `404` / `model not found`** — the slug was retired or
  renamed. The bot now surfaces the API's suggested replacement when one
  is available (e.g. `qwen/qwen-2.5-coder-32b-instruct:free` →
  `qwen/qwen-2.5-coder-32b-instruct`). Update `CLOUD_MODEL` in `.env`,
  or pass `--model <id>` for this session.
- **"Could not reach local Ollama"** — run `ollama serve` (or restart the
  Ollama app) and confirm with `ollama list`. The bot fails fast on
  connection errors instead of looping.
- **Local model describes what it would do instead of calling tools** —
  the system prompt is aggressive about tool use, but small local models
  still slip sometimes. Try rephrasing ("Use write_file to create X…") or
  switch to `use cloud` for more reliable tool calling.
- **Rate limits** — the bot retries with backoff automatically. If you keep
  hitting limits, switch to a different free model or wait a minute.
- **`edit_file` keeps failing** — the `old_str` must match exactly once. The
  error message says how many matches it had; re-read the file and use a more
  specific snippet.

---

## License

MIT. Use it, fork it, teach it new tricks. Have fun. 🚀