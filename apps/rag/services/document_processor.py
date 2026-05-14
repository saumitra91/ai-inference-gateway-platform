from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from django.conf import settings

from apps.rag.metrics import (
    rag_chunks_stored_total,
    rag_documents_uploaded_total,
    rag_documents_ready,
    rag_ingestion_duration,
)
from apps.rag.models import Document

from .chunker import chunk_pages
from .pdf_parser import extract_text
from .vector_store import delete_document_chunks, store_chunks

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)


async def process_document(document_id: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_executor, _sync_process, document_id)


def _sync_process(document_id: str) -> None:
    start = time.monotonic()
    rag_documents_uploaded_total.inc()

    try:
        doc = Document.objects.get(id=document_id)
    except Document.DoesNotExist:
        logger.error("Document %s not found", document_id)
        return

    try:
        doc.status = Document.Status.PROCESSING
        doc.save(update_fields=["status"])

        parsed = extract_text(doc.file_path)

        full_text = "\n\n".join(p.text for p in parsed.pages)
        if not full_text.strip():
            raise ValueError("Extracted text is empty")

        doc.page_count = len(parsed.pages)
        doc.save(update_fields=["page_count"])

        delete_document_chunks(str(doc.id))

        chunks = chunk_pages(
            pages=parsed.pages,
            document_id=str(doc.id),
            chunk_size=settings.RAG_CHUNK_SIZE,
            chunk_overlap=settings.RAG_CHUNK_OVERLAP,
        )

        chunk_dicts = [c.to_dict() for c in chunks]
        stored = store_chunks(chunk_dicts)

        doc.chunk_count = stored
        doc.status = Document.Status.READY
        doc.error_message = ""
        doc.save(update_fields=["chunk_count", "status", "error_message"])

        rag_chunks_stored_total.inc(stored)
        rag_documents_ready.inc()

        duration = time.monotonic() - start
        rag_ingestion_duration.observe(duration)
        logger.info("Document %s processed: %d chunks in %.2fs", doc.original_filename, stored, duration)

    except Exception as exc:
        duration = time.monotonic() - start
        logger.error("Document processing failed for %s after %.2fs: %s", document_id, duration, exc)
        try:
            doc.status = Document.Status.FAILED
            doc.error_message = str(exc)[:1000]
            doc.save(update_fields=["status", "error_message"])
        except Exception as save_exc:
            logger.error("Failed to save error status for document %s: %s", document_id, save_exc)
