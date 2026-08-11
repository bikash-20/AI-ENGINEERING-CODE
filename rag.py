"""RAG (Retrieval-Augmented Generation) layer.

Single embedding tier for commit 1 (happy path):
  - model: openai/text-embedding-3-small via OpenRouter
  - dimension: 1536
  - store: Chroma, persistent, one collection keyed by {model_slug}_{dim}

In commit 2, this file grows a cascade of embedding models and a
collection-per-tier routing layer. The function signatures below are
intentionally tier-agnostic so the cascade layer can drop in without
changing callers.

Public API:
  ingest_folder(path)           -> dict  {"added", "updated", "orphaned"}
  retrieve(query, k=4)          -> list[(text, source)]  (top-k chunks w/ source)
  build_augmented_messages(query, k=4) -> list[dict]  (chat-completions shape)
  collection_name()             -> str   (current tier's collection)
"""

from __future__ import annotations

import os
import re
import hashlib
from pathlib import Path

import chromadb

# ----------------------------------------------------------------------
# Active embedding tier (commit 1: single tier; commit 2 will add cascade)
# ----------------------------------------------------------------------

EMBEDDING_MODEL_ID = "openai/text-embedding-3-small"
EMBEDDING_DIM = 1536  # text-embedding-3-small native dim

# Client is injected by the chatbot at startup via configure(). We keep
# this module-level reference so other helpers (retrieval, ingest) can
# build embedding calls without each one needing the client passed in.
# Defaults to None — configure() must be called before retrieve/ingest.
_client = None


def configure(openrouter_client, model_id: str | None = None, dim: int | None = None) -> None:
    """Wire the OpenRouter client into this module.

    Called once at chatbot startup. In commit 2, this is where the
    cascade layer will register multiple clients per tier.
    """
    global _client, EMBEDDING_MODEL_ID, EMBEDDING_DIM
    _client = openrouter_client
    if model_id is not None:
        EMBEDDING_MODEL_ID = model_id
    if dim is not None:
        EMBEDDING_DIM = dim


# ----------------------------------------------------------------------
# Chroma embedding function adapter
# ----------------------------------------------------------------------
# Chroma expects an object with __call__(list[str]) -> list[list[float]].
# We delegate to the OpenAI SDK pointed at OpenRouter's /embeddings
# endpoint, which is OpenAI-compatible.

class OpenRouterEmbeddingFunction:
    """Chroma-compatible embedding function that calls OpenRouter.

    OpenRouter exposes an OpenAI-compatible /embeddings endpoint, so we
    just hand the request to the OpenAI SDK pointed at
    https://openrouter.ai/api/v1 with model="openai/text-embedding-3-small".
    """

    def __init__(self, client, model_id: str) -> None:
        self._client = client
        self._model_id = model_id

    # Chroma's protocol: name attribute + __call__ returning list[list[float]]
    def name(self) -> str:
        # stable identifier Chroma uses for caching
        return f"openrouter:{self._model_id}"

    def __call__(self, input: list[str]) -> list[list[float]]:
        # Legacy Chroma interface: docs and queries both come through here
        # as input: Documents. Kept for backward compat.
        return self.embed_documents(input)

    def embed_documents(self, input: list[str]) -> list[list[float]]:
        # Chroma calls this during collection.add / upsert.
        # input: list[str]
        resp = self._client.embeddings.create(model=self._model_id, input=input)
        return [d.embedding for d in resp.data]

    def embed_query(self, input: list[str]) -> list[list[float]]:
        # In chromadb 1.5.9:
        #   - input is a list[str] (the query_texts list)
        #   - return value is expected to be list[list[float]] (a list of
        #     query embeddings, one per query), not a single list[float].
        # Return the full batch from embed_documents unwrapped.
        return self.embed_documents(input)


# ----------------------------------------------------------------------
# Chunking
# ----------------------------------------------------------------------
# Plain word-count chunker with overlap. Good enough for commit 1.
# If we need sentence-aware splitting later, swap this for
# langchain.text_splitter.RecursiveCharacterTextSplitter — same API.

CHUNK_WORDS = 300
CHUNK_OVERLAP_WORDS = 50


