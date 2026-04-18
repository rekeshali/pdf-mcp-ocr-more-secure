# tests/test_extractor_ocr.py
"""Tests for OCR fallback logic in extract_text_with_ocr_fallback."""

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from pdf_mcp.extractor import extract_text_with_ocr_fallback


def _make_page(text: str, pixmap: MagicMock | None = None) -> MagicMock:
    """Build a MagicMock that quacks like a PyMuPDF Page."""
    page = MagicMock()
    # extract_text_from_page reads via get_text('blocks', sort=True).
    # Return a single text block with block_type=0.
    page.get_text = MagicMock(
        side_effect=lambda *args, **kwargs: [(0, 0, 1, 1, text, 0, 0)]
        if args and args[0] == "blocks"
        else text
    )
    if pixmap is not None:
        page.get_pixmap = MagicMock(return_value=pixmap)
    return page


def _make_pixmap(cmyk: bool = False) -> MagicMock:
    """Build a MagicMock PyMuPDF Pixmap suitable for OCR rendering."""
    pix = MagicMock()
    pix.n = 5 if cmyk else 3  # CMYK has n>=4 (with alpha accounted for)
    pix.alpha = 0
    pix.tobytes = MagicMock(return_value=b"fake-png-bytes")
    return pix


class TestOCRFallbackBehavior:
    """Decisions about when OCR fires vs is skipped."""

    def test_skips_ocr_when_text_layer_is_substantial(self) -> None:
        page = _make_page("x" * 500)
        text, info = extract_text_with_ocr_fallback(page)
        assert info["ocr_used"] is False
        assert info["ocr_confidence"] is None
        assert info["ocr_error"] is None
        assert len(text) >= 500

    def test_fires_ocr_when_text_is_sparse(self) -> None:
        fake_pytesseract = ModuleType("pytesseract")
        fake_pytesseract.image_to_string = lambda img, lang, config: "recovered via OCR " * 10  # type: ignore[attr-defined]
        fake_pil = ModuleType("PIL")
        fake_pil_image = ModuleType("PIL.Image")
        fake_pil_image.open = lambda buf: MagicMock()  # type: ignore[attr-defined]
        fake_pil.Image = fake_pil_image  # type: ignore[attr-defined]

        pix = _make_pixmap()
        page = _make_page("", pixmap=pix)

        with patch.dict(
            sys.modules,
            {
                "pytesseract": fake_pytesseract,
                "PIL": fake_pil,
                "PIL.Image": fake_pil_image,
            },
        ):
            text, info = extract_text_with_ocr_fallback(page)

        assert info["ocr_used"] is True
        assert "recovered via OCR" in text
        assert info["ocr_confidence"] == "high"  # >10 tokens

    def test_skip_ocr_flag_prevents_fallback_on_empty_pages(self) -> None:
        page = _make_page("")
        text, info = extract_text_with_ocr_fallback(page, skip_ocr=True)
        assert info["ocr_used"] is False
        assert text == ""  # empty text layer; no fallback

    def test_force_ocr_bypasses_text_layer_and_runs_ocr(self) -> None:
        fake_pytesseract = ModuleType("pytesseract")
        fake_pytesseract.image_to_string = lambda img, lang, config: "forced"  # type: ignore[attr-defined]
        fake_pil = ModuleType("PIL")
        fake_pil_image = ModuleType("PIL.Image")
        fake_pil_image.open = lambda buf: MagicMock()  # type: ignore[attr-defined]
        fake_pil.Image = fake_pil_image  # type: ignore[attr-defined]

        pix = _make_pixmap()
        page = _make_page("lots of real text " * 100, pixmap=pix)

        with patch.dict(
            sys.modules,
            {
                "pytesseract": fake_pytesseract,
                "PIL": fake_pil,
                "PIL.Image": fake_pil_image,
            },
        ):
            text, info = extract_text_with_ocr_fallback(page, force_ocr=True)

        # force_ocr: page.get_text should NOT be called for text extraction
        # (it was only called inside get_pixmap rendering, if at all).
        assert info["ocr_used"] is True
        assert text == "forced"


class TestOCRLowConfidence:
    def test_low_confidence_when_ocr_produces_few_words(self) -> None:
        fake_pytesseract = ModuleType("pytesseract")
        fake_pytesseract.image_to_string = lambda img, lang, config: "a b c"  # type: ignore[attr-defined]
        fake_pil = ModuleType("PIL")
        fake_pil_image = ModuleType("PIL.Image")
        fake_pil_image.open = lambda buf: MagicMock()  # type: ignore[attr-defined]
        fake_pil.Image = fake_pil_image  # type: ignore[attr-defined]

        pix = _make_pixmap()
        page = _make_page("", pixmap=pix)

        with patch.dict(
            sys.modules,
            {
                "pytesseract": fake_pytesseract,
                "PIL": fake_pil,
                "PIL.Image": fake_pil_image,
            },
        ):
            text, info = extract_text_with_ocr_fallback(page)

        assert info["ocr_used"] is True
        assert info["ocr_confidence"] == "low"


class TestOCRMissingDependencies:
    def test_missing_pytesseract_sets_error_and_returns_original(self) -> None:
        page = _make_page("")
        # Pretend pytesseract is uninstallable by making import raise.
        with patch.dict(sys.modules, {"pytesseract": None}):
            text, info = extract_text_with_ocr_fallback(page)

        assert info["ocr_used"] is False
        assert info["ocr_error"] is not None
        assert "dependencies missing" in info["ocr_error"]
        # Text should be whatever get_text produced (empty here).
        assert text == ""

    def test_missing_pytesseract_raises_when_force_ocr(self) -> None:
        page = _make_page("irrelevant")
        with patch.dict(sys.modules, {"pytesseract": None}):
            with pytest.raises(RuntimeError, match="dependencies missing"):
                extract_text_with_ocr_fallback(page, force_ocr=True)


class TestOCRLanguageParameter:
    def test_language_code_passed_through_to_tesseract(self) -> None:
        captured: dict[str, str] = {}

        def capture(img: object, lang: str, config: str) -> str:
            captured["lang"] = lang
            captured["config"] = config
            return "ok"

        fake_pytesseract = ModuleType("pytesseract")
        fake_pytesseract.image_to_string = capture  # type: ignore[attr-defined]
        fake_pil = ModuleType("PIL")
        fake_pil_image = ModuleType("PIL.Image")
        fake_pil_image.open = lambda buf: MagicMock()  # type: ignore[attr-defined]
        fake_pil.Image = fake_pil_image  # type: ignore[attr-defined]

        pix = _make_pixmap()
        page = _make_page("", pixmap=pix)

        with patch.dict(
            sys.modules,
            {
                "pytesseract": fake_pytesseract,
                "PIL": fake_pil,
                "PIL.Image": fake_pil_image,
            },
        ):
            extract_text_with_ocr_fallback(page, ocr_language="eng+fra")

        assert captured["lang"] == "eng+fra"
        assert captured["config"] == "--psm 6"
