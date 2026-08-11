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

## Patch — mic calibration in `voice_utils.listen()`

**This is a fix, not a feature.** I am not bumping a version number.

**Symptom:** in voice mode, the bot heard the user fine on the first turn. On subsequent turns it bailed out early — felt like the bot was "rushing" instead of waiting for the user to finish.

**Suspected cause:** the bot's TTS (via `pyttsx3` → macOS `say`) plays through the same speakers the mic is sitting next to. The mic is opened again immediately after `speak()` returns, but the speaker audio is still ringing. `speech_recognition` then auto-tunes `energy_threshold` against that ringing audio as the ambient baseline, so the user's real voice has to exceed a threshold that's already "loud" — it bails on partial phrases.

**Change:** added `recognizer.adjust_for_ambient_noise(source, duration=0.5)` inside `listen()` between opening the mic stream and calling `recognizer.listen()`. This forces a deliberate half-second ambient sample at a known-quiet moment.

**Risk:** I'm guessing. Possible real cause is something else (`pyttsx3` blocking the audio device, `recognize_google` rate limit, OS buffer reuse). If this doesn't fix it, revert the `voice_utils.py` patch — it's one block, fully isolated.

**Revert:** delete the `Calibrating mic...` line and the `_recognizer.adjust_for_ambient_noise(...)` call inside `listen()`.

**Next up (6.0):** token usage printed after each turn so the cost of persistence becomes visible.

## chatbot5.2.py — rewrite, not a patch: online/offline + local Ollama + hybrid STT

**This is a behavior change, not a copy of 5.2 with tweaked wording.** The version number stays at 5.2 because the user-facing commands (`voice`, `text`, `last topic?`, `exit`) and the `chat_history.json` shape are unchanged. Everything below is additive.

**Added — connectivity:**
- `voice_utils.has_internet()` — single socket probe to `8.8.8.8:53` with a ~2s timeout, result cached at module level. Cached so we don't pay the latency on every `listen()` call.
- `mark_offline()` / `mark_online()` — explicit state flips so the chatbot can update the cache when it observes a real network failure or a successful local call. The cache is the source of truth for both the model router and the STT router.

**Added — second client (Ollama):**
- New `OpenAI(api_key="ollama", base_url="http://localhost:11434/v1")` instance alongside the existing OpenRouter one. Both speak the OpenAI chat-completions API, so the rest of the script is agnostic about which one is active.
- `MODELS` registry — a dict of `(label, client, model_id, requires_internet)` tuples for the four models: `deepseek` (OpenRouter, online-only), `qwen` / `gemma` / `phi` (Ollama, local). The `requires_internet` flag is what auto-failover uses to decide whether to swap models on an error.

**Added — model selection:**
- `active_key` / `active_client` / `active_model_id` globals, mutated by `set_active(key)`.
- `pick_initial_model()` — boots `deepseek` if online, first local model if not. Single source of truth used at startup and inside the failover path.
- New commands: `model` (print active) and `model deepseek` / `model qwen` / `model gemma` / `model phi` (switch). Switching to a cloud model while offline prints a heads-up; the next API call will either succeed (and flip the cache to online) or fail over to a local model. Works regardless of online/offline state — overrides everything.

**Changed — calls routed through one helper:**
- All API calls — the main streaming chat loop *and* `last_topic()` — now go through `_call_with_failover(messages, stream)`. It calls the active client, catches any exception, flips `mark_offline()`, and if the active model was online-only, swaps to `pick_initial_model()` and retries once. This is what fixes `last_topic()` offline (it was hardcoded to `deepseek/deepseek-chat` and would have broken the moment you lost internet).

**Changed — streaming loop is now resilient:**
- The `for chunk in stream:` block is wrapped in `try/except`. A mid-stream network drop prints `(stream interrupted: ...)` instead of crashing. If no tokens arrived, the user turn that was just appended to `messages` is popped and re-saved, so `chat_history.json` stays consistent.
- `mark_online()` runs after every successful reply, so the cache self-corrects when connectivity returns.