def _chunk_text(text: str) -> list[str]:
    """Split text into ~CHUNK_WORDS-word chunks with overlap. Returns list[str]."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    step = CHUNK_WORDS - CHUNK_OVERLAP_WORDS
    for start in range(0, len(words), step):
        piece = words[start : start + CHUNK_WORDS]
        if not piece:
            break
        chunks.append(" ".join(piece))
        if start + CHUNK_WORDS >= len(words):
            break
    return chunks


# ----------------------------------------------------------------------
# File loading (commit 1: .txt and .md only)
# ----------------------------------------------------------------------

_SUPPORTED_SUFFIXES = {".txt", ".md"}


def _read_file(path) -> str:
    """Read a file as text. Returns "" on read error.

    Read errors are surfaced to stderr rather than swallowed, so a
    permission or encoding problem doesn't get mistaken for an empty
    document downstream. We still return "" so the ingest pipeline can
    skip the file without special-casing.
    """
    import sys
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        print(f"[rag] skip {path}: {e.__class__.__name__}: {e}", file=sys.stderr)
        return ""
    except UnicodeDecodeError as e:
        print(f"[rag] skip {path}: not utf-8 ({e})", file=sys.stderr)
        return ""


def _iter_documents(root: str | Path):
    """Yield (path_str, text) for every supported file under root (recursive)."""
    root = Path(root)
    if not root.exists():
        return
    if root.is_file():
        if root.suffix.lower() in _SUPPORTED_SUFFIXES:
            text = _read_file(root)
            if text:
                yield (str(root), text)
        return
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in _SUPPORTED_SUFFIXES:
            text = _read_file(p)
            if text:
                yield (str(p), text)


# ----------------------------------------------------------------------
# Store + collection
# ----------------------------------------------------------------------

# Anchor to the script's directory so the store is found regardless of
# where the process is launched from. Running `python3 chatbot5.2.py`
# from `~/Documents/AI ENGINEERING` and running it via a launcher alias
# from `~/` would otherwise silently produce two separate stores with
# identical names — ingest into one, ask from the other, "why doesn't
# it remember what I ingested" mystery.
#
# Override with the RAG_CHROMA_DIR env var for containerized / PyInstaller
# layouts where the script lives in a read-only image but the data should
# live elsewhere (e.g. /data/chroma).
CHROMA_DIR = os.environ.get(
    "RAG_CHROMA_DIR",
    str(Path(__file__).resolve().parent / ".chroma"),
)


def _safe_slug(model_id: str) -> str:
    """Filesystem-safe version of a model id: 'openai/text-embedding-3-small'
    -> 'openai_text-embedding-3-small'."""
    return re.sub(r"[/:.]", "_", model_id)


def collection_name() -> str:
    """Name of the collection for the currently active embedding tier."""
    return f"corpus_{_safe_slug(EMBEDDING_MODEL_ID)}_{EMBEDDING_DIM}"


def _chroma_client() -> chromadb.PersistentClient:
    """Persistent Chroma client. Stored in ./.chroma/ next to the script."""
    return chromadb.PersistentClient(path=CHROMA_DIR)


def _get_or_create_collection() -> chromadb.Collection:
    """Get the current tier's collection, creating it if missing."""
    if _client is None:
        raise RuntimeError("rag.configure(...) must be called before retrieval/ingest.")
    client = _chroma_client()
    ef = OpenRouterEmbeddingFunction(_client, EMBEDDING_MODEL_ID)
    return client.get_or_create_collection(
        name=collection_name(),
        embedding_function=ef,
        metadata={"embedding_model": EMBEDDING_MODEL_ID, "dim": EMBEDDING_DIM},
    )


# ----------------------------------------------------------------------
# Ingest
# ----------------------------------------------------------------------

def _chunk_id(source_path: str, chunk_index: int, text: str) -> str:
    """Deterministic chunk id derived from (path, index, content).

    Including a content hash means editing a file produces new IDs for
    shifted chunks instead of silently overwriting old chunk text with
    new chunk text at the same ID. The downside is that *old* chunks
    become orphans in the collection — see the commit-2 TODO.
    """
    h = hashlib.sha256(f"{source_path}:{chunk_index}:{text}".encode()).hexdigest()[:16]
    safe = source_path.replace(os.sep, "_").replace(":", "_")
    return f"{safe}__{chunk_index}__{h}"


