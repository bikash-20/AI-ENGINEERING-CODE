import config
import cascade

HELP = """
Commands:
  /model <kind:model>   pin one model, e.g. /model ollama:gemma3:4b
  /auto                  return to full cascade mode
  /status                show current mode and the cascade order
  /help                  show this message
  /quit                  exit
"""


def parse_pin(arg: str) -> tuple[str, str] | None:
    if ":" not in arg:
        print("usage: /model <kind:model>   e.g. /model openrouter:openai/gpt-oss-20b:free")
        return None
    kind, model = arg.split(":", 1)
    if kind not in providers_kinds():
        print(f"kind must be one of: {', '.join(providers_kinds())}")
        return None
    return kind, model


def providers_kinds() -> list[str]:
    return sorted({kind for kind, _ in config.TIERS})


def display_name(label: str) -> str:
    """Map a cascade label like 'openrouter:model:free' to its short alias."""
    return config.DISPLAY_NAMES.get(label, label.split(":")[-1])


def print_status(pinned: tuple[str, str] | None) -> None:
    mode = f"pinned -> {pinned[0]}:{pinned[1]}" if pinned else "auto (full cascade)"
    print(f"mode: {mode}")
    print("cascade order:")
    for kind, model in config.TIERS:
        label = f"{kind}:{model}"
        print(f"  {display_name(label):<8} {label}")


def main() -> None:
    history = [{"role": "system", "content": config.SYSTEM_PROMPT}]
    pinned: tuple[str, str] | None = None

    print("MEMO online. Type /help for commands, /quit to exit.")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not text:
            continue
        if text == "/quit":
            break
        if text == "/help":
            print(HELP)
            continue
        if text == "/auto":
            pinned = None
            print("[cascade mode restored]")
            continue
        if text == "/status":
            print_status(pinned)
            continue
        if text.startswith("/model "):
            pin = parse_pin(text[len("/model "):].strip())
            if pin:
                pinned = pin
                print(f"[pinned to {pinned[0]}:{pinned[1]}]")
            continue
        if text.startswith("/"):
            print(f"unknown command: {text}  (try /help)")
            continue

        history.append({"role": "user", "content": text})
        try:
            label, chunks = cascade.stream(history, pinned=pinned)
        except cascade.AllProvidersFailedError as e:
            print(f"[all providers failed]\n{e}")
            history.pop()  # don't poison history with an unanswered turn
            continue

        print(f"{display_name(label)}> ", end="", flush=True)
        reply_parts: list[str] = []
        try:
            for chunk in chunks:
                print(chunk, end="", flush=True)
                reply_parts.append(chunk)
        except Exception as e:
            print(f"\n[stream interrupted: {e}]")
        print()

        reply = "".join(reply_parts)
        if reply:
            history.append({"role": "assistant", "content": reply})
        else:
            history.pop()


if __name__ == "__main__":
    main()