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

# same system prompt as 2.0
messages.append({
    "role": "system",
    "content": "You are a friendly general assistant. Be helpful, clear, and concise."
})

print("Chatbot ready. Type 'exit' to quit, or type 'voice' to speak instead.\n")

# new in 3.0: streaming
# the model sends back the reply in small chunks instead of all at once
# we print each chunk as it arrives and also speak it sentence-by-sentence

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

    # stream=True turns the response into an iterator of chunks
    stream = client.chat.completions.create(
        model="deepseek/deepseek-chat",
        messages=messages,
        stream=True,
    )

    reply = ""
    tts_buffer = ""
    print("Bot: ", end="", flush=True)

    for chunk in stream:
        # not every chunk has content (some are just stop signals)
        piece = chunk.choices[0].delta.content
        if piece is None:
            continue

        reply += piece
        print(piece, end="", flush=True)

        # feed TTS in sentence-sized chunks so it doesn't restart on every token
        tts_buffer += piece
        if piece.endswith((".", "!", "?", "\n")):
            speak(tts_buffer)
            tts_buffer = ""

    # flush whatever's left after the stream ends
    if tts_buffer.strip():
        speak(tts_buffer)

    print("\n")

    messages.append({"role": "assistant", "content": reply})
