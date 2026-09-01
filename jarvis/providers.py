import json
import requests
import config


def open_openrouter_stream(model: str, messages: list[dict]) -> requests.Response:
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set in .env")
    resp = requests.post(
        config.OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {config.OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": messages, "stream": True},
        timeout=config.REQUEST_TIMEOUT,
        stream=True,
    )
    resp.raise_for_status()
    return resp


def open_ollama_stream(model: str, messages: list[dict]) -> requests.Response:
    resp = requests.post(
        config.OLLAMA_URL,
        json={"model": model, "messages": messages, "stream": True},
        timeout=config.REQUEST_TIMEOUT,
        stream=True,
    )
    resp.raise_for_status()
    return resp


# Opens the connection and confirms the tier is alive (raises immediately on
# auth/network/404 failure, before any tokens are read) — this is what lets
# the cascade fall through to the next tier cleanly.
OPENERS = {
    "openrouter": open_openrouter_stream,
    "ollama": open_ollama_stream,
}


def _parse_openrouter_line(raw: bytes) -> str | None:
    line = raw.decode("utf-8")
    if not line.startswith("data: "):
        return None
    data = line[len("data: "):].strip()
    if data == "[DONE]":
        return None
    delta = json.loads(data)["choices"][0]["delta"]
    return delta.get("content")


def _parse_ollama_line(raw: bytes) -> str | None:
    return json.loads(raw).get("message", {}).get("content")


PARSERS = {
    "openrouter": _parse_openrouter_line,
    "ollama": _parse_ollama_line,
}


def stream_chunks(kind: str, resp: requests.Response):
    """Yield text pieces from an already-opened streaming response."""
    parse = PARSERS[kind]
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        text = parse(raw_line)
        if text:
            yield text