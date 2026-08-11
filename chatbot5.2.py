import os
import json
from openai import OpenAI
from dotenv import load_dotenv

from voice_utils import speak, listen, has_internet, mark_offline, mark_online

import rag

load_dotenv()

# ----------------------------------------------------------------------
# Clients
# ----------------------------------------------------------------------
# OpenRouter, Gemini, and Ollama. All three speak the OpenAI
# chat-completions API, so the rest of the script is agnostic about
# which one is active. Ollama is local/offline-capable; the other two
# are cloud/online-only.

OPENROUTER_CLIENT = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

# Gemini exposes an OpenAI-compatible endpoint at the URL below.
# Pass-through: the SDK just sends standard chat.completions requests.
GEMINI_CLIENT = OpenAI(
    api_key=os.getenv("GEMINI_API_KEY"),
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
)

# Ollama ignores the api_key, but the SDK requires a non-empty string.
OLLAMA_CLIENT = OpenAI(
    api_key="ollama",
    base_url="http://localhost:11434/v1",
)

# Wire the OpenRouter client into rag.py for the embedding tier.
# (In commit 2, this is where the cascade layer will register multiple
# embedding clients and switch between them.)
#
# TODO(commit 2): the `ask` and `ingest` commands below are cloud-only
# right now. If `has_internet()` is False, rag.build_augmented_messages
# still calls OPENROUTER_CLIENT.embeddings.create and the user sees
# `(retrieval error: ...)`. The chat path degrades gracefully via
# mark_offline() and the local-only cascade, but the RAG path does
# not. Once the embedding cascade exists in commit 2, this command's
# exception handlers should call mark_offline() on connectivity-shaped
# embedding failures and prefer a local embedding model from the
# cascade — same pattern as `_call_with_failover`.
rag.configure(OPENROUTER_CLIENT)

# ----------------------------------------------------------------------
# Model registry
# ----------------------------------------------------------------------
# Each entry: (label, client, model_id, requires_internet)
# label        -> what we print and what the user types ("model qwen", etc.)
# client       -> which OpenAI client instance to call
# model_id     -> the model string sent to that client
# online_only  -> True means: only auto-select this when we have internet

MODELS = {
    "deepseek":   ("deepseek/deepseek-chat (OpenRouter)",         OPENROUTER_CLIENT, "deepseek/deepseek-chat",                          True),
    "gemini":     ("gemini-2.0-flash (Gemini)",                    GEMINI_CLIENT,     "gemini-2.0-flash",                                True),
    # OpenRouter `:free` models — no cost, just rate-limited. The lineup
    # rotates; if a model 404s, swap it here. Keep at least one general
    # chat model and one coding-focused model so the cascade is useful
    # regardless of which one is currently free.
    "llama-free": ("llama-3.3-70b-instruct:free (OpenRouter)",    OPENROUTER_CLIENT, "meta-llama/llama-3.3-70b-instruct:free",         True),
    "qwen-free":  ("qwen3-coder:free (OpenRouter)",               OPENROUTER_CLIENT, "qwen/qwen3-coder:free",                           True),
    "laguna-free":("poolside-laguna:free (OpenRouter)",            OPENROUTER_CLIENT, "poolside/laguna:free",                            True),
    "gemini-free":("gemini-2.0-flash-exp:free (OpenRouter)",       OPENROUTER_CLIENT, "google/gemini-2.0-flash-exp:free",                True),
    # Local Ollama models — offline-capable, always available.
    "qwen":       ("qwen2.5-coder:7b (Ollama, local)",            OLLAMA_CLIENT,     "qwen2.5-coder:7b",                                False),
    "gemma":      ("gemma3:4b (Ollama, local)",                    OLLAMA_CLIENT,     "gemma3:4b",                                       False),
    "phi":        ("phi4-mini (Ollama, local)",                    OLLAMA_CLIENT,     "phi4-mini",                                       False),
}

DEFAULT_MODEL_KEY = "deepseek"  # online default; offline override below


def pick_initial_model():
    """Choose which model to boot with based on connectivity."""
    if has_internet():
        return DEFAULT_MODEL_KEY
    # offline: pick the first non-online-only model in the registry
    for key, (_, _, _, online_only) in MODELS.items():
        if not online_only:
            return key
    return DEFAULT_MODEL_KEY  # fallback


