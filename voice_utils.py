_recognizer = None
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
    global _recognizer
    if _recognizer is None:
        import speech_recognition as sr
        _recognizer = sr.Recognizer()
    with _recognizer.Microphone() as source:
        print("Listening...")
        audio = _recognizer.listen(source)
    try:
        return _recognizer.recognize_google(audio)
    except _recognizer.UnknownValueError:
        return ""
    