# tests/test_extractor_preprocessing.py
"""Unit tests for OCR preprocessing helpers (Otsu + deskew).

Uses synthetic numpy arrays — no Tesseract, no matplotlib. Verifies
the pure-Python algorithms directly so we can test them fast and
independently of real image rendering.
"""

from __future__ import annotations

import numpy as np
import pytest

from pdf_mcp.extractor import (
    _estimate_skew_degrees,
    _otsu_threshold,
    _preprocess_for_ocr,
)


class TestOtsuThreshold:
    def _separates(self, arr: np.ndarray, t: int) -> bool:
        """True if threshold t cleanly separates the input into two groups."""
        below = arr[arr <= t].size
        above = arr[arr > t].size
        return below > 0 and above > 0

    def test_pure_bimodal_image_separates_clusters(self) -> None:
        """Pure-black + pure-white → any t in [0, 254] is mathematically optimal.
        What matters: the returned t actually separates the two clusters."""
        arr = np.zeros((100, 100), dtype=np.uint8)
        arr[:50] = 0
        arr[50:] = 255
        t = _otsu_threshold(arr)
        assert self._separates(arr, t), f"t={t} doesn't separate 0 and 255"

    def test_dense_text_like_separates_ink_from_paper(self) -> None:
        """Text band at value=30, paper at value=240 → any t in [30, 239] works."""
        arr = np.full((200, 200), 240, dtype=np.uint8)
        arr[:30, :] = 30
        t = _otsu_threshold(arr)
        assert self._separates(arr, t), f"t={t} doesn't separate ink (30) from paper (240)"
        assert 30 <= t < 240

    def test_returns_int(self) -> None:
        arr = np.random.RandomState(42).randint(0, 256, size=(50, 50), dtype=np.uint8)
        t = _otsu_threshold(arr)
        assert isinstance(t, int)
        assert 0 <= t <= 255


class TestEstimateSkew:
    def _make_horizontal_lines_image(self, angle: float = 0.0) -> np.ndarray:
        """Make a 400x400 image with thick horizontal dark bands, optionally rotated."""
        from PIL import Image

        arr = np.full((400, 400), 255, dtype=np.uint8)
        # 5 dark rows ~80px apart — simulates text lines
        for y in range(40, 400, 80):
            arr[y:y + 5, 20:380] = 0

        if angle != 0.0:
            img = Image.fromarray(arr).rotate(
                angle, resample=Image.Resampling.BILINEAR, fillcolor=255
            )
            arr = np.asarray(img)
        return arr

    def test_detects_zero_skew_on_horizontal_text(self) -> None:
        arr = self._make_horizontal_lines_image(angle=0.0)
        skew = _estimate_skew_degrees(arr)
        assert abs(skew) < 0.75, f"expected near-zero skew; got {skew}"

    def test_detects_small_positive_skew(self) -> None:
        """Image rotated +2° → detector should find a correction around -2°."""
        arr = self._make_horizontal_lines_image(angle=2.0)
        skew = _estimate_skew_degrees(arr)
        # Correcting rotation has the opposite sign. We accept a range
        # because projection-profile is noisy on 400x400 and step is 0.5°.
        assert -3.5 < skew < -0.5, f"expected ~-2° correction; got {skew}"

    def test_detects_small_negative_skew(self) -> None:
        arr = self._make_horizontal_lines_image(angle=-2.0)
        skew = _estimate_skew_degrees(arr)
        assert 0.5 < skew < 3.5, f"expected ~+2° correction; got {skew}"


class TestPreprocessForOcr:
    def test_returns_pil_image(self) -> None:
        from PIL import Image

        arr = np.full((200, 200), 250, dtype=np.uint8)
        arr[80:120, 20:180] = 20  # dark band
        img = Image.fromarray(arr)
        out = _preprocess_for_ocr(img)
        assert out.mode == "L"  # grayscale

    def test_no_skew_returns_near_identical_size(self) -> None:
        from PIL import Image

        arr = np.full((300, 300), 250, dtype=np.uint8)
        arr[:20] = 10  # horizontal dark band → already aligned
        img = Image.fromarray(arr)
        out = _preprocess_for_ocr(img)
        assert out.size == img.size  # no rotation ⇒ same dims

    def test_skewed_input_gets_rotated(self) -> None:
        """A rotated image should trigger correction, changing the output dims."""
        from PIL import Image

        # Dark horizontal bands on white.
        arr = np.full((400, 400), 255, dtype=np.uint8)
        for y in range(40, 400, 80):
            arr[y:y + 5, :] = 0
        # Rotate by 3° — above the 0.25° threshold where preprocess applies.
        skewed = Image.fromarray(arr).rotate(
            3.0, resample=Image.Resampling.BILINEAR, fillcolor=255
        )

        out = _preprocess_for_ocr(skewed)
        # rotate(expand=True) grows canvas; size should change if correction applied.
        assert out.size != skewed.size
