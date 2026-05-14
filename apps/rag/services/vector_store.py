from __future__ import annotations

import logging
import threading
import time
from typing import Any

from django.conf import settings

from apps.rag.metrics import rag_vector_db_latency

from .embeddings import embed_texts

logger = logging.getLogger(__name__)

_client = None
_client_lock = threading.Lock()


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        try:
            import chromadb
            _client = chromadb.HttpClient(
                host=settings.CHROMADB_HOST,
                port=settings.CHROMADB_PORT,
            )
            logger.info("Connected to ChromaDB at %s:%s", settings.CHROMADB_HOST, settings.CHROMADB_PORT)
        except Exception as exc:
            logger.error("Failed to connect to ChromaDB: %s", exc)
            raise
    return _client


def _get_collection():
    client = _get_client()
    try:
        return client.get_or_create_collection(
            name=settings.CHROMADB_COLLECTION,
            metadata={"hnsw:space": "cosine"},
        )
    except Exception as exc:
        logger.error("Failed to get/create collection: %s", exc)
        raise


def _chunked(items: list[Any], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def store_chunks(chunks: list[dict[str, object]]) -> int:
    if not chunks:
        return 0

    texts = [c["text"] for c in chunks]
    ids = [c["id"] for c in chunks]
    metadatas = [
        {
            "document_id": c["document_id"],
            "chunk_index": c["chunk_index"],
            "page_number": c["page_number"],
        }
        for c in chunks
    ]

    embeddings = embed_texts(texts)

    collection = _get_collection()
    stored = 0

    for batch_ids, batch_embeddings, batch_metadatas, batch_texts in zip(
        _chunked(ids, 100),
        _chunked(embeddings, 100),
        _chunked(metadatas, 100),
        _chunked(texts, 100),
    ):
        try:
            collection.add(
                embeddings=batch_embeddings,
                documents=batch_texts,
                metadatas=batch_metadatas,
                ids=batch_ids,
            )
            stored += len(batch_ids)
        except Exception as exc:
            logger.error("Failed to store batch in ChromaDB: %s", exc)
            raise

    logger.info("Stored %d chunks in ChromaDB", stored)
    return stored


def search_chunks(query_text: str, top_k: int | None = None, document_ids: list[str] | None = None) -> list[dict[str, Any]]:
    top_k = top_k or settings.RAG_TOP_K
    collection = _get_collection()

    query_embedding = embed_texts([query_text])

    where: dict[str, Any] | None = None
    if document_ids:
        where = {"document_id": {"$in": document_ids}}

    db_start = time.monotonic()
    try:
        results = collection.query(
            query_embeddings=query_embedding,
            n_results=min(top_k, 50),
            where=where,
            include=["documents", "metadatas", "distances"],
        )
    except Exception as exc:
        rag_vector_db_latency.observe(time.monotonic() - db_start)
        logger.error("ChromaDB query failed: %s", exc)
        return []

    rag_vector_db_latency.observe(time.monotonic() - db_start)

    hits: list[dict[str, Any]] = []
    if results and results["ids"] and results["ids"][0]:
        for idx, doc_id in enumerate(results["ids"][0]):
            distance = results["distances"][0][idx] if results.get("distances") else 0.0
            similarity = 1.0 - distance
            hit = {
                "id": doc_id,
                "text": results["documents"][0][idx],
                "metadata": results["metadatas"][0][idx],
                "score": round(similarity, 4),
            }
            hits.append(hit)

    return hits


def delete_document_chunks(document_id: str) -> int:
    collection = _get_collection()
    try:
        results = collection.get(where={"document_id": document_id})
        ids = results.get("ids", [])
        if ids:
            collection.delete(ids=ids)
            logger.info("Deleted %d chunks for document %s", len(ids), document_id)
            return len(ids)
        return 0
    except Exception as exc:
        logger.error("Failed to delete chunks for document %s: %s", document_id, exc)
        return 0


def health() -> bool:
    try:
        client = _get_client()
        client.heartbeat()
        return True
    except Exception:
        return False
