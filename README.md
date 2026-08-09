# AI-ENGINEERING-CODE

A personal lab for learning how AI APIs actually work under the hood. Every script here is a small, focused experiment — written to understand *one* thing at a time, not to ship a product.

The version suffix (`1.0`, `5.2`, …) is intentional: I bump it when the logic changes so I can compare what got added and watch the file grow side-by-side. The full progression (system prompts → streaming → persistence → commands → online/offline cascade) lives in `CHANGELOG.md`.

## What's in here

| File | What it teaches |
|---|---|
| `chatbot5.2.py` | The current chatbot. Multi-provider (OpenRouter, Gemini, Ollama), online/offline cascade, streaming, persistent history, voice/text mode, `model X` switching, `last topic?` command. |
| `voice_utils.py` | Three concerns — `speak(text)` (offline TTS via `pyttsx3`), `listen()` (hybrid STT: Google online, local faster-whisper server offline), and `has_internet()` + `set_online(bool)` / `mark_offline()` for the connectivity cache. |
| `CHANGELOG.md` | Every version bump, why it happened, what changed, what to try, and what's coming next. Read top-to-bottom to follow the learning curve. |
| `.env` / `.env.example` | API keys. `.env` is gitignored; `.env.example` is the safe template. |
| `chat_history.json` | Persisted `messages` list. Created on first run, updated every turn. Safe to delete for a fresh start. |

> **Note on older files.** Earlier versions (`chatbot.py`, `chatbot1.0.py`, …) are documented in `CHANGELOG.md` but are not in this tree — they were each replaced by the next version. `index.js` (the Node port) was a parallel experiment and isn't checked in here.

## What `chatbot5.2.py` actually does

It's one file (~440 lines) with five layers stacked on top of each other. From bottom to top:

1. **Clients** — three `OpenAI(...)` SDK instances, all speaking the chat-completions API:
   - `OPENROUTER_CLIENT` → `https://openrouter.ai/api/v1`
   - `GEMINI_CLIENT` → `https://generativelanguage.googleapis.com/v1beta/openai/`
   - `OLLAMA_CLIENT` → `http://localhost:11434/v1` (local; ignores the key)
2. **Model registry** — `MODELS` dict. Each entry is `(label, client, model_id, requires_internet)`. Keys are what you type: `deepseek`, `gemini`, `llama-free`, `qwen-free`, `laguna-free`, `gemini-free`, `qwen`, `gemma`, `phi`.
3. **Active selection** — `active_key` plus `set_active(key)` for runtime switching. `pick_initial_model()` boots `deepseek` if online, the first local Ollama model otherwise.
4. **Cascade** — `_call_with_failover(messages, stream)` tries the active model first, then walks `FALLBACK_CHAIN` (OpenRouter paid → OpenRouter free → Gemini direct → local Ollama). Connectivity-shaped errors flip the cache via `mark_offline()`; HTTP-level errors (429, 401, 5xx) do not, because the network is fine and STT should keep using Google.
5. **Main loop** — read input → dispatch commands (`voice`, `text`, `voice off`, `model X`, `model`, `last topic?`, greetings) → otherwise append to `messages`, stream the reply, speak it, save.

Two small but important details:

- The streaming `for chunk in stream:` block is wrapped in `try/except`. A mid-stream network drop prints `(stream interrupted: …)` and pops the just-appended user turn if no tokens arrived, so `chat_history.json` stays consistent.
- `mark_online()` runs after every successful reply, so the connectivity cache self-corrects when the network comes back. Pure probes would lie when the cloud is reachable but the cloud provider is down; pure observation would lie before the first call. Doing both handles both cases.

## How a chat-completions call works

Every provider in `MODELS` (OpenRouter, Gemini, Ollama) accepts the same shape:

1. **You** `POST` JSON to a chat-completions URL with two important fields:
   - `model` — which model to run (e.g. `deepseek/deepseek-chat`, `gemini-2.0-flash`, `qwen2.5-coder:7b`).
   - `messages` — a list of `{ role, content }` turns. Roles are `system`, `user`, or `assistant`.
2. The **server** runs the model and returns JSON with `choices[0].message.content` — the assistant's reply.
3. You **append** that reply to `messages` and send the next request. That list *is* the conversation's memory.

The OpenAI SDK hides steps 1–2 behind `client.chat.completions.create(...)`. Because OpenRouter, Gemini's OpenAI-compatible endpoint, and Ollama all speak that same shape, the rest of the script doesn't care which one it's talking to.

