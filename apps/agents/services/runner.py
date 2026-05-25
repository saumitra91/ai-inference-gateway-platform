from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from asgiref.sync import sync_to_async
from django.utils import timezone as tz

from apps.agents.metrics import (
    agent_active_runs,
    agent_failures_total,
    agent_run_duration_seconds,
    agent_runs_total,
)
from apps.agents.models import Agent, AgentRun, TelegramConfig
from apps.agents.services.pipelines import get_pipeline
from apps.agents.services.telegram.service import TelegramService

logger = logging.getLogger(__name__)


class AgentRunner:
    MAX_RUN_TIMEOUT_S = 600

    async def run_agent(self, agent_id: str) -> dict[str, Any]:
        agent = await sync_to_async(Agent.objects.get)(id=agent_id)

        if not agent.enabled:
            return {"status": "skipped", "reason": "Agent is disabled"}

        run = await sync_to_async(AgentRun.objects.create)(
            agent=agent,
            status=AgentRun.Status.RUNNING,
        )

        agent_active_runs.labels(agent_type=agent.type).inc()
        agent_runs_total.labels(agent_type=agent.type, agent_name=agent.name, status="started").inc()

        telegram_config = await sync_to_async(
            lambda: TelegramConfig.objects.filter(enabled=True).first()
        )()
        telegram = TelegramService(config=telegram_config) if telegram_config else TelegramService()

        start_time = time.monotonic()
        try:
            pipeline = get_pipeline(agent.type, llm_backend=agent.llm_backend_preference, telegram=telegram)
            run = await pipeline.run(agent, run)

            duration = time.monotonic() - start_time
            agent_run_duration_seconds.labels(agent_type=agent.type, agent_name=agent.name).observe(duration)
            agent_runs_total.labels(agent_type=agent.type, agent_name=agent.name, status="completed").inc()

            agent.last_run_at = tz.now()
            await sync_to_async(agent.save)(update_fields=["last_run_at"])

            logger.info(
                "Agent '%s' completed in %.1fs: %d results",
                agent.name,
                duration,
                run.discovered_count,
            )

            return {
                "status": "completed",
                "run_id": str(run.id),
                "duration_ms": run.duration_ms,
                "discovered_count": run.discovered_count,
                "sent_count": run.sent_count,
            }

        except Exception as exc:
            duration = time.monotonic() - start_time
            logger.exception("Agent '%s' failed after %.1fs: %s", agent.name, duration, exc)

            error_type = type(exc).__name__
            agent_failures_total.labels(agent_type=agent.type, agent_name=agent.name, error_type=error_type).inc()
            agent_runs_total.labels(agent_type=agent.type, agent_name=agent.name, status="failed").inc()

            run.status = AgentRun.Status.FAILED
            run.completed_at = tz.now()
            run.duration_ms = int(duration * 1000)
            run.error_message = str(exc)[:2000]
            await sync_to_async(run.save)(update_fields=["status", "completed_at", "duration_ms", "error_message"])

            agent.last_run_at = tz.now()
            await sync_to_async(agent.save)(update_fields=["last_run_at"])

            try:
                if telegram.is_available:
                    telegram.send_error(str(exc)[:500], agent_name=agent.name)
            except Exception as tg_exc:
                logger.warning("Failed to send error notification: %s", tg_exc)

            return {
                "status": "failed",
                "run_id": str(run.id),
                "error": str(exc)[:500],
                "duration_ms": int(duration * 1000),
            }

        finally:
            agent_active_runs.labels(agent_type=agent.type).dec()

    async def run_agent_sync(self, agent_id: str) -> dict[str, Any]:
        return await self.run_agent(agent_id)
