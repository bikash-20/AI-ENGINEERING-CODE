import requests
import config
import providers


class AllProvidersFailedError(Exception):
    pass


def _status_code(exc: requests.exceptions.HTTPError) -> int | None:
    return exc.response.status_code if exc.response is not None else None


def stream(messages: list[dict], pinned: tuple[str, str] | None = None):
    """Try each (kind, model) tier in order; return (label, chunk_iterator)
    for the first one that connects successfully.

    A 429 from OpenRouter means the free-tier quota is exhausted for the
    whole account, not just that one model — so after the first 429, every
    remaining OpenRouter tier is skipped (they'd just fail too, and failed
    attempts still count against the daily quota) and we go straight to
    the Ollama tiers.
    """
    tiers = [pinned] if pinned else config.TIERS
    errors = []
    openrouter_exhausted = False

    for kind, model in tiers:
        if kind == "openrouter" and openrouter_exhausted:
            continue

        label = f"{kind}:{model}"
        print(f"[trying {label}...]")
        try:
            resp = providers.OPENERS[kind](model, messages)
        except requests.exceptions.HTTPError as e:
            print(f"[failed {label}: {e}]")
            errors.append(f"{label} -> {e}")
            if kind == "openrouter" and _status_code(e) == 429:
                openrouter_exhausted = True
                print("[free-tier quota likely exhausted — skipping remaining OpenRouter tiers]")
            continue
        except Exception as e:
            print(f"[failed {label}: {e}]")
            errors.append(f"{label} -> {e}")
            continue

        return label, providers.stream_chunks(kind, resp)

    raise AllProvidersFailedError("\n".join(errors))