## Commands inside the loop

| Command | Effect |
|---|---|
| `voice` | Switch to voice input for all subsequent turns (press Enter at the prompt to speak, or type a command). |
| `text` / `voice off` | Switch back to typed input. |
| `model` | Print the active model and online/offline status. |
| `model deepseek` / `model qwen` / … | Switch the active backend. Works in both online and offline modes. |
| `last topic?` | Ask the model to compress your last real turn into ≤ 8 words. Uses a separate prompt so it doesn't pollute the main history. |
| `hey` / `hi` / `hello` / `yo` / `sup` / `hola` / `howdy` | Handled locally. Bot replies with a fixed open question; nothing is appended to `messages`. |
| `exit` / `quit` | End the session. |

## Setup

### 1. API keys

Copy `.env.example` to `.env` and fill in the keys you have. At minimum set `OPENROUTER_API_KEY`. If you want the `model gemini` path to work, also set `GEMINI_API_KEY`. Ollama doesn't need a key.

```bash
cp .env.example .env
# then edit .env
```

Keys live at <https://openrouter.ai/keys> and <https://aistudio.google.com/apikey>.

### 2. Python dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install openai python-dotenv pyttsx3 SpeechRecognition
```

`pyttsx3` is required even in text-only mode because `voice_utils.speak()` is called after every successful reply when voice mode is on. If you never plan to use `voice`, you can skip both `pyttsx3` and `SpeechRecognition` and avoid the macOS mic-permission prompts.

### 3. (Optional) Local Ollama

For the offline cascade you need Ollama running with at least one model pulled:

```bash
# install: https://ollama.com/download
ollama serve                      # leave running
ollama pull qwen2.5-coder:7b
ollama pull gemma3:4b
ollama pull phi4-mini
```

Any of those models pulled is enough — the cascade picks whatever's available.

### 4. (Optional) Local faster-whisper for offline STT

`voice_utils.listen()` falls back to a local faster-whisper server on `http://localhost:5005/transcribe` when Google is unreachable. The endpoint expects a multipart `audio` field and returns `{"text": "..."}`. Any server that matches that contract works — `faster-whisper server` (the upstream Python package) is the simplest. If the server isn't running, voice mode offline will print `(Local whisper STT failed: …)` and return — no crash.

## Running

```bash
# Activate the venv first if you made one
source .venv/bin/activate

python chatbot5.2.py
```

On startup you'll see something like:

```text
Chatbot ready (online mode). Active model: deepseek/deepseek-chat (OpenRouter).
Commands: 'voice', 'voice off' / 'text', 'last topic?', 'model deepseek | gemini | llama-free | qwen-free | laguna-free | gemini-free | qwen | gemma | phi', 'exit'.
(Loaded 0 message(s) from chat_history.json)
```

Type to chat, `voice` to talk, `model phi` to go offline, `exit` to quit. Delete `chat_history.json` for a clean slate.

### macOS voice notes

- `pyttsx3` on macOS uses the built-in `say` command. No extra install.
- The first time you run a listening script, macOS will prompt for microphone access. System Settings → Privacy & Security → **Microphone** → enable your terminal app (Terminal, iTerm, VS Code, …).
- The mic-calibration half-second (`adjust_for_ambient_noise`) in `voice_utils.listen()` exists specifically because the bot's own TTS plays through the same speakers the mic is sitting next to. Without it, the recognizer auto-tunes its threshold against the bot's tail-end audio and bails on your first word. See the patch entry in `CHANGELOG.md`.

### Linux / Windows voice notes

- **Linux:** `sudo apt install espeak portaudio19-dev`, then `pip install pyaudio` (or `pipwin install pyaudio` if the wheel fails).
- **Windows:** SAPI5 is built in. `pip install PyAudio` works if you have the Visual C++ build tools; otherwise `pipwin install pyaudio` is the easier path.

## Versioning convention

I'm using this repo to track my own learning curve. The progression lives in `CHANGELOG.md`:

- **2.0** — system prompt
- **3.0** — streaming + TTS-after (the "audio cuts off" bug fix is documented inline)
- **4.0** — persistent `chat_history.json`
- **5.0** — `last topic?` command + local greeting handling
- **5.1** — sticky `voice` / `text` mode across turns
- **5.2** — tighter mode-switch wording, then a rewrite adding online/offline cascade, the model registry, hybrid STT, and mid-stream resilience

Each major bump means the *logic* changed. Read the diff between versions to see what I was figuring out at that step.
