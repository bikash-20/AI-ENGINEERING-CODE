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

## chatbot5.1.py — sticky input mode

**Added:** a single `input_mode` variable (`"text"` or `"voice"`) that survives between turns.

**Why:** in 5.0, typing `voice` worked for *one* turn. Next turn you had to type `voice` again. The user expected a *mode*, not a *command*. A real chat client has a stable input mode you switch into and out of — same idea here.

**Changed:**
- `chatbot5.1.py`: new `input_mode` global plus `read_input()` helper that picks the right read path each turn. The loop body is otherwise identical to 5.0 — the same command parser, the same streaming, the same TTS-after.
- New commands: `voice`, `text`, `voice off`, `text off`. The `* off` form is symmetric: if the mode is already off, you get a friendly "already off" message instead of a silent mode change.

**Try it:**
- `python3 chatbot5.1.py`
- Type `voice` → says "Switched to voice mode". Next prompt is `[type a command, or Enter to speak]:`. Press Enter, speak, bot replies. Press Enter, speak, bot replies. No more typing `voice` every turn.
- Type `text` → switches back without ending the session.
- In voice mode, type `last topic?` at the prompt — it's a command, recognized the same way as text mode.

**Things you'll notice (intentional lessons):**
- Voice mode still asks for a typed line every turn so you can issue commands. The alternative — voice-only command recognition — would need its own recognition layer. Out of scope here.
- `input_mode` is *not* persisted across runs. Quitting and restarting drops you back to text. That's deliberate: a forgotten mode is worse than a default mode.
- The greeting handler and `last topic?` command work in both modes without special-casing. They run *after* the unified command branch, so the loop has one body, not two.

**Next up (6.0):** token usage printed after each turn so the cost of persistence becomes visible.

## chatbot5.2.py — tighter mode-switch wording

**Changed:** the `"voice"` and `"text"` / `"voice off"` branches in the loop.

**Why:** the wording in 5.1 was redundant. `"Voice mode is already on."` for a one-word command is fine but verbose; the bot's persona is "friendly and concise" and the messages should match. Also, `"text off"` and `"voice off"` were duplicating each other — if voice is off, you're already in text mode, so a single command handles both.

**Diff vs 5.1:**
- `voice` already on → "Already in voice mode." (was: "Voice mode is already on.")
- `text` / `voice off` already on text → "Already in text mode." (was: separate messages per branch)
- Removed `text off` command entirely. `voice off` covers both directions.
- Startup banner lists only the commands that actually exist.

**No code logic changed.** Same `input_mode` global, same `read_input()` helper, same loop body. This is a copy of 5.1 with tighter copy and a smaller command surface.

**Try it:** `python3 chatbot5.2.py` — same flow as 5.1, shorter mode messages.

**Next up (6.0):** token usage printed after each turn so the cost of persistence becomes visible.