**Changed — hybrid STT in `voice_utils.listen()`:**
- Capture step (`speech_recognition.Microphone()` + `adjust_for_ambient_noise`) is shared between both paths and unchanged in behavior.
- Online: `recognizer.recognize_google(audio)` — same as before. If Google raises anything other than `UnknownValueError` (no internet mid-call, quota, DNS), it prints a one-liner, calls `mark_offline()`, and falls through to the local whisper attempt instead of crashing.
- Offline: serializes the captured audio to WAV in-memory and `POST`s it as multipart form data with field name `audio` to `http://localhost:5005/transcribe`. Reads `data["text"]`.
- Both paths return `""` on failure with a clear one-line message about which path failed and why. No exceptions propagate to the chatbot loop.
- `speak()` is unchanged — `pyttsx3` is already fully offline. The optional Piper TTS server on port 5006 is not wired up (deferred — `pyttsx3` is good enough for now).

**Why this isn't a 6.0:**
The user's perspective didn't change. Same commands, same `chat_history.json`, same `voice` / `text` toggle pattern. The connectivity-aware backend and the model switcher are infrastructure that the loop *uses*, not new commands the user has to learn *except* for the explicit `model X` ones. The "smallest change that teaches one new idea" rule is broken here on purpose — half a dozen small ideas (caching, failover, model registry, hybrid STT, mid-stream resilience) are all in one file because they share the same state.

**Try it:**
- `python3 chatbot5.2.py` — boots online, deepseek active.
- `model phi` — switches immediately to local phi4-mini. Status line prints `[online | phi4-mini (Ollama, local)]`.
- Disconnect wifi, type something. The first call will either succeed (if you were already on a local model) or print `(network error: ... Switched to qwen2.5-coder:7b (Ollama, local).)` and retry.
- `voice` mode offline: `listen()` will skip Google and go straight to `localhost:5005/transcribe`. If the whisper server isn't running, you'll see `(Local whisper STT failed: ...)` and the prompt returns — no crash.
- `model deepseek` while offline — prints a note, then attempts the call. If internet is actually back, the cache flips to online and the call succeeds. If not, it fails over to a local model again.
- `cat chat_history.json` — same shape as before. Model switches never touch it.

**Things you'll notice (intentional lessons):**
- The connectivity cache is *both* updated by the explicit probe (`has_internet()`) *and* by observed runtime results. Pure probes would lie when the cloud is reachable but the cloud provider is down. Pure observation would lie before the first call. Doing both is the smallest change that handles both cases.
- `_call_with_failover` only retries once. A second failure bubbles up — the chatbot prints `(API error: ...)` and pops the user turn so the history doesn't drift. A retry loop would feel magical in the best case and silently swallow real bugs in the worst case.
- `model` switching is decoupled from `input_mode` switching on purpose. Voice/text is a *transport* choice; model is a *backend* choice. Mixing them into one command would couple two things that change for different reasons.
- `chat_history.json` stays model-agnostic. Switching models mid-conversation just means the next call sends the same list to a different client. The model sees whatever the previous model wrote, including its own quirks. That's a feature for "compare two models on the same context" — not a bug to fix.

**Next up (6.0):** token usage printed after each turn so the cost of persistence becomes visible. Same as before — this entry doesn't deliver it.

## rag.py — folder ingest + ask, single embedding tier (commit 1 of 3)

**Added:** a new module `rag.py` with a single-tier retrieval pipeline, plus two commands in `chatbot5.2.py`. No cascade yet — that's commit 2. No URL ingestion yet — that's commit 3. This entry is the smallest working RAG.

**Why:** you can't ask the model about a folder of notes if the folder isn't part of its context. The classical fix is RAG: chunk, embed, store, retrieve, prepend the top hits to the user turn so the model can quote from them. "Augmented" generation. The reason commit 1 is *just* a single embedding tier with no failover is the same reason 3.0 was just streaming without TTS — once you mix cascade + RAG in one shot you can't tell which part of the answer came from which tier, or which embedding space mixed with which.

