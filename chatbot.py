import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1",
)

messages = []

print("Chatbot ready. Type 'exit' to quit.\n")

while True:
    user_input = input("You: ")
    if user_input.lower() in ("exit", "quit"):
        break

    messages.append({"role": "user", "content": user_input})

    response = client.chat.completions.create(
        model="deepseek/deepseek-chat",
        messages=messages,
    )

    reply = response.choices[0].message.content
    messages.append({"role": "assistant", "content": reply})
    print(f"Bot: {reply}\n")