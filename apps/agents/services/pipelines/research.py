from __future__ import annotations

import hashlib
import logging
import time
from typing import Any

from asgiref.sync import sync_to_async

from apps.agents.metrics import (
    agent_results_discovered_total,
    agent_source_fetch_latency_seconds,
)
from apps.agents.models import Agent, AgentResult, AgentRun
from apps.agents.services.dedup.service import SemanticDeduplicator
from apps.agents.services.digest.service import DigestAssembler
from apps.agents.services.embeddings.service import cosine_similarity, embed_text, embed_texts
from apps.agents.services.llm.service import LLMService
from apps.agents.services.sources import get_source
from apps.agents.services.sources.base import SourceItem, SourceResult
from apps.agents.services.telegram.service import TelegramService

logger = logging.getLogger(__name__)


class ResearchPipeline:
    def __init__(
        self,
        llm_service: LLMService | None = None,
        telegram: TelegramService | None = None,
        digest: DigestAssembler | None = None,
        llm_backend: str = "llamacpp",
    ):
        self._llm = llm_service or LLMService(backend=llm_backend)
        self._telegram = telegram or TelegramService()
        self._digest = digest or DigestAssembler(self._llm)
        self._dedup = SemanticDeduplicator()

    async def run(self, agent: Agent, run: AgentRun) -> AgentRun:
        logger.info("Starting research pipeline for agent: %s", agent.name)
        start_time = time.monotonic()
        all_items: list[SourceItem] = []
        all_errors: list[str] = []
        stage_logs: list[dict[str, Any]] = []

        source_names = agent.sources or ["hackernews", "reddit", "arxiv"]
        query = agent.search_query or agent.instructions

        for source_name in source_names:
            stage_start = time.monotonic()
            try:
                source = get_source(source_name)
                result: SourceResult = await source.fetch(query, agent.max_results)
                elapsed = (time.monotonic() - stage_start) * 1000
                agent_source_fetch_latency_seconds.labels(
                    source=source_name,
                    status="ok" if not result.error else "error",
                ).observe(elapsed / 1000.0)
                stage_logs.append({
                    "stage": "collect",
                    "source": source_name,
                    "items_count": len(result.items),
                    "fetch_time_ms": round(elapsed, 1),
                    "error": result.error,
                })
                if result.error:
                    all_errors.append(f"{source_name}: {result.error}")
                all_items.extend(result.items)
                logger.info("Source %s returned %d items in %.0fms", source_name, len(result.items), elapsed)
            except ValueError as exc:
                logger.warning("Unknown source '%s', skipping", source_name)
                stage_logs.append({"stage": "collect", "source": source_name, "error": f"Unknown source: {exc}"})
                continue
            except Exception as exc:
                logger.error("Source %s failed: %s", source_name, exc)
                stage_logs.append({"stage": "collect", "source": source_name, "error": str(exc)})
                all_errors.append(f"{source_name}: {exc}")
                continue

        stage_logs.append({"stage": "collect_complete", "total_items": len(all_items)})

        deduped_items: list[SourceItem] = []
        self._dedup.reset()
        for item in all_items:
            if not self._dedup.is_duplicate(item, agent_type=agent.type, agent_name=agent.name):
                deduped_items.append(item)

        stage_logs.append({
            "stage": "dedup",
            "before": len(all_items),
            "after": len(deduped_items),
            "duplicates_filtered": self._dedup.duplicates_filtered,
        })
        logger.info("Dedup: %d -> %d items", len(all_items), len(deduped_items))

        # Relevance filter + embed: embed items once, score against instructions, keep related content
        RELEVANCE_THRESHOLD = 0.20
        scored_items: list[tuple[SourceItem, float]] = []
        item_emb_map: dict[str, list[float]] = {}
        if agent.instructions and deduped_items:
            try:
                inst_emb = embed_text(agent.instructions)
                item_texts = [f"{item.title}\n{item.content[:500]}" for item in deduped_items]
                item_embs = embed_texts(item_texts)
                for item, emb in zip(deduped_items, item_embs):
                    key = item.url or item.title
                    item_emb_map[key] = emb
                    score = cosine_similarity(inst_emb, emb) if emb else 0.0
                    scored_items.append((item, score))
                scored_items.sort(key=lambda x: x[1], reverse=True)
                relevant = [(item, score) for item, score in scored_items if score >= RELEVANCE_THRESHOLD]
                if not relevant:
                    relevant = scored_items[:1]
                ranked_items = [item for item, _ in relevant[:agent.max_results]]
                stage_logs.append({
                    "stage": "relevance_filter",
                    "before": len(scored_items),
                    "after": len(ranked_items),
                    "threshold": RELEVANCE_THRESHOLD,
                })
                logger.info("Relevance filter: %d -> %d items (threshold=%.2f)", len(scored_items), len(ranked_items), RELEVANCE_THRESHOLD)
            except Exception as exc:
                logger.warning("Relevance filtering failed, falling back to all items: %s", exc)
                ranked_items = deduped_items[:agent.max_results]
                stage_logs.append({"stage": "relevance_filter", "error": str(exc), "fallback": True})
        else:
            ranked_items = deduped_items[:agent.max_results]
            stage_logs.append({"stage": "relevance_filter", "skipped": "no instructions"})

        summaries: list[str] = []
        llm_tokens = 0
        for item in ranked_items[:10]:
            try:
                summary = self._llm.summarize(f"{item.title}\n{item.content[:2000]}", max_tokens=128)
                summaries.append(summary)
                llm_tokens += 100
            except Exception as exc:
                logger.warning("Summarization failed for '%s': %s", item.title, exc)
                summaries.append("")

        stage_logs.append({
            "stage": "llm_summarize",
            "summarized_count": len(summaries),
        })

        items_text = "\n\n".join(
            f"- {item.title}\n{item.content[:500]}" for item in ranked_items[:15]
        )

        synthesis = ""
        try:
            synthesis = self._llm.synthesize_trends(
                items_text=items_text,
                instructions=agent.instructions,
            )
            llm_tokens += 200
        except Exception as exc:
            logger.warning("Trend synthesis failed: %s", exc)
            synthesis = "Trend synthesis unavailable."

        stage_logs.append({"stage": "synthesis", "synthesis_length": len(synthesis)})

        result_objects: list[AgentResult] = []

        for i, item in enumerate(ranked_items):
            key = item.url or item.title
            embedding = item_emb_map.get(key, [])
            semantic_hash = hashlib.sha256(
                (item.url + item.title + item.content[:200]).encode("utf-8")
            ).hexdigest()[:16]
            summary_text = summaries[i] if i < len(summaries) else ""
            relevance_score = next(
                (s for it, s in scored_items if (it.url or it.title) == key),
                0.0,
            )

            metadata = dict(item.metadata) if item.metadata else {}
            metadata["relevance_score"] = round(relevance_score, 3)

            result = await sync_to_async(AgentResult.objects.create)(
                agent=agent,
                run=run,
                title=item.title,
                url=item.url,
                source=item.source,
                content=item.content[:5000],
                summary=summary_text,
                metadata=metadata,
                semantic_hash=semantic_hash,
                match_score=relevance_score,
            )
            result_objects.append(result)
            agent_results_discovered_total.labels(
                agent_type=agent.type,
                agent_name=agent.name,
                source=item.source,
            ).inc()

        stage_logs.append({"stage": "persist", "results_stored": len(result_objects)})

        digest_text = ""
        try:
            digest_text = self._digest.assemble_markdown(agent, run, result_objects)
        except Exception as exc:
            logger.warning("Digest assembly failed: %s", exc)

        sent_count = 0
        if digest_text and agent.digest_frequency != "disabled":
            try:
                sent = self._telegram.send_digest(digest_text)
                if sent:
                    sent_count = len(result_objects)
                stage_logs.append({"stage": "telegram", "sent": sent})
            except Exception as exc:
                logger.warning("Telegram send failed: %s", exc)
                stage_logs.append({"stage": "telegram", "error": str(exc)})

        duration_ms = int((time.monotonic() - start_time) * 1000)

        run.completed_at = __import__("django").utils.timezone.now()
        run.status = AgentRun.Status.COMPLETED
        run.duration_ms = duration_ms
        run.discovered_count = len(result_objects)
        run.sent_count = sent_count
        run.summary = synthesis[:2000] if synthesis else "Research complete."
        run.tokens_used = llm_tokens
        run.raw_logs = stage_logs
        await sync_to_async(run.save)()

        logger.info(
            "Research pipeline complete for '%s': %d results in %.1fs",
            agent.name,
            len(result_objects),
            duration_ms / 1000,
        )

        return run