**Added — `rag.py`:**
- `OpenRouterEmbeddingFunction` — Chroma-compatible adapter that calls OpenRouter's `/embeddings` (which is OpenAI-compatible, so the OpenAI SDK works as-is) using `openai/text-embedding-3-small`, dim 1536. Single tier — `configure(client)` takes one client.
- `_chunk_text()` — plain word-count splitter, 300-word chunks with 50-word overlap. No LangChain dependency. Returns a list of overlapping windows; the 50 words at the end of chunk N are the first 50 words of chunk N+1, so a sentence that straddles the boundary stays retrievable.
- `_iter_documents(path)` — recursive folder walk that yields `(file_path, text)` for `.txt` and `.md`. Other extensions (including `.bin`) are silently skipped — that's how you can point it at a messy project directory and not get errors.
- `ingest_folder(path)` — walks, chunks, embeds, and `coll.upsert()`s into Chroma. **Chunk IDs are content-addressed** (`{safe_path}__{index}__{sha256[:16]}`), so re-ingesting an unchanged file reports every chunk as `updated` (id matched, vector overwritten with the same value) and zero as `added`. Editing the file to shrink it or shift its chunk boundaries produces new hashes for the new chunks and *leaves the old chunks orphaned in the collection*, which the function reports as `orphaned` — committed-2's TODO is to delete-by-source before re-upserting to clean those up. Returns a dict `{"added": int, "updated": int, "orphaned": int}` so the chatbot can distinguish a fresh ingest from a no-op re-run.
- `retrieve(query, k=4)` — `coll.query(query_texts=[query], n_results=k)`, returns just the text of the top-k chunks. No reranker, no metadata filtering, no hybrid search.
- `SYSTEM_PROMPT` — instructs the model to answer *only* from the provided context, cite `[source: filename]` for every claim, and say "I don't know" when the context doesn't cover the question. Without this prompt the model ignores the retrieved context and answers from its training data, which defeats the whole point of RAG.
- `build_augmented_messages(question, k=4)` — returns a two-turn message list `[system_with_rag_prompt, user_with_context_then_question]`. **Does NOT append to `messages`** — the retrieved chunks should not become part of `chat_history.json`. The RAG lookup is a per-turn side-effect, not a conversation act.
- `_log_query()` — appends one JSON line per query to `./.chroma/query_log.jsonl` with `collection`, `model`, `dim`, `k`, `query`, `num_results`. This is the file that will prove commit 2's tier-switching works: you can `cat` the sidecar and see which embedding model served which query.

**Changed — `chatbot5.2.py`:**
- Imports `rag` and calls `rag.configure(OPENROUTER_CLIENT)` once at startup (after both clients are built, before the first user turn). All embedding work goes through that one client; in commit 2, the configure call will gain more arguments for the cascade.
- Two new commands in the main loop, inserted after the `model` handler:
  - `ingest <path>` — walks, embeds, prints `ingested N chunks into <collection name>`. Path can be relative (`./corpus`); the chroma store resolves to absolute.
  - `ask <question>` — builds the augmented messages, sends them through `_call_with_failover(...)` so the same cascade that handles ordinary chat now handles RAG-augmented chat. Streams the reply and (in voice mode) speaks it. **Does not touch `chat_history.json`** — the assistant reply to an `ask` is not persisted by design; if you want it persisted, type the same question normally.
- Startup banner now lists `ingest <path>` and `ask <question>` alongside the existing `voice` / `text` / `model` / `exit` commands.

**Added — infrastructure:**
- `chromadb 1.5.9` installed; `.chroma/` added to `.gitignore` (the persistent store is local-only; the source files in your corpus are what get shared, not the vectors).

**Why this is three commits and not one:**
Commit 1 ships the happy path: one embedding tier, one provider, folder ingest only. Commit 2 will add a cascade of tiers (e.g. text-embedding-3-small → BGE-M3 via Ollama → a smaller fallback), with `collection_name()` keyed on `{model_slug}_{dim}` so that different embedding spaces never share a collection. Without the sidecar log from this commit, you'd never be able to tell commit 2 was doing the right thing — you'd just see "queries work, what's the problem?". Commit 3 adds URL ingestion, which is the same `ingest_folder` pipeline with a fetcher in front of it. All three commits are independently demoable; you can stop after any of them and have a working tool.

**Try it:**
- `mkdir -p corpus && cat > corpus/hello.md <<'EOF'` — write a few paragraphs of anything.
- Run `python3 chatbot5.2.py`, type `ingest ./corpus`, see `Ingested N new, updated 0 in place, orphaned 0 stale chunks in corpus_openai_text-embedding-3-small_1536.`
- Run `ingest ./corpus` again — the second run reports `added 0, updated N, orphaned 0` (idempotent).
- Edit `corpus/hello.md` to add or remove paragraphs and run `ingest ./corpus` again — the changed chunks show up as `added` and the old chunks from the prior version show up as `orphaned`. That's the signal to add a `delete by source` cleanup in commit 2.
- `ask what does my notes say about <topic>?` — the bot now answers with quotes from your file and `[source: corpus/hello.md]` citations.
- `rm -rf .chroma/` to start over (collection name and dimensions are baked into `collection_name()`, so changing embedding model in commit 2 won't reuse commit 1's vectors — by design).