# ----------------------------------------------------------------------
# Active selection state
# ----------------------------------------------------------------------
active_key = pick_initial_model()
active_label, active_client, active_model_id, _ = MODELS[active_key]


def set_active(key):
    """Switch to a new model and update the globals. Returns the new label."""
    global active_key, active_label, active_client, active_model_id
    if key not in MODELS:
        return None
    active_key = key
    active_label, active_client, active_model_id, _ = MODELS[key]
    return active_label


def status_line():
    """One-line description of the current mode + model."""
    mode = "online" if has_internet() else "offline"
    return f"[{mode} | {active_label}]"


# ----------------------------------------------------------------------
# History
# ----------------------------------------------------------------------
HISTORY_FILE = "chat_history.json"

DEFAULT_SYSTEM = {
    "role": "system",
    "content": "You are a friendly general assistant. Be helpful, clear, and concise."
}

GREETINGS = {"hey", "hi", "hello", "yo", "sup", "hola", "howdy"}
GREETING_REPLY = "Hey! What are you working on today?"

# input_mode: "text" (default, type every turn) or "voice" (speak every turn)
input_mode = "text"


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return [DEFAULT_SYSTEM]


def save_history(messages):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)


# ----------------------------------------------------------------------
# Fallback chain
# ----------------------------------------------------------------------
# When a call fails, we walk this list in order. The user-facing active
# model is the *first* entry they (or pick_initial_model) picked; the
# remaining entries are the providers we'll try in order if that one
# fails. Entries are model-registry keys, so each one is fully described
# by MODELS[key].

# Online cascade (in order):
#   1. OpenRouter paid    -> deepseek-chat (best quality, costs credits)
#   2. OpenRouter :free   -> llama, qwen3-coder, laguna, gemini-flash
#                            (no quota, just rate-limited; the lineup
#                             rotates, so if one 404s the cascade walks
#                             past it to the next)
#   3. Gemini direct      -> gemini-2.0-flash (separate quota, separate key)
#   4. Local Ollama       -> phi (last resort, no internet needed)
# Offline cascade: local Ollama only (the first gate, has_internet(),
# short-circuits the cloud tiers when there's no internet at all).
FALLBACK_CHAIN = [
    "deepseek",     # OpenRouter paid
    "llama-free",   # OpenRouter free tier (general chat)
    "qwen-free",    # OpenRouter free tier (coding)
    "laguna-free",  # OpenRouter free tier (coding agent)
    "gemini-free",  # OpenRouter free tier (Google-via-OpenRouter)
    "gemini",       # Gemini direct
    "phi",          # local Ollama
]


def _provider_name(key):
    """Short, human-readable provider name for log messages."""
    return {
        "deepseek":    "OpenRouter",
        "gemini":      "Gemini",
        "llama-free":  "OpenRouter free",
        "qwen-free":   "OpenRouter free",
        "laguna-free": "OpenRouter free",
        "gemini-free": "OpenRouter free",
        "qwen":        "Ollama",
        "gemma":       "Ollama",
        "phi":         "Ollama",
    }.get(key, key)


def _is_connectivity_error(exc):
    """True if the exception looks like 'no network', not 'provider rejected us'.

    A 429 / 401 / 500 from a reachable server is a provider-side problem;
    the connectivity cache must NOT flip on those. Only true socket /
    connection / DNS / timeout failures count.
    """
    # The OpenAI SDK wraps HTTP errors in APIError / RateLimitError / etc.
    # Connection problems stay as plain OSError subclasses (or the SDK's
    # APIConnectionError, which inherits from them).
    if isinstance(exc, OSError):
        return True
    name = type(exc).__name__
    # Be explicit about what we treat as connectivity, not a catch-all:
    # - APIConnectionError (SDK): no socket-level connection
    # - APITimeoutError         : request timed out at the transport layer
    if name in ("APIConnectionError", "APITimeoutError"):
        return True
    return False


# ----------------------------------------------------------------------
# Model calls (both go through the cascade)
# ----------------------------------------------------------------------

def _attempt(key, messages, stream):
    """Try a single provider. Returns the response / stream iterator on
    success, raises on failure. Caller handles the cascade."""
    label, client, model_id, _ = MODELS[key]
    return client.chat.completions.create(
        model=model_id,
        messages=messages,
        stream=stream,
    )


