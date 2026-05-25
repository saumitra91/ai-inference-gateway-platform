from __future__ import annotations

import hashlib
import logging
import threading
import time

import numpy as np

from apps.agents.metrics import agent_embedding_requests_total

logger = logging.getLogger(__name__)

_model = None
_model_lock = threading.Lock()
_embedding_dim = 384


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:
            return _model
        try:
            from sentence_transformers import SentenceTransformer

            model_name = "all-MiniLM-L6-v2"
            logger.info("Loading agent embedding model: %s", model_name)
            _model = SentenceTransformer(model_name)
            global _embedding_dim
            _embedding_dim = _model.get_sentence_embedding_dimension()
            logger.info("Agent embedding model loaded: dim=%d", _embedding_dim)
        except Exception as exc:
            logger.error("Failed to load agent embedding model: %s", exc)
            raise
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = _load_model()
    start = time.monotonic()
    embeddings = model.encode(texts, show_progress_bar=False, normalize_embeddings=True)
    agent_embedding_requests_total.labels(agent_type="agent").inc(len(texts))
    logger.debug("Embedded %d texts in %.2fs", len(texts), time.monotonic() - start)
    return embeddings.tolist()


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    a_arr = np.array(a, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))


def compute_semantic_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8"))[:12].hexdigest()


def compute_semantic_hash_from_embedding(embedding: list[float]) -> str:
    arr = np.array(embedding, dtype=np.float32)
    return hashlib.sha256(arr.tobytes()).hexdigest()[:16]
