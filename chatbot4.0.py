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


def load_history():
    # if the file exists, read it and return the messages
    # otherwise start with just the default system prompt
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return [DEFAULT_SYSTEM]


def save_history(messages):
    # write the whole messages list to disk after every turn
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(messages, f, indent=2, ensure_ascii=False)


# load whatever we had from the last run
messages = load_history()

print("Chatbot ready. Type 'exit' to quit, or type 'voice' to speak instead.")
print(f"(Loaded {len(messages) - 1} message(s) from {HISTORY_FILE})\n")

while True:
    mode = input("You (type or 'voice'): ")
    if mode.lower() in ("exit", "quit"):
        break

    if mode.lower() == "voice":
        user_input = listen()
        print(f"You said: {user_input}")
        if not user_input:
            continue
    else:
        user_input = mode

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