def _build_order():
    """Decide which tiers to try, and in what order.

    - Offline: skip all cloud tiers; try the local tier(s) only.
    - Online: start with the user's active model, then walk the rest of
      FALLBACK_CHAIN so the *preferred* order is still preserved as a
      fallback (cloud before local). A manual `model gemini` override
      means Gemini is tried first; if it fails, we still fall through
      OpenRouter -> local, not just back to deepseek.

    The returned list always ends with a local entry so we degrade
    gracefully even when online.
    """
    if not has_internet():
        mark_offline()
        local_only = [k for k in FALLBACK_CHAIN if not MODELS[k][3]]
        return local_only or ["phi"]

    # Online: rotate so active_key is first, but keep FALLBACK_CHAIN order
    # for everything after it. That way the spec'd cascade (OpenRouter ->
    # Gemini -> local) is preserved when active_key is the chain's first
    # entry, and a manual override just changes where the cascade *starts*.
    chain = list(FALLBACK_CHAIN)
    if active_key in chain:
        order = [active_key] + [k for k in chain if k != active_key]
    else:
        # user picked a local model like `model phi` while online -> respect
        # that pick, then fall through to the rest of the chain
        order = [active_key] + chain
    return order


def _call_with_failover(messages, stream=False):
    """Call the active model; on any failure, walk the cascade.

    Order:
      1. If we're offline, skip cloud tiers entirely and start at the
         first local model in FALLBACK_CHAIN.
      2. Otherwise, start at the user's active model.
      3. On any exception (rate limit, timeout, network, 5xx, etc.),
         print a one-line "[Provider failed: <reason> — trying Next...]"
         and try the next entry in FALLBACK_CHAIN.
      4. Connectivity-shaped failures (socket, DNS, timeout) flip the
         cache via mark_offline(); HTTP-level failures (429, 401, 5xx)
         do NOT, because the network is fine and listen() should keep
         using Google.
      5. If everything fails, raise the last exception so the caller
         can show it and roll back the user turn.

    Returns the response / stream iterator on the first success.
    """
    order = _build_order()
    last_err = None

    for i, key in enumerate(order):
        try:
            resp = _attempt(key, messages, stream)
            if key != active_key:
                # we landed on a backup -> update active so the next call
                # starts there, and the user sees what changed
                set_active(key)
                print(f"(switched to {active_label} {status_line()})")
            return resp
        except Exception as e:
            last_err = e
            if _is_connectivity_error(e):
                mark_offline()
            next_name = _provider_name(order[i + 1]) if i + 1 < len(order) else None
            if next_name:
                print(f"[{_provider_name(key)} failed: {e} — trying {next_name}...]")
            else:
                print(f"[{_provider_name(key)} failed: {e}]")

    # every tier failed
    raise last_err


def last_topic():
    past = load_history()

    last_user = None
    for turn in reversed(past):
        if turn["role"] == "user":
            last_user = turn["content"]
            break

    if last_user is None:
        return "I don't have any past conversations yet."

    summary_prompt = [
        {
            "role": "system",
            "content": "You summarize the user's last message in at most 8 words."
        },
        {"role": "user", "content": f"Last topic: {last_user}"}
    ]

    response = _call_with_failover(summary_prompt, stream=False)
    return response.choices[0].message.content.strip()


def read_input():
    # read one user turn using the current mode
    # in voice mode, ask once — type a command, or press Enter to speak
    if input_mode == "voice":
        typed = input("[type a command, or Enter to speak]: ").strip()
        if typed:
            return typed
        heard = listen()
        print(f"You said: {heard}")
        return heard

    return input("You: ")


# ----------------------------------------------------------------------
# Main loop
# ----------------------------------------------------------------------
messages = load_history()

mode_now = "online" if has_internet() else "offline"
print(f"Chatbot ready ({mode_now} mode). Active model: {active_label}.")
print(
    "Commands: 'voice', 'voice off' / 'text', "
    "'last topic?', "
    "'model deepseek | gemini | llama-free | qwen-free | laguna-free | "
    "gemini-free | qwen | gemma | phi', "
    "'ingest <path>', 'ask <question>', "
    "'exit'."
)
print(f"(Loaded {len(messages) - 1} message(s) from {HISTORY_FILE})\n")

