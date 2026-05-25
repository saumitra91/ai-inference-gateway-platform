from __future__ import annotations

import hashlib
import logging
from typing import Any

import numpy as np

from apps.agents.metrics import agent_duplicate_results_filtered_total
from apps.agents.services.sources.base import SourceItem

logger = logging.getLogger(__name__)

_similarity_threshold = 0.92


class SemanticDeduplicator:
    def __init__(self, threshold: float = _similarity_threshold):
        self._threshold = threshold
        self._seen_hashes: set[str] = set()
        self._seen_embeddings: list[list[float]] = []
        self._duplicate_count = 0

    def is_duplicate(self, item: SourceItem, agent_type: str = "", agent_name: str = "") -> bool:
        url_hash = hashlib.sha256(item.url.encode("utf-8")).hexdigest()[:16]
        if url_hash in self._seen_hashes:
            self._duplicate_count += 1
            agent_duplicate_results_filtered_total.labels(agent_type=agent_type, agent_name=agent_name).inc()
            return True
        self._seen_hashes.add(url_hash)

        title_hash = hashlib.sha256(item.title.encode("utf-8")).hexdigest()[:16]
        if title_hash in self._seen_hashes:
            self._duplicate_count += 1
            agent_duplicate_results_filtered_total.labels(agent_type=agent_type, agent_name=agent_name).inc()
            return True
        self._seen_hashes.add(title_hash)

        return False

    def is_semantic_duplicate(
        self,
        embedding: list[float],
        agent_type: str = "",
        agent_name: str = "",
    ) -> bool:
        if not self._seen_embeddings:
            self._seen_embeddings.append(embedding)
            return False

        embedding_arr = np.array(embedding, dtype=np.float32)
        for seen in self._seen_embeddings:
            seen_arr = np.array(seen, dtype=np.float32)
            norm_product = np.linalg.norm(embedding_arr) * np.linalg.norm(seen_arr)
            if norm_product == 0:
                continue
            sim = float(np.dot(embedding_arr, seen_arr) / norm_product)
            if sim >= self._threshold:
                self._duplicate_count += 1
                agent_duplicate_results_filtered_total.labels(agent_type=agent_type, agent_name=agent_name).inc()
                return True

        self._seen_embeddings.append(embedding)
        return False

    @property
    def duplicates_filtered(self) -> int:
        return self._duplicate_count

    def reset(self) -> None:
        self._seen_hashes.clear()
        self._seen_embeddings.clear()
        self._duplicate_count = 0


def should_deduplicate_by_hash(
    existing_hashes: set[str],
    item: SourceItem,
    agent_type: str = "",
    agent_name: str = "",
) -> bool:
    url_hash = hashlib.sha256(item.url.encode("utf-8")).hexdigest()[:16]
    if url_hash in existing_hashes:
        agent_duplicate_results_filtered_total.labels(agent_type=agent_type, agent_name=agent_name).inc()
        return True
    title_hash = hashlib.sha256(item.title.encode("utf-8")).hexdigest()[:16]
    if title_hash in existing_hashes:
        agent_duplicate_results_filtered_total.labels(agent_type=agent_type, agent_name=agent_name).inc()
        return True
    existing_hashes.add(url_hash)
    existing_hashes.add(title_hash)
    return False


def should_deduplicate_by_semantics(
    existing_embeddings: list[list[float]],
    new_embedding: list[float],
    threshold: float = _similarity_threshold,
    agent_type: str = "",
    agent_name: str = "",
) -> bool:
    if not existing_embeddings:
        return False
    new_arr = np.array(new_embedding, dtype=np.float32)
    for emb in existing_embeddings:
        emb_arr = np.array(emb, dtype=np.float32)
        norm_product = np.linalg.norm(new_arr) * np.linalg.norm(emb_arr)
        if norm_product == 0:
            continue
        sim = float(np.dot(new_arr, emb_arr) / norm_product)
        if sim >= threshold:
            agent_duplicate_results_filtered_total.labels(agent_type=agent_type, agent_name=agent_name).inc()
            return True
    return False
