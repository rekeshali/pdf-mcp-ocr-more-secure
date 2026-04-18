"""
PDF extraction utilities using PyMuPDF.
"""

import logging
import os
import re
import sys
import typing
import warnings
from pathlib import Path
from typing import Any

# Suppress PyMuPDF/SWIG DeprecationWarnings (upstream issue, not actionable).
# Python-level filter handles import-time warnings.
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    message="builtin type.*[Ss]wig.*has no __module__ attribute",
)


# C-level SWIG warnings emitted during interpreter shutdown bypass Python's
# warning filters and write directly to stderr. Wrap stderr to catch those.
class _StderrSwigFilter:
    __slots__ = ("_stream",)

    def __init__(self, stream: typing.TextIO) -> None:
        self._stream = stream

    def write(self, msg: str) -> int:
        if "DeprecationWarning" in msg and "swig" in msg.lower():
            return len(msg)
        return self._stream.write(msg)

    def __getattr__(self, name: str) -> object:
        return getattr(self._stream, name)


sys.stderr = _StderrSwigFilter(sys.stderr)  # type: ignore[assignment]

import pymupdf  # noqa: E402

logger = logging.getLogger(__name__)


def parse_page_range(pages: str | list[int] | None, total_pages: int) -> list[int]:
    """
    Parse page specification into list of 0-indexed page numbers.

    Args:
        pages: Page specification:
            - None: all pages
            - list[int]: explicit page numbers (1-indexed)
            - str: range like "1-5,10,15-20" (1-indexed)
        total_pages: Total number of pages in document

    Returns:
        List of 0-indexed page numbers

    Examples:
        >>> parse_page_range(None, 10)
        [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
        >>> parse_page_range([1, 5, 10], 10)
        [0, 4, 9]
        >>> parse_page_range("1-3,5,8-10", 10)
        [0, 1, 2, 4, 7, 8, 9]
    """
    if pages is None:
        return list(range(total_pages))

    if isinstance(pages, list):
        # Convert 1-indexed to 0-indexed
        return [p - 1 for p in pages if 1 <= p <= total_pages]

    # Parse string format like "1-5,10,15-20"
    result = []
    parts = re.split(r"[,\s]+", pages.strip())

    for part in parts:
        part = part.strip()
        if not part:
            continue

        if "-" in part:
            # Range: "1-5" or "10-20"
            match = re.match(r"(\d+)\s*-\s*(\d+)", part)
            if match:
                start, end = int(match.group(1)), int(match.group(2))
                # Convert to 0-indexed and clamp to valid range
                for p in range(start - 1, end):
                    if 0 <= p < total_pages:
                        result.append(p)
        else:
            # Single page: "5"
            try:
                p = int(part) - 1  # Convert to 0-indexed
                if 0 <= p < total_pages:
                    result.append(p)
            except ValueError:
                continue

    # Remove duplicates while preserving order
    seen = set()
    unique_result = []
    for p in result:
        if p not in seen:
            seen.add(p)
            unique_result.append(p)

    return unique_result


def extract_text_from_page(page: Any, sort_by_position: bool = True) -> str:
    """
    Extract text from a PDF page.

    Args:
        page: PyMuPDF page object
        sort_by_position: If True, sort text blocks by Y-coordinate for reading order

    Returns:
        Extracted text content
    """
    if sort_by_position:
        # Get text with position information
        blocks = page.get_text("blocks", sort=True)

        # blocks format: (x0, y0, x1, y1, "text", block_no, block_type)
        # block_type: 0 = text, 1 = image
        text_blocks = [block[4] for block in blocks if block[6] == 0]

        return "\n\n".join(text_blocks)
    else:
        return str(page.get_text())


