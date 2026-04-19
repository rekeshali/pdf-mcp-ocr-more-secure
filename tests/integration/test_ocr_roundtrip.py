"""End-to-end OCR round-trip test with real Tesseract.

Generates a synthetic image-based PDF (text rendered via matplotlib,
rasterised, then embedded as a single page image into a PDF) and runs
the real `extract_text_with_ocr_fallback` against it. This verifies
the full pipeline — PyMuPDF rendering, CMYK/RGB handling, PIL hand-off,
pytesseract invocation, Tesseract binary, result threshold/confidence
— rather than just the mocked decision logic covered in
`test_extractor_ocr.py`.

Skipped automatically if:
- `pytesseract` isn't installed (via the `[ocr]` extra)
- the `tesseract` system binary isn't on PATH
- `matplotlib` isn't installed (dev extra only)

Local: `uv run pytest tests/integration/` after `uv sync --extra dev --extra ocr`.
CI: the `pdf-mcp-ocr-ci.yml` workflow installs both extras + `tesseract-ocr`.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest

pymupdf = pytest.importorskip("pymupdf", reason="pymupdf required for PDF synthesis")
pytesseract = pytest.importorskip("pytesseract", reason="install the [ocr] extra")
plt = pytest.importorskip(
    "matplotlib.pyplot", reason="install the [dev] extra for integration fixtures"
)
pytestmark = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract system binary not installed (brew install tesseract / apt install tesseract-ocr)",
)


def _render_text_as_pdf(text_lines: list[str], out_path: Path, dpi: int = 200) -> None:
    """Render `text_lines` via matplotlib, rasterise, embed as image in a PDF.

    The result is a one-page PDF whose content is *only* a raster image — no
    embedded text layer, mimicking a scanned document. The OCR fallback
    should detect that the text layer is empty and run Tesseract on the
    rendered page.
    """
    # 1. Draw the text to a PNG via matplotlib (guaranteed rasterised output).
    fig = plt.figure(figsize=(8.5, 11), dpi=dpi)
    ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])
    ax.axis("off")
    body = "\n".join(text_lines)
    ax.text(
        0.05,
        0.95,
        body,
        fontsize=18,
        verticalalignment="top",
        fontfamily="monospace",
    )
    png_buffer = io.BytesIO()
    fig.savefig(png_buffer, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    png_buffer.seek(0)

    # 2. Create a PDF and embed the PNG as the full page image.
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)  # US letter, 72dpi points
    rect = pymupdf.Rect(0, 0, 612, 792)
    page.insert_image(rect, stream=png_buffer.read())
    doc.save(str(out_path))
    doc.close()


def test_ocr_recovers_text_from_rasterised_pdf(tmp_path: Path) -> None:
    """Full round-trip: synth a rasterised PDF, OCR it, assert text round-trips."""
    from pdf_mcp.extractor import extract_text_with_ocr_fallback

    expected_lines = [
        "Hello, world.",
        "This is a synthetic scanned document.",
        "The extractor should fall back to OCR",
        "because the text layer is empty.",
    ]
    pdf_path = tmp_path / "synthetic-scan.pdf"
    _render_text_as_pdf(expected_lines, pdf_path)

    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[0]

        # Sanity-check: the embedded-image PDF really has no text layer.
        pre_ocr_text = page.get_text("text")
        assert pre_ocr_text.strip() == "", (
            f"Test fixture failed — rasterised PDF unexpectedly has an "
            f"embedded text layer: {pre_ocr_text!r}"
        )

        text, info = extract_text_with_ocr_fallback(page)
    finally:
        doc.close()

    assert info["ocr_used"] is True
    assert info["ocr_error"] is None
    assert info["ocr_confidence"] in ("high", "low")

    # Tesseract output won't be a perfect character-for-character match
    # (kerning, punctuation, font rendering all introduce noise). Assert
    # on a few clean, distinctive words the OCR should consistently recover.
    low = text.lower()
    for needle in ("hello", "world", "synthetic", "document", "text layer"):
        assert needle in low, (
            f"Expected {needle!r} in OCR output; got:\n{text!r}"
        )


def test_force_ocr_roundtrip_on_text_based_pdf(tmp_path: Path) -> None:
    """`force_ocr=True` should OCR even a PDF that has a real text layer."""
    from pdf_mcp.extractor import extract_text_with_ocr_fallback

    # Generate a PDF with a real text layer (not rasterised). Using words
    # that Tesseract reliably round-trips at this font size — short 3- or 4-
    # letter tokens can confuse the OCR, so we stick to distinctive everyday
    # words and phrases.
    distinctive_text = "The quick brown fox jumps over the lazy dog"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), distinctive_text, fontsize=24)
    pdf_path = tmp_path / "text-layer.pdf"
    doc.save(str(pdf_path))
    doc.close()

    doc = pymupdf.open(str(pdf_path))
    try:
        page = doc[0]

        # Sanity check: this PDF *does* have a text layer.
        pre_ocr_text = page.get_text("text").strip()
        assert "quick" in pre_ocr_text.lower()

        text, info = extract_text_with_ocr_fallback(page, force_ocr=True)
    finally:
        doc.close()

    assert info["ocr_used"] is True
    assert info["ocr_error"] is None
    # Assert multiple distinctive words survive OCR so one bad character
    # doesn't flake the whole test.
    low = text.lower()
    recovered = sum(w in low for w in ("quick", "brown", "jumps", "lazy"))
    assert recovered >= 3, (
        f"Expected ≥3 of 4 distinctive words to round-trip through OCR. "
        f"Recovered: {recovered}. Output: {text!r}"
    )