while True:
    user_input = read_input()

    if not user_input:
        continue

    cmd = user_input.lower().strip()

    if cmd in ("exit", "quit"):
        break

    # ---- mode-switching commands (unchanged) ----
    if cmd == "voice":
        if input_mode == "voice":
            print("Already in voice mode.")
        else:
            input_mode = "voice"
            print("Switched to voice mode. Speak every turn until you type 'voice off' or 'text'.")
        continue
    if cmd in ("voice off", "text"):
        if input_mode == "text":
            print("Already in text mode.")
        else:
            input_mode = "text"
            print("Switched to text mode.")
        continue

    # ---- model switching ----
    if cmd.startswith("model "):
        requested = cmd.split(" ", 1)[1].strip()
        if requested not in MODELS:
            print(f"Unknown model '{requested}'. Try: {', '.join(MODELS.keys())}.")
            continue
        new_label = set_active(requested)
        # if we switched to a cloud model and we think we're offline,
        # give it a chance — the next API call will confirm.
        if MODELS[requested][3] and not has_internet():
            print(f"Note: '{requested}' needs internet. Will retry connectivity on next call.")
        print(f"Now using: {new_label} {status_line()}")
        continue

    if cmd == "model":
        print(f"Active: {active_label} {status_line()}")
        continue

    # ---- RAG: ingest a folder of documents into the vector store ----
    if cmd.startswith("ingest "):
        path = user_input.split(" ", 1)[1].strip().strip('"').strip("'")
        print(f"Ingesting '{path}'... (this may take a while on first run)")
        try:
            result = rag.ingest_folder(path)
        except Exception as e:
            print(f"(ingest error: {e})")
            continue
        print(
            f"Ingested {result['added']} new, "
            f"updated {result['updated']} in place, "
            f"orphaned {result['orphaned']} stale chunks "
            f"in {rag.collection_name()}.\n"
        )
        continue

    if cmd == "ingest":
        print("Usage: ingest <path>  (folder or single .txt/.md file)")
        continue

    # ---- RAG: ask a question against the loaded documents ----
    if cmd == "ask":
        print("Usage: ask <question>")
        continue
    if cmd.startswith("ask "):
        question = user_input.split(" ", 1)[1].strip()
        if not question:
            print("Usage: ask <question>")
            continue
        print(f"Retrieving for: {question}")
        try:
            aug = rag.build_augmented_messages(question, k=4)
        except Exception as e:
            print(f"(retrieval error: {e})")
            continue
        try:
            stream = _call_with_failover(aug, stream=True)
        except Exception as e:
            print(f"(API error: {e})")
            continue

        reply = ""
        print("Bot: ", end="", flush=True)
        try:
            for chunk in stream:
                piece = chunk.choices[0].delta.content
                if piece is None:
                    continue
                reply += piece
                print(piece, end="", flush=True)
        except Exception as e:
            mark_offline()
            print(f"\n(stream interrupted: {e})")
            continue
        print("\n")
        speak(reply)
        # Note: ask() does NOT append to messages — retrieval-augmented
        # turns would pollute the persistent chat history with raw chunks.
        mark_online()
        continue

    # ---- command: last topic? ----
    if cmd.rstrip("?!.") == "last topic":
        reply = last_topic()
        print(f"Bot: {reply}\n")
        speak(reply)
        continue

    # ---- short greeting -> respond locally, no API call, no history append ----
    if cmd in GREETINGS:
        reply = GREETING_REPLY
        print(f"Bot: {reply}\n")
        speak(reply)
        continue

    # ---- real conversation turn ----
    messages.append({"role": "user", "content": user_input})
    save_history(messages)

    try:
        stream = _call_with_failover(messages, stream=True)
    except Exception as e:
        print(f"(API error: {e})")
        # roll back the user turn we just appended, since there's no reply
        messages.pop()
        save_history(messages)
        continue

    reply = ""
    print("Bot: ", end="", flush=True)

    try:
        for chunk in stream:
            piece = chunk.choices[0].delta.content
            if piece is None:
                continue
            reply += piece
            print(piece, end="", flush=True)
    except Exception as e:
        # mid-stream failure (network died, etc.)
        mark_offline()
        print(f"\n(stream interrupted: {e})")
        if not reply:
            messages.pop()
            save_history(messages)
            continue

    print("\n")

    speak(reply)

    messages.append({"role": "assistant", "content": reply})
    save_history(messages)

    # a successful call proves we have a working connection to the active client
    mark_online()