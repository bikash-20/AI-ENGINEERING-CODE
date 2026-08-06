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

# new in 5.0: greetings are handled locally, not sent to the model
# the response is short and consistent, and we don't pollute history
GREETINGS = {"hey", "hi", "hello", "yo", "sup", "hola", "howdy"}
GREETING_REPLY = "Hey! What are you working on today?"


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


def last_topic():
    # new in 5.0: ask the model to compress the last user turn into one line
    past = load_history()

    # walk backward and find the most recent user message
    last_user = None
    for turn in reversed(past):
        if turn["role"] == "user":
            last_user = turn["content"]
            break

    if last_user is None:
        return "I don't have any past conversations yet."

    # send a tiny prompt just for the summary, don't touch the main history
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


# load whatever we had from the last run
messages = load_history()

print("Chatbot ready. Type 'exit' to quit, or type 'voice' to speak instead.")
print(f"(Loaded {len(messages) - 1} message(s) from {HISTORY_FILE})")
print("(Try: 'hey', 'last topic?', or just chat normally)\n")

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

    # command: last topic?
    if user_input.lower().strip().rstrip("?!.") == "last topic":
        reply = last_topic()
        print(f"Bot: {reply}\n")
        speak(reply)
        continue

    # short greeting -> respond locally without hitting the model
    if user_input.lower().strip() in GREETINGS:
        reply = GREETING_REPLY
        print(f"Bot: {reply}\n")
        speak(reply)
        continue

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