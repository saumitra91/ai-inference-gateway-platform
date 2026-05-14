from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class PdfParseError(Exception):
    pass


class PdfPage:
    def __init__(self, text: str, page_number: int) -> None:
        self.text = text
        self.page_number = page_number


class ParsedDocument:
    def __init__(self, pages: list[PdfPage], metadata: dict[str, object]) -> None:
        self.pages = pages
        self.metadata = metadata


def extract_text(file_path: str | Path) -> ParsedDocument:
    file_path = Path(file_path)
    if not file_path.exists():
        raise PdfParseError(f"File not found: {file_path}")

    try:
        import fitz
    except ImportError as exc:
        raise PdfParseError("PyMuPDF (fitz) is not installed") from exc

    try:
        doc = fitz.open(str(file_path))
    except Exception as exc:
        raise PdfParseError(f"Failed to open PDF: {exc}") from exc

    pages: list[PdfPage] = []
    metadata: dict[str, object] = {
        "filename": file_path.name,
        "file_size_bytes": file_path.stat().st_size,
        "page_count": len(doc),
    }

    for page_num in range(len(doc)):
        try:
            page = doc.load_page(page_num)
            text = page.get_text("text")
            pages.append(PdfPage(text=text, page_number=page_num + 1))
        except Exception as exc:
            logger.warning("Failed to extract text from page %d: %s", page_num + 1, exc)
            pages.append(PdfPage(text="", page_number=page_num + 1))

    doc.close()

    if not pages:
        raise PdfParseError("PDF has no pages")

    total_chars = sum(len(p.text) for p in pages)
    metadata["total_chars"] = total_chars
    logger.info("Extracted %d pages, %d chars from %s", len(pages), total_chars, file_path.name)

    return ParsedDocument(pages=pages, metadata=metadata)