def extract_text_with_ocr_fallback(
    page: Any,
    *,
    ocr_language: str = "eng",
    sparse_threshold: int = 50,
    force_ocr: bool = False,
    skip_ocr: bool = False,
    sort_by_position: bool = True,
    dpi: int = 300,
) -> tuple[str, dict[str, Any]]:
    """Extract page text, falling back to OCR when the embedded text layer is sparse.

    Borrows the CMYK->RGB conversion, in-memory PIL handling, and per-page
    warn-and-continue pattern from labeveryday/mcp_pdf_reader; diverges by
    rendering the whole page at dpi (default 300) rather than iterating embedded
    images — scanned PDFs typically encode one big embedded image per page, but
    the page-render strategy also works when the scan is the rasterised
    background behind invisible (empty) text objects.

    Args:
        page: PyMuPDF page object.
        ocr_language: Tesseract lang code (e.g. "eng", "eng+fra"). Requires the
            matching language pack installed on the Tesseract binary.
        sparse_threshold: If the extracted text has fewer than this many
            non-whitespace characters, OCR fallback fires. Set higher for PDFs
            where even a small amount of real text is trustworthy.
        force_ocr: Skip text-layer extraction; OCR the page directly.
        skip_ocr: Never OCR, even if the text layer is empty. Returns whatever
            get_text() produced.
        sort_by_position: Passed to extract_text_from_page.
        dpi: Rendering DPI for OCR. 300 is a reasonable default for scanned
            documents; higher gives better accuracy at quadratic time cost.

    Returns:
        Tuple of (text, info) where info is:
            {
                "ocr_used": bool,
                "ocr_confidence": "high" | "low" | None,
                "ocr_error": str | None,
            }
        confidence is "high" if OCR produced >10 whitespace-separated tokens,
        "low" otherwise (borrowed from labeveryday). None when OCR wasn't run.
    """
    info: dict[str, Any] = {
        "ocr_used": False,
        "ocr_confidence": None,
        "ocr_error": None,
    }

    text = "" if force_ocr else extract_text_from_page(page, sort_by_position=sort_by_position)
    stripped_len = len(text.strip())

    should_ocr = force_ocr or (not skip_ocr and stripped_len < sparse_threshold)
    if not should_ocr:
        return text, info

    try:
        # Import lazily so the [ocr] extra is truly optional.
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        info["ocr_error"] = (
            f"OCR requested but dependencies missing: {exc}. "
            f"Install with: pip install 'pdf-mcp-ocr-more-secure[ocr]' "
            f"(also requires the tesseract system binary)."
        )
        logger.warning(info["ocr_error"])
        if force_ocr:
            raise RuntimeError(info["ocr_error"]) from exc
        return text, info

    try:
        pix = page.get_pixmap(dpi=dpi)

        # CMYK -> RGB before PNG encode (borrowed from labeveryday).
        if pix.n - pix.alpha < 4:
            img_bytes = pix.tobytes("png")
        else:
            img_bytes = pymupdf.Pixmap(pymupdf.csRGB, pix).tobytes("png")

        from io import BytesIO

        img = Image.open(BytesIO(img_bytes))
        ocr_text = pytesseract.image_to_string(
            img,
            lang=ocr_language,
            config="--psm 6",  # Uniform block of text — sensible default for whole-page scans
        )

        info["ocr_used"] = True
        word_count = len(ocr_text.split())
        info["ocr_confidence"] = "high" if word_count > 10 else "low"
        return ocr_text, info

    except Exception as exc:  # pragma: no cover — tesseract binary missing / render failure
        info["ocr_error"] = f"OCR failed: {type(exc).__name__}: {exc}"
        logger.warning(info["ocr_error"])
        if force_ocr:
            raise
        return text, info


def extract_text_with_coordinates(page: Any) -> list[dict[str, Any]]:
    """
    Extract text with Y-coordinate information for content ordering.

    Args:
        page: PyMuPDF page object

    Returns:
        List of content blocks with type, text, and position
    """
    blocks = page.get_text("dict")["blocks"]

    content = []
    for block in blocks:
        if block["type"] == 0:  # Text block
            # Extract text from spans
            text_parts = []
            for line in block["lines"]:
                line_text = ""
                for span in line["spans"]:
                    line_text += span["text"]
                text_parts.append(line_text)

            text = "\n".join(text_parts)
            if text.strip():
                content.append(
                    {
                        "type": "text",
                        "text": text,
                        "y": block["bbox"][1],  # Top Y coordinate
                        "bbox": block["bbox"],
                    }
                )
        elif block["type"] == 1:  # Image block
            content.append(
                {
                    "type": "image_placeholder",
                    "y": block["bbox"][1],
                    "bbox": block["bbox"],
                }
            )

    # Sort by Y coordinate for natural reading order
    content.sort(key=lambda x: x["y"])

    return content


def extract_images_from_page(
    doc: pymupdf.Document,
    page_num: int,
    output_dir: Path | None = None,
    pdf_hash: str = "",
) -> list[dict[str, Any]]:
    """
    Extract images from a PDF page as PNG files saved to disk.

    Args:
        doc: PyMuPDF document object
        page_num: Page number (0-indexed)
        output_dir: Directory to save PNG files
        pdf_hash: Hash prefix for deterministic filenames

    Returns:
        List of image dicts with width, height, format, path, size_bytes
    """
    page = doc[page_num]
    images = []

    image_list = page.get_images(full=True)

    for img_index, img_info in enumerate(image_list):
        xref = img_info[0]

        try:
            # Extract image as Pixmap
            pix = pymupdf.Pixmap(doc, xref)

            # Handle CMYK images
            if pix.n - pix.alpha > 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)

            # Determine color format
            if pix.n == 1:
                color_format = "grayscale"
            elif pix.n == 3:
                color_format = "rgb"
            elif pix.n == 4:
                color_format = "rgba"
            else:
                color_format = "unknown"

            # Save to disk
            assert output_dir is not None
            file_name = f"{pdf_hash}_p{page_num}_i{img_index}.png"
            file_path = output_dir / file_name
            try:
                pix.save(str(file_path))
                os.chmod(str(file_path), 0o600)
            except Exception as e:
                try:
                    file_path.unlink(missing_ok=True)
                except Exception:
                    pass
                logger.warning(
                    "Failed to save image %d from page %d: %s",
                    img_index,
                    page_num,
                    e,
                )
                continue

            images.append(
                {
                    "page": page_num + 1,  # 1-indexed for output
                    "index": img_index,
                    "width": pix.width,
                    "height": pix.height,
                    "format": color_format,
                    "path": str(file_path),
                    "size_bytes": file_path.stat().st_size,
                }
            )

        except (ValueError, RuntimeError, KeyError) as e:
            # Skip problematic images but log the issue
            logger.warning(
                "Failed to extract image %d from page %d: %s", img_index, page_num, e
            )
            continue

    return images


