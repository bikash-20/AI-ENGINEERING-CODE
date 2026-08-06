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

## chatbot3.0.py — streaming + live TTS

**Added:** `stream=True` on the API call + a sentence buffer that hands text to `pyttsx3` as it's generated.

**Why:** without streaming you stare at a blank line while the model thinks, then the whole reply dumps at once. With streaming the first token arrives almost instantly — terminal *and* TTS can start in parallel. The model isn't faster overall; you just stop waiting for the *whole* answer to start.

**Changed:**
- `chatbot3.0.py`: `create(...)` now uses `stream=True` and returns an iterator. Loop accumulates into `reply` and prints each `delta.content` with `flush=True`. Same `messages` history, same system prompt, same voice/text switch.
- TTS buffer: instead of speaking one chunk at a time (which makes `say` re-spawn per token), I accumulate until the buffer ends with `.` `!` `?` or `\n`, then speak. Anything left after the stream ends gets flushed too.
- `voice_utils.py`: unchanged.

**Try it:** compare `python3 chatbot2.0.py` vs `python3 chatbot3.0.py` on the same prompt. 2.0 prints the full reply at once. 3.0 prints it token-by-token *and* starts talking before it's done typing.

**Things you'll notice (intentional lessons):**
- The terminal feels alive even though wall-clock time is similar.
- TTS may slightly lag the terminal — `say` has a small per-call cost on macOS.
- The model still doesn't *know* it's streaming; the API just delivers earlier.

**Next up (4.0):** persist `messages` to a JSON file so the bot remembers across runs. Right now exit = amnesia.
