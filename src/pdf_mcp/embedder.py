"""Thin wrapper around fastembed for lazy model loading and text embedding.

The embedding model is loaded once per process (singleton). fastembed is an
optional dependency; calling encode() when it is not installed raises
ImportError with an actionable install hint.

Model: nomic-ai/nomic-embed-text-v1.5 (Nomic AI, NYC; Apache 2.0; fully-open
training data + code + weights). Chosen over the upstream BAAI default for
provenance reasons — see SECURITY-AUDIT.md §Nomic for the full rationale,
per-file SHA-256 hashes, and airgap install procedure.

Two integration notes specific to Nomic v1.5:

1. Prefixes. Unlike BAAI/bge-small, Nomic v1.5 is trained with separate
   prompts for documents vs queries; retrieval quality drops ~5 MTEB points
   without them. Applied transparently in encode()/encode_query().

2. Matryoshka truncation. Nomic v1.5's native output is 768-dim, but it is
   explicitly trained to be truncatable to 384 / 256 / 128 / 64 dimensions
   with minimal quality loss. We truncate to 384 to preserve binary cache
   compatibility with the pre-existing SQLite blob schema (384 * 4 bytes =
   1536 bytes per vector). L2-renormalise after truncation so dot-product
   still equals cosine similarity.
"""

from __future__ import annotations

from typing import Any

MODEL_NAME = "nomic-ai/nomic-embed-text-v1.5"
EMBEDDING_DIM = 384  # Matryoshka truncation target (native is 768)

# Nomic v1.5 prompt prefixes. Must match exactly; these are trained-in tokens.
DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "

# Module-level singleton. None until the first encode() call.
_model: Any = None


def check_available() -> None:
    """Raise ImportError with install hint if fastembed is not installed.

    Call this before running semantic search to give a clear error before
    any expensive PDF work begins.
    """
    try:
        import fastembed  # type: ignore[import-untyped,import-not-found]  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "pdf_search semantic mode requires the 'fastembed' package. "
            "Install it with: pip install 'pdf-mcp-ocr-more-secure[semantic]' "
            "and see SECURITY-AUDIT.md for the recommended side-load procedure "
            "on hardened workstations."
        ) from exc


def _get_model() -> Any:
    """Load embedding model on first call; return cached model on later calls."""
    global _model
    if _model is None:
        try:
            from fastembed import TextEmbedding  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "pdf_search semantic mode requires the 'fastembed' package. "
                "Install it with: pip install 'pdf-mcp-ocr-more-secure[semantic]'"
            ) from exc
        _model = TextEmbedding(MODEL_NAME)
    return _model


def _truncate_and_renormalize(vectors: Any, dim: int) -> Any:
    """Truncate embedding vectors to `dim` dimensions and L2-renormalise.

    Matryoshka-trained models (Nomic v1.5) preserve retrieval quality at
    reduced dims, but the truncated vector is no longer unit-norm and must
    be renormalised for dot-product similarity to equal cosine similarity.
    """
    import numpy as np

    truncated = vectors[:, :dim]
    norms = np.linalg.norm(truncated, axis=1, keepdims=True)
    # Avoid division by zero on all-zero rows (shouldn't happen in practice).
    norms = np.where(norms == 0, 1.0, norms)
    return (truncated / norms).astype(np.float32)


def encode(texts: list[str]) -> Any:
    """Encode a list of documents into embedding vectors.

    Applies Nomic v1.5's ``search_document: `` prefix, generates 768-dim
    embeddings, truncates to 384 dim (Matryoshka), and L2-renormalises.

    Returns an ndarray of shape (N, 384), dtype float32.
    """
    import numpy as np

    model = _get_model()
    prefixed = [DOCUMENT_PREFIX + t for t in texts]
    embeddings = np.array(list(model.embed(prefixed)), dtype=np.float32)
    return _truncate_and_renormalize(embeddings, EMBEDDING_DIM)


def encode_query(text: str) -> Any:
    """Encode a single query string.

    Applies Nomic v1.5's ``search_query: `` prefix, generates a 768-dim
    embedding, truncates to 384 dim (Matryoshka), and L2-renormalises.

    Returns an ndarray of shape (384,), dtype float32.
    """
    import numpy as np

    model = _get_model()
    prefixed = [QUERY_PREFIX + text]
    embeddings = np.array(list(model.embed(prefixed)), dtype=np.float32)
    return _truncate_and_renormalize(embeddings, EMBEDDING_DIM)[0]