def extract_tables_from_page(page: Any) -> list[dict[str, Any]]:
    """
    Extract tables from a PDF page using PyMuPDF's table finder.

    Requires visible line borders to detect table structure.
    Pages without detectable tables return an empty list.

    Args:
        page: PyMuPDF page object

    Returns:
        List of table dicts, each with:
        - index: 0-based table index on this page
        - bbox: [x0, y0, x1, y1] bounding box
        - row_count: total rows including header (equals 1 + len(rows))
        - col_count: number of columns
        - header: list of header cell strings (first row)
        - rows: list of data rows (excludes header); each row is a list of cell strings
    """
    tables = []
    try:
        found = page.find_tables()
        for i, table in enumerate(found.tables):
            extracted = table.extract()
            if not extracted:
                continue
            header = [str(cell) if cell is not None else "" for cell in extracted[0]]
            rows = [
                [str(cell) if cell is not None else "" for cell in row]
                for row in extracted[1:]
            ]
            tables.append(
                {
                    "index": i,
                    "bbox": list(table.bbox),
                    "row_count": len(extracted),
                    "col_count": len(extracted[0]),
                    "header": header,
                    "rows": rows,
                }
            )
    except Exception as e:
        logger.warning("Failed to extract tables from page: %s", e)
    return tables


def extract_metadata(doc: pymupdf.Document) -> dict[str, Any]:
    """
    Extract metadata from PDF document.

    Args:
        doc: PyMuPDF document object

    Returns:
        Metadata dict with author, title, subject, etc.
    """
    meta = doc.metadata or {}

    return {
        "title": meta.get("title", ""),
        "author": meta.get("author", ""),
        "subject": meta.get("subject", ""),
        "keywords": meta.get("keywords", ""),
        "creator": meta.get("creator", ""),
        "producer": meta.get("producer", ""),
        "creation_date": meta.get("creationDate", ""),
        "modification_date": meta.get("modDate", ""),
        "format": meta.get("format", ""),
        "encryption": meta.get("encryption", ""),
    }


def extract_toc(doc: pymupdf.Document) -> list[dict[str, Any]]:
    """
    Extract table of contents from PDF document.

    Args:
        doc: PyMuPDF document object

    Returns:
        List of TOC entries with level, title, page
    """
    toc = doc.get_toc()

    return [
        {
            "level": entry[0],
            "title": entry[1],
            "page": entry[2],
        }
        for entry in toc
    ]


def estimate_tokens(text: str) -> int:
    """
    Estimate token count for text (rough approximation).

    Uses ~4 characters per token as rough estimate.

    Args:
        text: Input text

    Returns:
        Estimated token count
    """
    return len(text) // 4


def chunk_text(
    text: str, max_tokens: int = 4000, overlap_tokens: int = 200
) -> list[dict[str, Any]]:
    """
    Split text into chunks with overlap.

    Args:
        text: Input text
        max_tokens: Maximum tokens per chunk
        overlap_tokens: Overlap tokens between chunks

    Returns:
        List of chunk dicts with text, start_char, end_char, estimated_tokens
    """
    max_chars = max_tokens * 4
    overlap_chars = overlap_tokens * 4

    chunks = []
    start = 0
    chunk_index = 0

    while start < len(text):
        end = min(start + max_chars, len(text))

        # Try to break at sentence boundary
        if end < len(text):
            # Look for sentence end (.!?) followed by space or newline
            search_start = max(start + max_chars - 500, start)
            last_sentence = -1

            for i in range(end - 1, search_start, -1):
                if text[i] in ".!?" and (i + 1 >= len(text) or text[i + 1] in " \n\t"):
                    last_sentence = i + 1
                    break

            if last_sentence > start:
                end = last_sentence

        chunk_text = text[start:end]

        chunks.append(
            {
                "chunk_index": chunk_index,
                "text": chunk_text,
                "start_char": start,
                "end_char": end,
                "estimated_tokens": estimate_tokens(chunk_text),
            }
        )

        chunk_index += 1
        start = end - overlap_chars if end < len(text) else end

    return chunks
