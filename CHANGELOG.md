# Changelog

Each entry is the smallest change that teaches one new idea. Read the diff between versions to see what was learned.

## chatbot2.0.py — system prompt

**Added:** one new piece of state — a `system` turn at the very start of `messages`.

**Why:** every chat API takes a list of turns with three roles: `system`, `user`, `assistant`. `1.0` only used `user` and `assistant`. The `system` turn doesn't come from the conversation — it sets the rules the model follows for the whole session. Same responses, different *framing*.

**Changed:**
- `chatbot2.0.py`: appends a `system` message before the loop starts. Loop itself is unchanged.
- `voice_utils.py`: imports moved inside the functions that use them. Text-only mode no longer needs `pyttsx3` or `speech_recognition` installed.

**Try it:** change the `system` content to "You are a strict math tutor. Always ask if the student wants a hint before answering." and re-run. Same code, different bot.

**Next up (3.0):** pick a specific persona and pin the model to something smaller so you can feel the cost trade-off.
