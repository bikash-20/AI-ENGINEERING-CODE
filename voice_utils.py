_recognizer = None
_sr = None
_tts_engine = None


def speak(text):
    """Convert text to speech and play it out loud."""
    global _tts_engine
    if _tts_engine is None:
        import pyttsx3
        _tts_engine = pyttsx3.init()
    _tts_engine.say(text)
    _tts_engine.runAndWait()


def listen():
    """Listen from the microphone and convert speech to text."""
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
    try:
        return _recognizer.recognize_google(audio)
    except _sr.UnknownValueError:
        return ""
    