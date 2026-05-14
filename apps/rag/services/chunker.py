from __future__ import annotations

import logging
import re

from django.conf import settings

from .pdf_parser import PdfPage

logger = logging.getLogger(__name__)


class Chunk:
    def __init__(self, text: str, page_number: int, chunk_index: int, document_id: str) -> None:
        self.text = text
        self.page_number = page_number
        self.chunk_index = chunk_index
        self.document_id = document_id
        self.id = f"{document_id}_chunk_{chunk_index}"

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "text": self.text,
            "page_number": self.page_number,
            "chunk_index": self.chunk_index,
            "document_id": self.document_id,
        }


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if p.strip()]


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def chunk_document(
    text: str,
    document_id: str,
    page_number: int = 1,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Chunk]:
    chunk_size = chunk_size or settings.RAG_CHUNK_SIZE
    chunk_overlap = chunk_overlap or settings.RAG_CHUNK_OVERLAP

    paragraphs = _split_paragraphs(text)
    chunks: list[Chunk] = []
    chunk_index = 0

    current_chunk = ""

    for para in paragraphs:
        if not para:
            continue

        if len(current_chunk) + len(para) + 1 <= chunk_size:
            if current_chunk:
                current_chunk += "\n\n" + para
            else:
                current_chunk = para
            continue

        if current_chunk:
            chunks.append(Chunk(text=current_chunk.strip(), page_number=page_number, chunk_index=chunk_index, document_id=document_id))
            chunk_index += 1

            overlap_text = _compute_overlap(current_chunk, chunk_overlap)
            current_chunk = overlap_text + "\n\n" + para if overlap_text else para
        else:
            sentences = _split_sentences(para)
            for sent in sentences:
                if len(current_chunk) + len(sent) + 1 <= chunk_size:
                    if current_chunk:
                        current_chunk += " " + sent
                    else:
                        current_chunk = sent
                else:
                    if current_chunk:
                        chunks.append(Chunk(text=current_chunk.strip(), page_number=page_number, chunk_index=chunk_index, document_id=document_id))
                        chunk_index += 1
                    current_chunk = sent

    if current_chunk:
        chunks.append(Chunk(text=current_chunk.strip(), page_number=page_number, chunk_index=chunk_index, document_id=document_id))

    logger.info("Chunked page %d into %d chunks", page_number, len(chunks))
    return chunks


def _compute_overlap(text: str, overlap_chars: int) -> str:
    if overlap_chars <= 0 or not text:
        return ""
    sentences = _split_sentences(text)
    overlap = ""
    for sent in reversed(sentences):
        candidate = sent + " " + overlap if overlap else sent
        if len(candidate) > overlap_chars:
            break
        overlap = candidate
    return overlap.strip()


def chunk_pages(
    pages: list[PdfPage],
    document_id: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[Chunk]:
    all_chunks: list[Chunk] = []
    for page in pages:
        page_chunks = chunk_document(
            text=page.text,
            document_id=document_id,
            page_number=page.page_number,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        all_chunks.extend(page_chunks)

    for i, chunk in enumerate(all_chunks):
        chunk.chunk_index = i
        chunk.id = f"{chunk.document_id}_chunk_{i}"

    logger.info("Total: %d chunks across %d pages", len(all_chunks), len(pages))
    return all_chunks
