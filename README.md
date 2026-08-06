# AI-ENGINEERING-CODE

A personal lab for learning how AI APIs actually work under the hood. Every script here is a small, focused experiment — written to understand *one* thing at a time, not to ship a product.

## What's in here

| File | What it teaches |
|---|---|
| `chatbot.py` | The smallest possible OpenAI-compatible chat loop. One model, one history list, `input()` → reply. |
| `chatbot1.0.py` | Same loop, but with **voice**: speaks the reply with TTS and accepts a `voice` mode that uses your microphone (STT). |
| `voice_utils.py` | Two helpers — `speak(text)` (TTS via `pyttsx3`) and `listen()` (STT via `speech_recognition` + Google Web Speech). |
| `index.js` | The Node.js version of the same chat loop. Same idea, no SDK — builds the HTTP request with `fetch` directly. |

The version suffix (`1.0`) is intentional: I bump it when the logic changes so I can compare what got added and watch the file grow side-by-side.

## How a simple API call works

Every chat API you'll touch (OpenAI, OpenRouter, Anthropic, etc.) follows the same shape:

1. **You** send an HTTP `POST` to a URL like `https://openrouter.ai/api/v1/chat/completions`.
2. The **body** is JSON with two important fields:
   - `model` — which model to run (e.g. `deepseek/deepseek-chat`, `meta-llama/llama-3.1-8b-instruct`).
   - `messages` — a list of `{ role, content }` turns. Roles are `system`, `user`, or `assistant`.
3. The **server** runs the model and returns JSON with `choices[0].message.content` — the assistant's reply.
4. You **append** that reply to `messages` and send the next request. That list *is* the conversation's memory.

Most languages have an SDK that hides steps 1–3 behind a `client.chat.completions.create(...)` call. `index.js` skips the SDK on purpose so you can see the raw HTTP shape.

## How the chatbot replies

```text
You: hi
Bot: Hello! How can I help you today?

You: what did I just say?
Bot: You said "hi".
```

Two things make this "remember" you:

1. The `messages` list grows on every turn — both your input and the bot's reply are appended.
2. The whole list is sent back on the next request. The model only knows what you give it.

Try editing one of the scripts and removing the `messages.append(...)` lines — the bot immediately forgets the previous turn. That's the cleanest way to feel what context is.

## Python vs Node.js (same loop, both files)

The Node version is ~60 lines; the Python version is ~30. Why?

- **OpenAI SDK does the heavy lifting in Python.** `client.chat.completions.create(...)` handles URL, headers, JSON, and parsing. In Node I had to write all of that with `fetch` and `JSON.stringify`.
- **No `readline` ceremony.** Python's `input("You: ")` is one line. Node's `readline` needs a `createInterface`, a promise wrapper, and a manual close.
- **Less boilerplate, not less power.** Both files use the same model, same API, same history pattern. The size difference is the SDK, not the logic.

Mental model: Python first when you're prototyping or doing data/ML work; Node first when you're shipping a web app or need async I/O at scale.

## Setup

### 1. API key

Copy `.env.example` to `.env` and put your OpenRouter key in it:

```bash
cp .env.example .env
# then edit .env and set OPENROUTER_API_KEY=...
```

Both Python and Node read this file. Get a key at <https://openrouter.ai/keys>.

### 2. Python + (optional) voice

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install openai python-dotenv

# only needed for voice (chatbot1.0.py + voice_utils.py):
pip install pyttsx3 SpeechRecognition
```

#### macOS voice setup (TTS — `pyttsx3`)

`pyttsx3` on macOS uses the built-in `say` command under the hood. **No extra install needed** — just grant Terminal/iTerm microphone access the first time you run a listening script:

- System Settings → Privacy & Security → **Microphone** → enable your terminal app.

Run:

```bash
python chatbot1.0.py
```

Type `voice` at the prompt to talk, anything else to type. Say `exit` or `quit` to stop.

#### Linux / Windows voice setup

- **Linux:** `pyttsx3` needs `espeak` → `sudo apt install espeak`. PortAudio for the mic → `sudo apt install portaudio19-dev`. Then `pip install pyaudio` (or use `pipwin install pyaudio` if the wheel fails).
- **Windows:** `pyttsx3` uses SAPI5 (built in). For the mic, `pip install PyAudio` works out of the box if you have the Visual C++ build tools; otherwise `pipwin install pyaudio` is the easy path.

#### STT backends

`voice_utils.py` uses `recognizer.recognize_google(...)` — free, no key, needs internet. To swap:

- **Offline:** `recognize_sphinx` (needs `pip install pocketsphinx`) — slow setup, no internet.
- **Whisper:** `recognize_whisper("base")` (needs `pip install openai-whisper`) — heavier but accurate.

### 3. Node

```bash
node index.js
```

Needs Node 18+ for the built-in `fetch`.

## Running

```bash
# Text-only Python
python chatbot.py

# Voice + text Python
python chatbot1.0.py

# Node
node index.js
```

Type `exit` or `quit` to end any of them.

## Versioning convention

I'm using this repo to track my own learning curve. Expect files like `chatbot.py`, `chatbot1.0.py`, `chatbot2.0.py`. Each major bump (`1.0` → `2.0`) means the *logic* changed — new feature, new model, new flow. Read the diff between versions to see what I was figuring out at that step.
