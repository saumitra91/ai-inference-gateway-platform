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
from apps.agents.services.embeddings.service import embed_texts
from apps.agents.services.llm.service import LLMService
from apps.agents.services.ranking.service import RelevanceRanker
from apps.agents.services.sources import get_source
from apps.agents.services.sources.base import SourceItem, SourceResult
from apps.agents.services.telegram.service import TelegramService

logger = logging.getLogger(__name__)


class JobDiscoveryWorkflow:
    def __init__(
        self,
        llm_service: LLMService | None = None,
        telegram: TelegramService | None = None,
        digest: DigestAssembler | None = None,
        ranker: RelevanceRanker | None = None,
        llm_backend: str = "llamacpp",
    ):
        self._llm = llm_service or LLMService(backend=llm_backend)
        self._telegram = telegram or TelegramService()
        self._digest = digest or DigestAssembler(self._llm)
        self._ranker = ranker or RelevanceRanker(self._llm)
        self._dedup = SemanticDeduplicator()

    async def run(self, agent: Agent, run: AgentRun) -> AgentRun:
        logger.info("Starting job discovery workflow for agent: %s", agent.name)
        start_time = time.monotonic()
        all_items: list[SourceItem] = []
        all_errors: list[str] = []
        stage_logs: list[dict[str, Any]] = []

        source_names = agent.sources or ["remoteok", "yc_jobs"]
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

        ranked: list[tuple[SourceItem, float, str]] = []
        if deduped_items:
            try:
                ranked = self._ranker.rank_batch(deduped_items, agent.instructions, max_items=agent.max_results)
            except Exception as exc:
                logger.warning("Ranking failed: %s", exc)
                ranked = [(item, 0.5, "") for item in deduped_items[:agent.max_results]]

        stage_logs.append({
            "stage": "ranking",
            "ranked_count": len(ranked),
            "threshold": 0.3,
        })

        contents = [f"{item.title}\n{item.content[:500]}" for item, _, _ in ranked]
        if contents:
            try:
                embeddings = embed_texts(contents)
            except Exception as exc:
                logger.warning("Embedding failed: %s", exc)
                embeddings = [[] for _ in contents]
        else:
            embeddings = []

        result_objects: list[AgentResult] = []
        for i, (item, score, explanation) in enumerate(ranked):
            embedding = embeddings[i] if i < len(embeddings) else []
            semantic_hash = hashlib.sha256(
                (item.url + item.title + item.content[:200]).encode("utf-8")
            ).hexdigest()[:16]

            summary_text = ""
            if explanation:
                summary_text = explanation
            else:
                try:
                    summary_text = self._llm.summarize(f"{item.title}\n{item.content[:2000]}", max_tokens=96)
                except Exception:
                    pass

            result = await sync_to_async(AgentResult.objects.create)(
                agent=agent,
                run=run,
                title=item.title,
                url=item.url,
                source=item.source,
                content=item.content[:5000],
                summary=summary_text[:300],
                metadata={**item.metadata, "explanation": explanation, "rank_score": score},
                semantic_hash=semantic_hash,
                match_score=score,
            )
            result_objects.append(result)
            agent_results_discovered_total.labels(
                agent_type=agent.type,
                agent_name=agent.name,
                source=item.source,
            ).inc()

        stage_logs.append({"stage": "persist", "results_stored": len(result_objects)})

        report_text = ""
        try:
            if result_objects:
                summary = f"Ranked {len(result_objects)} jobs from {len(deduped_items)} candidates."
                run.summary = summary
            else:
                run.summary = "No matching jobs found."
        except Exception:
            pass

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

        from django.utils import timezone as tz

        run.completed_at = tz.now()
        run.status = AgentRun.Status.COMPLETED
        run.duration_ms = duration_ms
        run.discovered_count = len(result_objects)
        run.sent_count = sent_count
        run.tokens_used = len(result_objects) * 50
        run.raw_logs = stage_logs
        await sync_to_async(run.save)()

        logger.info(
            "Job discovery complete for '%s': %d results in %.1fs",
            agent.name,
            len(result_objects),
            duration_ms / 1000,
        )

        return run
