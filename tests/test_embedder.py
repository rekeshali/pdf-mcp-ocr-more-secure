"""Unit tests for pdf_mcp.embedder. All tests mock fastembed — no model download."""

import sys
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# Nomic v1.5 native output dim. We truncate to 384 via Matryoshka.
_NATIVE_DIM = 768
_TRUNC_DIM = 384


def test_check_available_raises_when_fastembed_missing():
    """check_available() raises ImportError with install hint when fastembed absent."""
    import pdf_mcp.embedder as emb

    # Setting a sys.modules entry to None blocks the import.
    with patch.dict(sys.modules, {"fastembed": None}):
        with pytest.raises(
            ImportError, match="pdf-mcp-ocr-more-secure\\[semantic\\]"
        ):
            emb.check_available()


def _make_mock_model(dim: int = _NATIVE_DIM) -> MagicMock:
    """Mock fastembed TextEmbedding that yields dim-dimensional unit vectors.

    Uses a non-trivial vector (incrementing values) so that truncation +
    renormalisation changes the output in verifiable ways.
    """
    mock = MagicMock()

    def _embed(texts):
        # Distinct vector per text so prefix application is observable.
        for i, _ in enumerate(texts):
            v = np.linspace(0.1, 1.0, dim, dtype=np.float32) + i * 0.01
            yield v

    mock.embed.side_effect = _embed
    return mock


def test_encode_returns_shape_n_by_384_after_truncation():
    """encode(texts) returns ndarray of shape (N, 384) — native 768 truncated."""
    import pdf_mcp.embedder as emb

    emb._model = _make_mock_model(_NATIVE_DIM)
    try:
        result = emb.encode(["hello", "world", "foo"])
    finally:
        emb._model = None

    assert result.shape == (3, _TRUNC_DIM)
    assert result.dtype == np.float32


def test_encode_output_is_unit_normalised():
    """After Matryoshka truncation, vectors are L2-renormalised."""
    import pdf_mcp.embedder as emb

    emb._model = _make_mock_model(_NATIVE_DIM)
    try:
        result = emb.encode(["a", "b", "c"])
    finally:
        emb._model = None

    norms = np.linalg.norm(result, axis=1)
    np.testing.assert_allclose(norms, np.ones(3), rtol=1e-5)


def test_encode_query_returns_1d_vector_of_384():
    """encode_query(text) returns ndarray of shape (384,), dtype float32."""
    import pdf_mcp.embedder as emb

    emb._model = _make_mock_model(_NATIVE_DIM)
    try:
        result = emb.encode_query("what is revenue?")
    finally:
        emb._model = None

    assert result.shape == (_TRUNC_DIM,)
    assert result.dtype == np.float32
    # Still unit-normalised.
    np.testing.assert_allclose(np.linalg.norm(result), 1.0, rtol=1e-5)


def test_encode_applies_document_prefix():
    """encode() prepends the 'search_document: ' prefix required by Nomic v1.5."""
    import pdf_mcp.embedder as emb

    emb._model = _make_mock_model(_NATIVE_DIM)
    received: list[str] = []

    def capture(texts):
        received.extend(texts)
        for _ in texts:
            yield np.zeros(_NATIVE_DIM, dtype=np.float32) + 0.1

    emb._model.embed.side_effect = capture
    try:
        emb.encode(["hello", "world"])
    finally:
        emb._model = None

    assert received == ["search_document: hello", "search_document: world"]


def test_encode_query_applies_query_prefix():
    """encode_query() prepends the 'search_query: ' prefix required by Nomic v1.5."""
    import pdf_mcp.embedder as emb

    emb._model = _make_mock_model(_NATIVE_DIM)
    received: list[str] = []

    def capture(texts):
        received.extend(texts)
        for _ in texts:
            yield np.zeros(_NATIVE_DIM, dtype=np.float32) + 0.1

    emb._model.embed.side_effect = capture
    try:
        emb.encode_query("find the budget table")
    finally:
        emb._model = None

    assert received == ["search_query: find the budget table"]


def test_encode_raises_when_fastembed_missing():
    """encode() raises ImportError with install hint when fastembed absent."""
    import pdf_mcp.embedder as emb

    emb._model = None  # force _get_model() to attempt import
    try:
        with patch.dict(sys.modules, {"fastembed": None}):
            with pytest.raises(
                ImportError, match="pdf-mcp-ocr-more-secure\\[semantic\\]"
            ):
                emb.encode(["hello"])
    finally:
        emb._model = None


def test_singleton_model_constructed_once():
    """TextEmbedding constructor is called only once across multiple encode() calls."""
    import pdf_mcp.embedder as emb

    emb._model = None  # force re-creation

    mock_instance = _make_mock_model(_NATIVE_DIM)
    mock_cls = MagicMock(return_value=mock_instance)
    mock_fastembed = MagicMock()
    mock_fastembed.TextEmbedding = mock_cls

    with patch.dict(sys.modules, {"fastembed": mock_fastembed}):
        try:
            emb.encode(["a"])
            emb.encode(["b"])
            emb.encode(["c"])
        finally:
            emb._model = None

    assert mock_cls.call_count == 1
    # Verify the model name passed is the Nomic one, not the BAAI default.
    assert mock_cls.call_args.args[0] == "nomic-ai/nomic-embed-text-v1.5"


def test_model_name_constant_points_at_nomic():
    """Canary: MODEL_NAME should be the Nomic model we've vetted."""
    import pdf_mcp.embedder as emb

    assert emb.MODEL_NAME == "nomic-ai/nomic-embed-text-v1.5"
    assert emb.EMBEDDING_DIM == 384
    assert emb.DOCUMENT_PREFIX == "search_document: "
    assert emb.QUERY_PREFIX == "search_query: "
