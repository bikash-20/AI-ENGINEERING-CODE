# Changelog

Each entry is the smallest change that teaches one new idea. Read the diff between versions to see what was learned.

## chatbot2.0.py — system prompt

**Added:** one new piece of state — a `system` turn at the very start of `messages`.

**Why:** every chat API takes a list of turns with three roles: `system`, `user`, `assistant`. `1.0` only used `user` and `assistant`. The `system` turn doesn't come from the conversation — it sets the rules the model follows for the whole session. Same responses, different *framing*.

**Changed:**
- `chatbot2.0.py`: appends a `system` message before the loop starts. Loop itself is unchanged.
- `voice_utils.py`: imports moved inside the functions that use them. Text-only mode no longer needs `pyttsx3` or `speech_recognition` installed.

**Try it:** change the `system` content to "You are a strict math tutor. Always ask if the student wants a hint before answering." and re-run. Same code, different bot.

**Next up (3.0):** stream tokens so the reply appears word-by-word.

## chatbot3.0.py — streaming + TTS after

**Added:** `stream=True` on the API call.

**Why:** without streaming you stare at a blank line while the model thinks, then the whole reply dumps at once. With streaming the first token arrives almost instantly — the terminal feels alive. The model isn't faster overall; you just stop waiting for the *whole* answer to start.

**Changed:**
- `chatbot3.0.py`: `create(...)` now uses `stream=True` and returns an iterator. The loop accumulates each `delta.content` into `reply` and prints it with `flush=True`. Same `messages` history, same system prompt, same voice/text switch.
- TTS is called *once*, after the stream finishes, with the full reply. Calling `speak()` inside the stream loop races with the iterator — `runAndWait()` blocks while chunks keep arriving, and on macOS some chunks get dropped or the stream stalls. That's the "audio cuts off" bug the first 3.0 had.
- `voice_utils.py`: unchanged.

**Try it:** compare `python3 chatbot2.0.py` vs `python3 chatbot3.0.py` on the same prompt. 2.0 prints the full reply at once. 3.0 prints it token-by-token in the terminal, then speaks the whole reply once it's done.

**Things you'll notice (intentional lessons):**
- Terminal feels alive even though wall-clock time is similar.
- Audio starts after the reply is finished, not during. That's the trade-off for not dropping chunks.
- The model still doesn't *know* it's streaming; the API just delivers earlier.

**Next up (4.0):** persist `messages` to a JSON file so the bot remembers across runs. Right now exit = amnesia.

## chatbot4.0.py — persistent history

**Added:** `chat_history.json` on disk. The bot now remembers across runs.

**Why:** until now, `messages` lived only in RAM. Exit → process dies → amnesia. Persisting `messages` to a file is the smallest step that turns this into a real product primitive — and it's also the first place you'll *feel* the context window: long sessions grow the file, the file becomes part of every API request, and eventually you have to decide what to drop.

**Changed:**
- `chatbot4.0.py`: two helpers — `load_history()` (read JSON or start with default system prompt) and `save_history()` (write the whole list). Loop is otherwise identical to 3.0: same stream, same TTS-after, same voice/text switch.
- Save runs **after every turn** (both user and assistant). Crash loses at most one message instead of everything. File is human-readable JSON; you can `cat chat_history.json` to see what the bot remembers about you.

**Try it:**
- Run `python3 chatbot4.0.py`, ask it your name, exit. Re-run and ask "what's my name?". The bot remembers.
- `rm chat_history.json` to start over with a fresh system prompt.
- Edit the file directly — change a past assistant reply — and re-run. The bot now believes that lie.

**Things you'll notice (intentional lessons):**
- Persona is sticky. The system prompt saved in the file wins over the one in code. Change the `DEFAULT_SYSTEM` constant but leave the old file alone → you keep the old persona.
- File size grows linearly with turns. After ~50 turns of conversation you're shipping thousands of tokens per request. That's the context-window problem.
- `save_history` writes the *entire* list every turn, not an append. Fine for a chat file, but it's why you don't want to do this for a million-user product.

**Next up (5.0):** show `usage.prompt_tokens` / `completion_tokens` after each turn so you can watch the cost shape a long conversation.

## chatbot5.0.py — commands + local greeting

**Added:** two shortcuts in the loop, no model call needed for either.

- **`last topic?`** — reads `chat_history.json`, finds the last `user` turn, and asks the model to compress it into ≤ 8 words. Returns the line. Done with a tiny one-shot prompt so the main history is untouched.
- **Greetings (`hey`, `hi`, `hello`, `yo`, `sup`, `hola`, `howdy`)** — handled locally. Bot replies with a fixed open question and nothing gets appended to `messages`.

**Why:** a real chatbot shouldn't have to call the model for every trivial turn. Greetings are noise — saving them to history means the model will eventually "remember" that you said hey 200 times. Commands like `last topic?` are a different shape: they're not conversation, they're a *query against history*. Treating them as a separate code path keeps the main chat history clean.

**Changed:**
- `chatbot5.0.py`: new `GREETINGS` set, `GREETING_REPLY` constant, and `last_topic()` helper. Loop checks the raw input *before* appending to `messages`.

**Try it:**
- Run, say `hey` — bot responds locally, no API call, history unchanged.
- Run, type `last topic?` after some conversation — bot replies with a one-liner about what you last asked.
- `cat chat_history.json` — neither the greeting nor the `last topic?` query is in there. Only real conversation turns.

**Things you'll notice (intentional lessons):**
- A greeting being "free" means the bot feels faster on common inputs. Same trick product bots use for "thanks", "ok", emoji-only replies.
- `last_topic()` uses a *separate* prompt array, not the main `messages`. The summary never enters the conversation. If you wanted it to, you'd append it as a fake user turn — that's a design choice, not a bug.
- The greeting matcher uses `strip()` and a small set. Add `"good morning"` later if you want; the loop is now a small command dispatcher instead of a one-track chat.

**Next up (6.0):** token usage (`usage.prompt_tokens` / `completion_tokens`) printed after each turn so the cost of persistence becomes visible.
