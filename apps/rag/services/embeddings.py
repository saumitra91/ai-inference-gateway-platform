from __future__ import annotations

import logging
import threading
import time

from django.conf import settings

from apps.rag.metrics import rag_embedding_latency

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from sentence_transformers import SentenceTransformer
            model_name = settings.RAG_EMBEDDING_MODEL
            logger.info("Loading embedding model: %s", model_name)
            _model = SentenceTransformer(model_name)
            logger.info("Embedding model loaded: dim=%d", _model.get_sentence_embedding_dimension())
        except Exception as exc:
            logger.error("Failed to load embedding model: %s", exc)
            raise
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = _load_model()
    start = time.monotonic()
    embeddings = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    rag_embedding_latency.observe(time.monotonic() - start)
    return embeddings.tolist()


def embed_query(text: str) -> list[float]:
    return embed_texts([text])[0]
