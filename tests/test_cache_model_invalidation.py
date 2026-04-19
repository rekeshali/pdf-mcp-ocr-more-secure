"""Tests for the model-name-scoped embeddings cache.

Covers the invariant that ``save_page_embeddings`` tags each row with the
current ``embedder.MODEL_NAME`` and ``get_page_embeddings`` filters on the
current model — so a swap from one embedding model to another automatically
invalidates stale cached vectors without needing a manual purge.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pdf_mcp.cache import PDFCache


@pytest.fixture
def cache_and_pdf(tmp_path: Path) -> tuple[PDFCache, str]:
    """Return (cache instance, fake-pdf path)."""
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%dummy for mtime\n")
    cache = PDFCache(cache_dir=tmp_path / "cache", ttl_hours=24)
    # Seed metadata so the mtime validity check passes.
    cache.save_metadata(str(pdf), page_count=5, metadata={}, toc=[])
    return cache, str(pdf)


def test_save_and_get_roundtrip_with_current_model(
    cache_and_pdf: tuple[PDFCache, str],
) -> None:
    cache, pdf = cache_and_pdf
    payload = {0: b"\x01" * 1536, 1: b"\x02" * 1536}
    cache.save_page_embeddings(pdf, payload)
    got = cache.get_page_embeddings(pdf, [0, 1, 2])
    assert got == payload  # page 2 wasn't saved, so it's absent


def test_get_filters_out_stale_model_rows(
    cache_and_pdf: tuple[PDFCache, str],
) -> None:
    """Rows written under a different MODEL_NAME must not surface on get."""
    cache, pdf = cache_and_pdf

    # Write rows under a fake "old" model.
    with patch("pdf_mcp.embedder.MODEL_NAME", "old/bge-small-en-v1.5"):
        cache.save_page_embeddings(pdf, {0: b"\xaa" * 1536})

    # Default MODEL_NAME is the Nomic model now; get should return nothing.
    got = cache.get_page_embeddings(pdf, [0, 1])
    assert got == {}


def test_save_with_new_model_does_not_overwrite_old_rows(
    cache_and_pdf: tuple[PDFCache, str],
) -> None:
    """Different model_name rows coexist; they're keyed as separate entries.

    This documents that the cache doesn't force purge of stale rows — it
    just ignores them on read. Disk growth is bounded (primary key includes
    model_name), so two models' rows can sit side-by-side until the next
    schema migration or manual clear.
    """
    cache, pdf = cache_and_pdf

    with patch("pdf_mcp.embedder.MODEL_NAME", "old/bge-small-en-v1.5"):
        cache.save_page_embeddings(pdf, {0: b"\xaa" * 1536})
    with patch("pdf_mcp.embedder.MODEL_NAME", "nomic-ai/nomic-embed-text-v1.5"):
        cache.save_page_embeddings(pdf, {0: b"\xbb" * 1536})

    # Read with the "new" model active — should see the new bytes, not the old.
    with patch("pdf_mcp.embedder.MODEL_NAME", "nomic-ai/nomic-embed-text-v1.5"):
        got = cache.get_page_embeddings(pdf, [0])
    assert got == {0: b"\xbb" * 1536}

    # Read with the "old" model active — should see the old bytes.
    with patch("pdf_mcp.embedder.MODEL_NAME", "old/bge-small-en-v1.5"):
        got_old = cache.get_page_embeddings(pdf, [0])
    assert got_old == {0: b"\xaa" * 1536}


def test_model_name_column_present_in_schema(
    cache_and_pdf: tuple[PDFCache, str],
) -> None:
    """Canary: the schema actually has the model_name column (migration sanity)."""
    import sqlite3

    cache, _ = cache_and_pdf
    with sqlite3.connect(cache.db_path) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(page_embeddings)")}
    assert "model_name" in cols
