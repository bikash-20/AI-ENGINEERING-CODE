"""Voice I/O with hybrid STT and offline TTS.

STT (listen):
  - online  -> Google Web Speech API (recognize_google) as before
  - offline -> local faster-whisper server at http://localhost:5005/transcribe
              (POST multipart "audio" field, JSON {"text": "..."} response)

TTS (speak):
  - pyttsx3, fully offline. Unchanged from prior versions.

Connectivity:
  - has_internet() probes 8.8.8.8:53 with a ~2s timeout (cached).
  - set_online(bool) / mark_offline() let the chatbot flip the cached state
    when it sees a network failure, so listen() picks the right path next time.
"""

import io
import socket
import wave


_online = None  # cached connectivity state: True / False / None (unknown)


def has_internet(timeout=2.0):
    """Return True if we can reach 8.8.8.8:53 within `timeout` seconds.

    Result is cached after the first successful or failed call. Call
    set_online(False) / mark_offline() to override the cache when the
    chatbot detects a network failure mid-session.
    """
    global _online
    if _online is not None:
        return _online
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("8.8.8.8", 53))
        sock.close()
        _online = True
    except OSError:
        _online = False
    return _online


def set_online(value):
    """Force the cached connectivity state. Used after a runtime check."""
    global _online
    _online = bool(value)


def mark_offline():
    """Flip the cache to offline. Called by the chatbot when an API call fails."""
    set_online(False)


def mark_online():
    """Flip the cache to online. Called by the chatbot after a successful call."""
    set_online(True)


# ----------------------------------------------------------------------
# TTS — macOS `say`, offline. No third-party driver needed.
# ----------------------------------------------------------------------
# Previously this used pyttsx3, which on newer macOS / Python combinations
# (3.14, fresh installs) fails to bind the NSSpeechSynthesizer driver and
# ends up "saying" the text by dumping each glyph to stdout as a Unicode
# emoji replacement. `subprocess.run(["say", ...])` uses the native macOS
# TTS directly, which is what pyttsx3's macOS driver was shelling out to
# anyway. On Linux/Windows this is a no-op; the caller should already be
# gating on input_mode == "voice" / "text" before invoking speak().

def speak(text):
    """Speak `text` out loud via the macOS `say` command. No-op on non-mac."""
    if not text:
        return
    import subprocess
    try:
        subprocess.run(["say", text], check=False)
    except FileNotFoundError:
        # `say` is missing — we're not on macOS, or it was removed. Silently
        # no-op rather than crashing the chatbot loop on every reply.
        pass
    except Exception as e:
        # Any other failure (broken pipe, weird argv) must not break the
        # chatbot. Voice is a polish feature, not a correctness one.
        print(f"(speak failed: {e})")


# ----------------------------------------------------------------------
# STT — hybrid: Google online, local faster-whisper offline.
# ----------------------------------------------------------------------

_recognizer = None
_sr = None

# local faster-whisper server endpoint
_WHISPER_URL = "http://localhost:5005/transcribe"
_WHISPER_TIMEOUT = 30  # seconds; whisper on CPU can be slow


def _capture_audio():
    """Open the mic, calibrate against ambient noise, and record one utterance.

    Returns the raw audio_data (a sr.AudioData) so the caller can pick
    which recognizer to feed it to (Google bytes vs. WAV for whisper).
    """
    global _recognizer, _sr
    if _recognizer is None:
        import speech_recognition as sr
        _sr = sr
        _recognizer = sr.Recognizer()

    with _sr.Microphone() as source:
        # sample the room for half a second before listening
        # the bot just finished speaking through the same speakers,
        # so the mic hears ringing audio as the baseline.
        # this calibrates against the actual quiet room.
        print("Calibrating mic...")
        _recognizer.adjust_for_ambient_noise(source, duration=0.5)
        print("Listening...")
        audio = _recognizer.listen(source)
    return audio


def _audio_data_to_wav_bytes(audio):
    """Serialize an sr.AudioData to a WAV byte string (in-memory)."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(audio.sample_width)
        wf.setframerate(audio.sample_rate)
        wf.writeframes(audio.get_raw_data())
    return buf.getvalue()


def _transcribe_local(audio):
    """POST the captured audio to the local faster-whisper server."""
    import requests  # only needed for the offline path

    wav_bytes = _audio_data_to_wav_bytes(audio)
    files = {"audio": ("utterance.wav", wav_bytes, "audio/wav")}

    resp = requests.post(_WHISPER_URL, files=files, timeout=_WHISPER_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return (data.get("text") or "").strip()


def listen():
    """Listen from the microphone and convert speech to text.

    Online  -> Google Web Speech API.
    Offline -> local faster-whisper server.

    Always returns a string. Returns "" and prints a one-line message
    if recognition fails for any reason (no internet + no local server,
    Google quota, audio not understood, etc.).
    """
    try:
        audio = _capture_audio()
    except Exception as e:
        print(f"(mic error: {e})")
        return ""

    if has_internet():
        # ---- Online path: Google Web Speech API (unchanged behavior) ----
        try:
            text = _recognizer.recognize_google(audio)
            return text
        except _sr.UnknownValueError:
            return ""
        except Exception as e:
            # anything else from Google (no internet mid-call, quota, network)
            # -> flip to offline and fall through to whisper if available
            print(f"(Google STT failed: {e}. Falling back to local whisper.)")
            mark_offline()

    # ---- Offline path: local faster-whisper server ----
    try:
        text = _transcribe_local(audio)
        return text
    except Exception as e:
        # most common: ConnectionError because the whisper server isn't running
        print(f"(Local whisper STT failed: {e})")
        return ""
