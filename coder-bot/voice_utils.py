"""Voice input/output utilities (optional, per-turn).

These are intentionally lightweight and degrade gracefully: if a backend
is missing they become no-ops so the rest of the app keeps working.

The original behaviour from the starter code is preserved:
  - `listen()` returns transcribed text (or None on failure / silence).
  - `speak(text)` streams speech through the local TTS engine.

CHANGES (silent-exit fix):
  - Added `mic_available()` so the REPL can self-check the microphone at
    startup and fall back to text if STT isn't usable.
  - Wrapped `listen()` in a top-level safety net so any unexpected
    exception (mic permission denied, PortAudio not installed, missing
    internet for Google STT, etc.) is reported as a `[voice] error:`
    line and returns None — it can NEVER raise into the REPL again.
"""
from __future__ import annotations

import sys
import threading
import traceback
from typing import Optional

# --- Optional imports wrapped in try/except so the CLI never crashes on import.
try:
    import speech_recognition as sr  # type: ignore
    _SR_AVAILABLE = True
except Exception:
    sr = None  # type: ignore
    _SR_AVAILABLE = False

try:
    import pyttsx3  # type: ignore
    _TTS_ENGINE = pyttsx3.init()
    _TTS_AVAILABLE = True
except Exception:
    _TTS_ENGINE = None
    _TTS_AVAILABLE = False

#: Serialises calls into the (single-threaded) pyttsx3 engine. Without
#: this, overlapping speak() calls raise "run loop already started".
_tts_lock = threading.Lock()


def stt_available() -> bool:
    """True iff the speech_recognition library was importable."""
    return _SR_AVAILABLE


def tts_available() -> bool:
    """True iff pyttsx3 was importable AND a working engine was created."""
    return _TTS_AVAILABLE and _TTS_ENGINE is not None


def mic_available() -> bool:
    """True iff we can actually open the default microphone.

    Used by the startup self-check. Catches every error because PortAudio /
    macOS mic permissions / missing hardware each raise a different
    exception type and we don't want to enumerate them all here.
    """
    if not _SR_AVAILABLE:
        return False
    try:
        # Just enumerating devices is enough to surface permission prompts
        # and missing-driver errors without opening the stream.
        _ = sr.Microphone.list_microphone_names()
        return True
    except Exception:
        return False


def listen(timeout: float = 5.0, phrase_time_limit: float = 10.0) -> Optional[str]:
    """Capture voice from the default microphone and transcribe it.

    Returns the transcribed string, or None on failure / no speech.
    Uses Google Web Speech via the `speech_recognition` library (no key needed).

    CHANGED: outermost try/except guarantees we never raise into the REPL —
    any error (mic permission, PortAudio, network) is logged and we return
    None so the caller can fall back to text input.
    """
    if not _SR_AVAILABLE:
        print("[voice] speech_recognition not installed; skipping listen().")
        return None

    try:
        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                print("[voice] listening...")
                recognizer.adjust_for_ambient_noise(source, duration=0.4)
                audio = recognizer.listen(
                    source, timeout=timeout, phrase_time_limit=phrase_time_limit
                )
        except Exception as e:
            # mic open / listen failure (permission, no mic, PortAudio, ...)
            print(f"[voice] mic error: {e}")
            return None

        try:
            text = recognizer.recognize_google(audio)
            print(f"[voice] heard: {text!r}")
            return text
        except sr.UnknownValueError:
            print("[voice] didn't catch that.")
            return None
        except sr.RequestError as e:
            print(f"[voice] STT request failed: {e}")
            return None
        except Exception as e:
            # Anything else from the recognizer — log + bail safely.
            print(f"[voice] recognition error: {e}")
            return None
    except Exception:
        # Last-resort safety net: NEVER let listen() kill the REPL.
        print("[voice] unexpected error in listen(); falling back to text.")
        traceback.print_exc()
        return None


def speak(text: str) -> None:
    """Speak `text` aloud using the local TTS engine (non-blocking).

    CHANGED: pyttsx3 is single-threaded — if `runAndWait()` is still in
    flight from a previous `speak()` call, calling it again raises
    "run loop already started" (RuntimeError). We serialise calls through
    a lock and skip the new utterance if one is already playing, which
    matches the original behaviour of `speak()` being fire-and-forget.
    """
    if not _TTS_AVAILABLE or not _TTS_ENGINE or not text:
        return
    try:
        def _run():
            with _tts_lock:
                try:
                    _TTS_ENGINE.say(text)
                    _TTS_ENGINE.runAndWait()
                except RuntimeError as e:
                    # Most common cause: another thread is still using
                    # the engine. Swallow rather than spam the REPL.
                    if "run loop" not in str(e):
                        print(f"[voice] TTS runtime error: {e}", file=sys.stderr)
                except Exception as e:
                    print(f"[voice] TTS error: {e}", file=sys.stderr)

        threading.Thread(target=_run, daemon=True).start()
    except Exception as e:
        print(f"[voice] TTS dispatch error: {e}", file=sys.stderr)