**Things you'll notice (intentional lessons):**
- The OpenRouter embedding call adds one round-trip per query in addition to the chat completion. Cost is roughly `0.02 USD / 1M tokens` for `text-embedding-3-small`, and a typical chunk is 300 words ≈ 400 tokens, so a 100-document corpus costs on the order of a fraction of a cent to ingest. The chat completion is the dominant cost.
- Retrieval quality is bounded by the chunking. 300/50 was chosen as a default that mostly works for prose — code and tables need different splits. Sentence-aware splitting (LangChain's `RecursiveCharacterTextSplitter`) is a one-import swap when you need it.
- `ask` not touching `chat_history.json` is a deliberate distinction: the user said "ask my notes about X", they didn't say "have a conversation about X". If you DO want the answer persisted, type the same question without `ask` — both paths use the same underlying model, but only one writes to history.
- Content-addressed chunk IDs surface the "I edited a file but my old chunks are still in the collection" problem as a visible `orphaned` count instead of silently corrupting retrieval. The fix for orphans is a `delete where source=path` pass before each upsert — that's the commit-2 TODO and the right place to put it because the cascade touches ingest anyway.
- The sidecar log is the only place to see which embedding model served which query. Commit 2's dashboard will live in that file.

**Next up (commit 2):** cascade of embedding tiers — same `collection_name()` shape, but multiple models, each in its own collection, with a tier selector that picks based on query length or a manual override.

### rag.py — hygiene pass (no behavioural change)

**Changed:**
- `from __future__ import annotations` — modern type-hint syntax without importing `List`/`Dict` from `typing`.
- Type hints on the public API (`configure`, `ingest_folder`, `retrieve`, `build_augmented_messages`), `OpenRouterEmbeddingFunction`'s four methods, `_chunk_text`, `_chunk_id`, `_read_file`, `_iter_documents`, `_safe_slug`, `collection_name`, `_chroma_client`, `_get_or_create_collection`, `_log_query`.
- `existing: list[str] = coll.get(ids=ids, include=[]).get("ids", [])` — `include=[]` stops Chroma from round-tripping ~6 MB of float vectors per 1000-chunk file just to check existence. The IDs are the only field the bookkeeping loop needs.
- `new_set = set(new_idx)` built once before the counter loop, so `if i in new_set` is O(1) per chunk instead of O(n).
- `_read_file` no longer swallows `OSError`/`UnicodeDecodeError` silently. Permission and encoding problems now print `[rag] skip <path>: <ExcType>: <msg>` to stderr so a user doesn't mistake a bad file for an empty document.
- `CHROMA_DIR` and the sidecar path now read from `RAG_CHROMA_DIR` / `RAG_LOG_PATH` env vars, falling back to the script-relative defaults. For Docker / PyInstaller setups where the script lives in a read-only image but data lives in `/data`.

**Not changed:** the embedding tier, the chunking strategy, the orphan-cleanup policy, the system prompt, the cascade plan. This is purely a code-quality commit so the next set of changes (paragraph-aware chunker, delete-by-source, token-budget guard, embedding cascade) lands on a clean baseline.

**Why a separate commit instead of folding in:** mixing "now `include=[]` is set" with "now I rewrite chunking" makes the diff unreadable. Each chunking rewrite will orphan every chunk in the existing corpus, and that's a *behavioural* change that deserves its own commit message — folding the structural changes into the same diff makes it hard to bisect later.

### rag.py — citation metadata wired through retrieve()

**Changed:** `retrieve(query, k=4)` now returns `list[(text, source)]` instead of `list[str]`. `build_augmented_messages` formats each context chunk as `[N] [source: <path>] <chunk>`.

**Why:** the original `retrieve()` returned only the chunk text and dropped Chroma's `metadatas` field. The system prompt told the model to cite sources — but the model can only cite what it's been shown. Without the source path in the prompt, even a perfectly grounded answer rendered without attribution.

**Diff vs the first amend:**
- `retrieve()` zips docs with metas and pairs each chunk with its `source` metadata field (written at ingest time).
- `build_augmented_messages` unpacks the pairs to render `[source: ...]` inline next to each chunk.
- Module docstring updated to reflect the new return shape.

**No new behaviour at the model layer** — the model was already instructed to cite. The fix is purely in what data the prompt contains.
