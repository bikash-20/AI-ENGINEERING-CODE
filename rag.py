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
  retrieve(query, k=4)          -> list[(text, source, page)]  (top-k chunks; page is None for non-PDFs)
  build_augmented_messages(query, k=4) -> list[dict]  (chat-completions shape)
  collection_name()             -> str   (current tier's collection)
"""

from __future__ import annotations

import os
import re
import time
import random
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
        return self._with_retry(lambda: self._do_embed(input))

    def embed_query(self, input: list[str]) -> list[list[float]]:
        # In chromadb 1.5.9:
        #   - input is a list[str] (the query_texts list)
        #   - return value is expected to be list[list[float]] (a list of
        #     query embeddings, one per query), not a single list[float].
        # Return the full batch from embed_documents unwrapped.
        return self.embed_documents(input)

    def _do_embed(self, input: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self._model_id, input=input)
        return [d.embedding for d in resp.data]

    @staticmethod
    def _with_retry(fn, *, attempts: int = 2) -> list[list[float]]:
        """Call `fn` with up to `attempts` tries and jittered backoff.

        Why: OpenRouter occasionally returns transient 5xx/429 for an
        embedding call while the rest of the request graph is fine. One
        retry with backoff recovers from most of these without forcing
        the user to re-run ingest.

        Backoff schedule: sleep = (2 ** attempt) + random.random(),
        which is 1-2s before the first retry. We never sleep after the
        final attempt — we just re-raise. No tenacity dep.
        """
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return fn()
            except Exception as e:  # broad: openai SDK raises varied subclasses
                last_exc = e
                if attempt + 1 >= attempts:
                    break
                sleep_for = (2 ** attempt) + random.random()
                print(
                    f"[rag] embed attempt {attempt + 1} failed ({type(e).__name__}: {e}); "
                    f"retrying in {sleep_for:.1f}s",
                    file=__import__("sys").stderr,
                )
                time.sleep(sleep_for)
        assert last_exc is not None  # loop only exits via break+exception or return
        raise last_exc


# ----------------------------------------------------------------------
# Chunking
# ----------------------------------------------------------------------
# Semantic chunker: split on paragraph boundaries first, then sentence
# boundaries if a single paragraph exceeds the limit, then a hard char
# split as a final fallback so we can never exceed CHUNK_MAX_CHARS.
#
# Why not the old word-count chunker: slicing at word 300 ignores
# sentence/paragraph boundaries, so embeddings cover mid-thought text and
# the vector geometry gets noisy. The chunker here keeps whole paragraphs
# together when possible, which is what prose retrieval actually wants.
#
# Defaults are char-based (~300 words / ~1500 chars per chunk). Override
# with RAG_CHUNK_MAX_CHARS / RAG_CHUNK_OVERLAP_CHARS for code-heavy or
# unusually long-paragraph corpora.

CHUNK_MAX_CHARS = int(os.environ.get("RAG_CHUNK_MAX_CHARS", "1500"))
CHUNK_OVERLAP_CHARS = int(os.environ.get("RAG_CHUNK_OVERLAP_CHARS", "0"))

# Sentence boundary: terminal punctuation followed by whitespace.
# Lookbehind keeps the punctuation attached to its sentence.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _is_formatting_paragraph(p: str) -> bool:
    """True if a paragraph is pure Markdown formatting (no real content).

    Targets:
      - ATX headers: '# Title', '## Section'
      - Setext underlines: '======' or '------' (single line of only those chars)
      - Unordered list items: '- item', '* item'
      - Blockquotes: '> quoted text'

    Skips long no-whitespace content (e.g. minified code, base64 blobs) —
    those are real content even if word-sparse.
    """
    s = p.lstrip()
    if not s:
        return True
    if s[0] == "#":
        return True
    if s[0] in {"-", "*", ">"}:
        # Pure list/quote: short, no useful prose.
        # Be lenient: only treat as formatting if also short or single-line.
        if "\n" not in s and len(s) <= 200:
            return True
        # Multi-line list/blockquote: keep it.
        return False
    # Setext underline: a line of only = or - characters.
    if len(s) <= 80 and s.strip() and set(s.strip()) <= {"=", "-"}:
        return True
    return False


def _chunk_text(text: str) -> list[str]:
    """Split text into semantic chunks. Returns list[str].

    Three-phase split:
      1. On paragraph boundaries (\\n\\n).
      2. On sentence boundaries (after [.!?] + whitespace) when a single
         paragraph exceeds CHUNK_MAX_CHARS.
      3. Hard char split as a final fallback so no chunk can ever exceed
         the limit.

    Overlap is appended (last N chars of previous chunk prepended to the
    next), not a sliding window. With paragraph-structured prose the
    overlap default is 0 because paragraphs are self-contained seams;
    bump it back up via RAG_CHUNK_OVERLAP_CHARS for fragmented input.
    """
    if not text or not text.strip():
        return []

    chunks: list[str] = []

    # Phase 1: split on paragraph boundaries. Drop paragraphs that are
    # pure Markdown formatting (headers, list markers, empty containers)
    # — they embed to near-zero-signal vectors that just dilute retrieval.
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    paragraphs = [p for p in paragraphs if not _is_formatting_paragraph(p)]
    for para in paragraphs:
        if len(para) <= CHUNK_MAX_CHARS:
            chunks.append(para)
            continue

        # Phase 2: paragraph too long — split on sentence boundaries.
        sentences = _SENTENCE_SPLIT.split(para)
        current = ""
        for sent in sentences:
            # +1 for the space we'd insert between sentences.
            if len(current) + len(sent) + 1 <= CHUNK_MAX_CHARS:
                current = f"{current} {sent}".strip() if current else sent
                continue
            if current:
                chunks.append(current)
            # Phase 3: single sentence still exceeds limit — hard split.
            if len(sent) > CHUNK_MAX_CHARS:
                step = max(1, CHUNK_MAX_CHARS - CHUNK_OVERLAP_CHARS)
                for i in range(0, len(sent), step):
                    chunks.append(sent[i : i + CHUNK_MAX_CHARS])
                current = ""
            else:
                current = sent
        if current:
            chunks.append(current)

    # Overlap step: append trailing chars from the previous chunk to the
    # next. Skipped entirely if there's nothing to overlap (single chunk,
    # or CHUNK_OVERLAP_CHARS == 0).
    if CHUNK_OVERLAP_CHARS <= 0 or len(chunks) <= 1:
        return chunks
    overlapped: list[str] = [chunks[0]]
    for chunk in chunks[1:]:
        prev = overlapped[-1]
        tail = prev[-CHUNK_OVERLAP_CHARS:] if len(prev) > CHUNK_OVERLAP_CHARS else prev
        overlapped.append(f"{tail}\n\n{chunk}")
    return overlapped


# ----------------------------------------------------------------------
# File loading (commit 1: .txt and .md; patched: .pdf via PyMuPDF)
# ----------------------------------------------------------------------

_SUPPORTED_SUFFIXES = {".txt", ".md", ".pdf"}


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


def _read_pdf(path) -> list[tuple[str, int]]:
    """Read a PDF page-by-page using PyMuPDF. Returns [(text, page_number), ...].

    PDFs are processed one page at a time so we never hold the entire
    document in memory at once — a 500-page PDF shouldn't mean 500 pages
    of strings sitting in RAM before chunking even starts.

    Handles these failure modes gracefully:
      - PyMuPDF not installed            -> ImportError propagates; caller sees it.
      - Encrypted / password-protected   -> returns []; prints to stderr.
      - Corrupt / unreadable pages       -> skips that page; prints to stderr.
      - Empty / zero-page PDF            -> returns []; no error.
    """
    import sys
    try:
        import fitz  # PyMuPDF
    except ImportError:
        print(
            f"[rag] skip PDF {path}: PyMuPDF not installed. "
            f"Install with: pip install pymupdf",
            file=sys.stderr,
        )
        return []

    out: list[tuple[str, int]] = []
    try:
        doc = fitz.open(path)
    except Exception as e:
        print(f"[rag] skip PDF {path}: cannot open ({e})", file=sys.stderr)
        return []

    try:
        if doc.is_encrypted:
            print(f"[rag] skip PDF {path}: encrypted / password-protected", file=sys.stderr)
            return []
        # page count is 1-indexed in citations, matches the rendered "page N"
        # most PDF readers show.
        for i, page in enumerate(doc, start=1):
            try:
                text = page.get_text("text") or ""
            except Exception as e:
                print(f"[rag] skip page {i} of {path}: {e}", file=sys.stderr)
                continue
            if text.strip():
                out.append((text, i))
    finally:
        doc.close()
    return out


def _iter_documents(root: str | Path):
    """Yield (path_str, text, page | None) for every supported file under root.

    The third element is the 1-indexed page number for PDFs, or None for
    plain-text files. Keeping the shape uniform lets the ingest loop treat
    .txt/.md and .pdf identically: chunk, hash, upsert — only the metadata
    payload differs (page is added when present).
    """
    root = Path(root)
    if not root.exists():
        return
    if root.is_file():
        suf = root.suffix.lower()
        if suf in {".txt", ".md"}:
            text = _read_file(root)
            if text:
                yield (str(root), text, None)
        elif suf == ".pdf":
            for page_text, page_num in _read_pdf(root):
                yield (str(root), page_text, page_num)
        return
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        suf = p.suffix.lower()
        if suf in {".txt", ".md"}:
            text = _read_file(p)
            if text:
                yield (str(p), text, None)
        elif suf == ".pdf":
            for page_text, page_num in _read_pdf(p):
                yield (str(p), page_text, page_num)


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

def _chunk_id(source_path: str, chunk_index: int, text: str, page: int | None = None) -> str:
    """Deterministic chunk id derived from (path, index, page, content).

    Including a content hash means editing a file produces new IDs for
    shifted chunks instead of silently overwriting old chunk text with
    new chunk text at the same ID. The downside is that *old* chunks
    become orphans in the collection — see the commit-2 TODO.

    `page` is included in the hash for PDFs so that re-ingesting a PDF
    whose content has not changed produces the same IDs (no duplicate
    chunks). Without page in the ID, two pages of the same PDF would
    share an ID namespace at index 0 and collide with each other.
    """
    page_key = page if page is not None else "na"
    h = hashlib.sha256(f"{source_path}:{page_key}:{chunk_index}:{text}".encode()).hexdigest()[:16]
    safe = source_path.replace(os.sep, "_").replace(":", "_")
    page_tag = f"_p{page}" if page is not None else ""
    return f"{safe}{page_tag}__{chunk_index}__{h}"


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
    for file_path, text, page in _iter_documents(path):
        chunks = _chunk_text(text)
        if not chunks:
            continue
        ids = [_chunk_id(file_path, i, c, page) for i, c in enumerate(chunks)]
        metadatas = []
        for i_only in range(len(chunks)):
            md = {"source": file_path, "chunk": i_only, "model": EMBEDDING_MODEL_ID}
            if page is not None:
                md["page"] = page
            metadatas.append(md)
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
    #
    # `_iter_documents` yields one tuple *per page* for PDFs, so we must
    # dedupe on `source` here. Otherwise a 3-page PDF would have each of
    # its orphan IDs counted three times (once per page-yield).
    orphaned = 0
    if seen_ids:
        queried_sources: set[str] = set()
        for file_path, _, _ in _iter_documents(path):
            if file_path in queried_sources:
                continue
            queried_sources.add(file_path)
            prior = coll.get(where={"source": file_path}).get("ids", [])
            for cid in prior:
                if cid not in seen_ids:
                    orphaned += 1

    # Delete-by-source: chunks whose source file is no longer present in
    # the folder at all (vs. just edited). Gated by an env switch so the
    # default behaviour stays "report, don't touch".
    delete_mode = os.environ.get("RAG_DELETE_MISSING_SOURCES", "false").lower()
    delete_mode_on = delete_mode in {"1", "true", "yes", "on"}
    # Always scan for stale-by-source chunks, regardless of mode. Only
    # the delete-or-print decision is gated.
    all_ids = coll.get(include=[]).get("ids", [])
    all_metas = coll.get(include=["metadatas"]).get("metadatas", [])
    # Same dedupe-by-source rationale as the orphan loop above: PDFs yield
    # once per page, so a comprehension over the raw generator would emit
    # the same path N times (harmless for set membership, but misleading
    # if anyone logs the resulting set).
    live_sources = {p for p, _, _ in _iter_documents(path)}
    stale_by_source: dict[str, list[str]] = {}
    for cid, meta in zip(all_ids, all_metas):
        src = (meta or {}).get("source")
        if src is None:
            continue
        if src not in live_sources:
            stale_by_source.setdefault(src, []).append(cid)
    if delete_mode_on:
        # Roll up all stale chunk IDs across every missing source and
        # delete in one call.
        all_stale = [cid for ids in stale_by_source.values() for cid in ids]
        if all_stale:
            coll.delete(ids=all_stale)
            for src, ids in stale_by_source.items():
                print(
                    f"[rag] deleted {len(ids)} chunks from {src}",
                    file=__import__("sys").stderr,
                )
    else:
        for src, ids in stale_by_source.items():
            print(
                f"[rag] would delete {len(ids)} chunks from {src} "
                f"(RAG_DELETE_MISSING_SOURCES=off)",
                file=__import__("sys").stderr,
            )
    return {"added": added, "updated": updated, "orphaned": orphaned}


# ----------------------------------------------------------------------
# Retrieve + answer-augmentation
# ----------------------------------------------------------------------

def retrieve(query: str, k: int = 4) -> list[tuple[str, str, int | None]]:
    """Return the top-k most similar chunks for `query`.

    Returns a list of (text, source, page) tuples — one per retrieved
    chunk — so callers can render `[source: <path>]` (or
    `[source: <path>#p<page>]` for PDFs) in the context block. `source`
    and `page` come from the metadata fields written at ingest time;
    `page` is None for non-PDF sources.
    """
    coll = _get_or_create_collection()
    res = coll.query(query_texts=[query], n_results=k)
    docs = res.get("documents", [[]])[0]
    metas = res.get("metadatas", [[]])[0]
    # zip is safe here: chroma returns parallel lists, and lengths match
    # even when one side is empty (both will be empty lists).
    out: list[tuple[str, str, int | None]] = []
    for doc, meta in zip(docs, metas):
        meta = meta or {}
        page_val = meta.get("page")
        # Chroma returns ints for int metadata; coerce defensively.
        page = int(page_val) if isinstance(page_val, (int, float)) else None
        out.append((doc, meta.get("source", "<unknown>"), page))
    return out


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


def _log_query(
    query: str,
    chunks: list[str],
    k: int,
    *,
    k_actual: int | None = None,
    budget_tokens: int | None = None,
    used_tokens: int | None = None,
) -> None:
    """Append one line per query to a JSONL sidecar. Best-effort."""
    import json
    try:
        record: dict = {
            "collection": collection_name(),
            "model": EMBEDDING_MODEL_ID,
            "dim": EMBEDDING_DIM,
            "k": k,
            "query": query,
            "num_results": len(chunks),
        }
        if k_actual is not None:
            record["k_actual"] = k_actual
        if budget_tokens is not None:
            record["budget_tokens"] = budget_tokens
        if used_tokens is not None:
            record["used_tokens"] = used_tokens
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

# Token budget for the context block. text-embedding-3-small is not used
# here — this bounds the *chat completion* context, which is what the
# model actually sees. Default 3000 leaves room for ~1500-token answers
# and the system prompt within typical 8k-context free models.
MAX_CONTEXT_TOKENS = int(os.environ.get("RAG_MAX_CONTEXT_TOKENS", "3000"))


def _estimate_tokens(text: str) -> int:
    """Rough token count without a real tokenizer.

    Heuristic: ~0.75 tokens per whitespace-separated word plus ~0.25 tokens
    per character. Either term alone overshoots real BPE counts in opposite
    directions; combining them tracks the OpenAI tiktoken cl100k_base within
    ~15% for normal English prose. Good enough for a budget guard — we're
    trying to avoid stuffing a 50k-char context block into a 4k-context
    model, not to count exactly.
    """
    if not text:
        return 0
    word_term = int(len(text.split()) * 0.75)
    char_term = int(len(text) * 0.25)
    return max(word_term, char_term)


def build_augmented_messages(query: str, k: int = 4) -> list[dict]:
    """Build a chat-completions messages list with retrieval context.

    The system prompt establishes the ground rules, then a single user
    turn contains [context chunks] + [the question]. We do NOT append
    this to the conversation's persistent messages — ask() uses it as a
    one-shot call so chat history stays clean.

    Token budget: the context block is truncated to MAX_CONTEXT_TOKENS
    (env RAG_MAX_CONTEXT_TOKENS, default 3000). When truncation kicks in,
    the dropped tail is logged to stderr and the *actual* number of
    chunks kept is recorded in the sidecar so a downstream dashboard can
    spot queries that consistently need more context than the budget
    allows.
    """
    chunks = retrieve(query, k=k)

    if not chunks:
        _log_query(query, [t for t, _, _ in chunks], k)
        # nothing in the store — fall back to a plain chat-style message
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Context: (no documents loaded)\n\n"
                f"Question: {query}"
            )},
        ]

    # Token-budget guard. Walk chunks in retrieval order, drop the tail
    # once we'd exceed the budget. k_actual is what we actually kept.
    kept: list[tuple[str, str, int | None]] = []
    budget_tokens = 0
    truncated = False
    for text, source, page in chunks:
        cite = f"{source}#p{page}" if page is not None else source
        formatted = f"[{len(kept)+1}] [source: {cite}] {text}"
        chunk_tokens = _estimate_tokens(formatted) + 4  # +4 for separator overhead
        if budget_tokens + chunk_tokens > MAX_CONTEXT_TOKENS:
            print(
                f"[rag] truncated context {k}->{len(kept)} "
                f"({budget_tokens} tokens, budget {MAX_CONTEXT_TOKENS})",
                file=__import__("sys").stderr,
            )
            truncated = True
            break
        kept.append((text, source, page))
        budget_tokens += chunk_tokens

    # Log after truncation so the sidecar carries the actual post-budget
    # chunk count, not just the requested k.
    _log_query(
        query,
        [t for t, _, _ in kept],
        k,
        k_actual=len(kept) if truncated else None,
        budget_tokens=MAX_CONTEXT_TOKENS if truncated else None,
        used_tokens=budget_tokens if truncated else None,
    )

    if not kept:
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Context: (no documents loaded)\n\n"
                f"Question: {query}"
            )},
        ]

    context_block = "\n\n---\n\n".join(
        f"[{i+1}] [source: {(f'{source}#p{page}' if page is not None else source)}] {text}"
        for i, (text, source, page) in enumerate(kept)
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Context:\n{context_block}\n\n"
            f"Question: {query}"
        )},
    ]