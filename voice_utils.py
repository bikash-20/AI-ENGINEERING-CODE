import speech_recognition as sr
import pyttsx3

recognizer = sr.Recognizer()
tts_engine = pyttsx3.init()


def speak(text):
    """Convert text to speech and play it out loud."""
    tts_engine.say(text)
    tts_engine.runAndWait()


def listen():
    """Listen from the microphone and convert speech to text."""
    with sr.Microphone() as source:
        print("Listening...")
        audio = recognizer.listen(source)
    try:
        return recognizer.recognize_google(audio)
    except sr.UnknownValueError:
        return ""
    