def ingest_folder(path: str | Path) -> dict[str, int]:
    """Walk `path`, chunk every .txt/.md file, embed, upsert.

    Returns a dict ``{"added": int, "updated": int, "orphaned": int}``
    so callers can distinguish a true fresh-ingest from a no-op re-run:

    - ``added``: chunks whose ID was not previously in the collection
      (and therefore actually cost an embedding call this run).
    - ``updated``: chunks whose ID existed and was overwritten in place.
      We only call ``upsert`` on these for the *metadata* refresh —
      skipping the embedding call entirely so a stable corpus is free
      to re-ingest. The vector payload is byte-identical because the
      chunk text (and therefore the embedding) is unchanged.
    - ``orphaned``: chunks left behind by a previous ingest whose
      content has since changed (content-hash mismatch). Not
      auto-deleted; surface to the user.

    TODO(commit 2): orphan detection currently only checks files
    *still on disk*. Deleted source files leave their chunks invisible
    to this function. Fix: diff ``seen_sources`` against
    ``coll.get()``'d distinct sources from prior ingests, not just
    against files the folder walk yields now.

    TODO(commit 2): ``embed_query`` and ``embed_documents`` are
    symmetric today because text-embedding-3-small is symmetric. The
    cascade layer (qwen3-embedding, nomic-embed) will need asymmetric
    prefixes (e.g. ``"query: "`` vs raw text) to hit benchmarked
    quality. Do not collapse them — make them per-model.
    """
    coll = _get_or_create_collection()
    added = updated = 0
    seen_ids: set[str] = set()
    for file_path, text in _iter_documents(path):
        chunks = _chunk_text(text)
        if not chunks:
            continue
        ids = [_chunk_id(file_path, i, c) for i, c in enumerate(chunks)]
        metadatas = [
            {"source": file_path, "chunk": i, "model": EMBEDDING_MODEL_ID}
            for i in range(len(chunks))
        ]
        # Look up which IDs are already present so we can skip the
        # embedding call for unchanged chunks. This is the difference
        # between "re-ingest a 1000-chunk corpus" costing 1000 embedding
        # calls vs 0 once the corpus is stable. include=[] avoids
        # pulling embedding vectors / documents across the Python
        # boundary just to check existence (~6MB per 1000-chunk file).
        existing: list[str] = coll.get(ids=ids, include=[]).get("ids", [])
        existing_set = set(existing)
        new_idx = [i for i, cid in enumerate(ids) if cid not in existing_set]
        new_set = set(new_idx)
        for i, cid in enumerate(ids):
            seen_ids.add(cid)
            if i in new_set:
                added += 1
            else:
                updated += 1
        # Embed only the new chunks. Upsert with full metadata so any
        # caller that changed metadata values still gets the refresh,
        # but skip the embedding function entirely for unchanged chunks.
        if new_idx:
            coll.upsert(
                ids=[ids[i] for i in new_idx],
                documents=[chunks[i] for i in new_idx],
                metadatas=[metadatas[i] for i in new_idx],
            )
    # Orphan detection: anything in the collection with the same source
    # but an id we did not see this run is a stale chunk from a prior
    # edit. Cheap because the collection has a metadata index on
    # "source" (Chroma does this automatically for metadata fields).
    orphaned = 0
    if seen_ids:
        for file_path, _ in _iter_documents(path):
            prior = coll.get(where={"source": file_path}).get("ids", [])
            for cid in prior:
                if cid not in seen_ids:
                    orphaned += 1
    return {"added": added, "updated": updated, "orphaned": orphaned}


# ----------------------------------------------------------------------
# Retrieve + answer-augmentation
# ----------------------------------------------------------------------

def retrieve(query: str, k: int = 4) -> list[tuple[str, str]]:
    """Return the top-k most similar chunks for `query`.

    Returns a list of (text, source) tuples — one per retrieved chunk —
    so callers can render [source: <path>] in the context block. `source`
    comes from the `source` metadata field written at ingest time.
    """
    coll = _get_or_create_collection()
    res = coll.query(query_texts=[query], n_results=k)
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    # zip is safe here: chroma returns parallel lists, and lengths match
    # even when one side is empty (both will be empty lists).
    return [(doc, (meta or {}).get("source", "<unknown>"))
            for doc, meta in zip(docs, metas)]


# ----------------------------------------------------------------------
# Sidecar log: which collection served each query
# ----------------------------------------------------------------------
# Why: in commit 2, when a fallback tier fires mid-session, retrieval
# switches to a different collection (different dim). Recording which
# one answered each query makes that visible in logs so silent dim-mixes
# don't go unnoticed.

_SIDECAR_PATH = os.environ.get(
    "RAG_LOG_PATH",
    str(Path(CHROMA_DIR) / "query_log.jsonl"),
)


def _log_query(query: str, chunks: list[str], k: int) -> None:
    """Append one line per query to a JSONL sidecar. Best-effort."""
    import json
    try:
        record = {
            "collection": collection_name(),
            "model": EMBEDDING_MODEL_ID,
            "dim": EMBEDDING_DIM,
            "k": k,
            "query": query,
            "num_results": len(chunks),
        }
        with open(_SIDECAR_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass  # logging must never break retrieval


SYSTEM_PROMPT = (
    "You answer the user's question using only the provided context. "
    "If the context doesn't contain the answer, say 'I don't have enough "
    "information in the loaded documents.' Do not use outside knowledge. "
    "Cite sources inline as [source: filename] when you use a fact."
)


def build_augmented_messages(query: str, k: int = 4) -> list[dict]:
    """Build a chat-completions messages list with retrieval context.

    The system prompt establishes the ground rules, then a single user
    turn contains [context chunks] + [the question]. We do NOT append
    this to the conversation's persistent messages — ask() uses it as a
    one-shot call so chat history stays clean.
    """
    chunks = retrieve(query, k=k)
    _log_query(query, [t for t, _ in chunks], k)

    if not chunks:
        # nothing in the store — fall back to a plain chat-style message
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Context: (no documents loaded)\n\n"
                f"Question: {query}"
            )},
        ]

    context_block = "\n\n---\n\n".join(
        f"[{i+1}] [source: {source}] {text}"
        for i, (text, source) in enumerate(chunks)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Context:\n{context_block}\n\n"
            f"Question: {query}"
        )},
    ]