import os
import json
from openai import OpenAI
from dotenv import load_dotenv
from voice_utils import speak, listen

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

HISTORY_FILE = "chat_history.json"

DEFAULT_SYSTEM = {
    "role": "system",
    "content": "You are a friendly general assistant. Be helpful, clear, and concise."
}

GREETINGS = {"hey", "hi", "hello", "yo", "sup", "hola", "howdy"}
GREETING_REPLY = "Hey! What are you working on today?"

# new in 5.1: sticky input mode
# "text"  -> user types every turn
# "voice" -> user speaks every turn
# switch with the commands below; default is text
input_mode = "text"


def load_history():
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return [DEFAULT_SYSTEM]


def save_history(messages):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)


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

    response = client.chat.completions.create(
        model="deepseek/deepseek-chat",
        messages=summary_prompt,
    )
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


messages = load_history()

print("Chatbot ready. Commands: 'voice', 'text', 'voice off', 'text off',")
print("'last topic?', 'exit'.")
print(f"(Loaded {len(messages) - 1} message(s) from {HISTORY_FILE})\n")

while True:
    user_input = read_input()

    if not user_input:
        continue

    cmd = user_input.lower().strip()

    if cmd in ("exit", "quit"):
        break

    # mode-switching commands
    if cmd == "voice":
        input_mode = "voice"
        print("Switched to voice mode. Speak every turn.")
        continue
    if cmd == "text":
        input_mode = "text"
        print("Switched to text mode.")
        continue
    if cmd == "voice off":
        if input_mode == "voice":
            input_mode = "text"
            print("Voice mode off. Back to text.")
        else:
            print("Voice mode is already off.")
        continue
    if cmd == "text off":
        if input_mode == "text":
            input_mode = "voice"
            print("Text mode off. Back to voice.")
        else:
            print("Text mode is already off.")
        continue

    # command: last topic?
    if cmd.rstrip("?!.") == "last topic":
        reply = last_topic()
        print(f"Bot: {reply}\n")
        speak(reply)
        continue

    # short greeting -> respond locally, no API call, no history append
    if cmd in GREETINGS:
        reply = GREETING_REPLY
        print(f"Bot: {reply}\n")
        speak(reply)
        continue

    # real conversation turn
    messages.append({"role": "user", "content": user_input})
    save_history(messages)

    stream = client.chat.completions.create(
        model="deepseek/deepseek-chat",
        messages=messages,
        stream=True,
    )

    reply = ""
    print("Bot: ", end="", flush=True)

    for chunk in stream:
        piece = chunk.choices[0].delta.content
        if piece is None:
            continue

        reply += piece
        print(piece, end="", flush=True)

    print("\n")

    speak(reply)

    messages.append({"role": "assistant", "content": reply})
    save_history(messages)