import os
from openai import OpenAI
from dotenv import load_dotenv
from voice_utils import speak, listen

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

messages = []

print("Chatbot ready. Type 'exit' to quit, or type 'voice' to speak instead.\n")

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

    response = client.chat.completions.create(
        model="deepseek/deepseek-chat",
        messages=messages,
    )

    reply = response.choices[0].message.content
    messages.append({"role": "assistant", "content": reply})
    print(f"Bot: {reply}\n")
    speak(reply)