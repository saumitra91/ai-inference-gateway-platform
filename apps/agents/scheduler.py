from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from django.conf import settings
from django.utils import timezone as tz

from apps.agents.metrics import agent_scheduler_queue_depth
from apps.agents.models import Agent

logger = logging.getLogger(__name__)

_scheduler_started = False


def start_scheduler() -> None:
    global _scheduler_started
    if _scheduler_started:
        return
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("APScheduler not installed. Install with: pip install apscheduler")
        return

    scheduler = BackgroundScheduler(timezone="UTC")

    enabled_agents = Agent.objects.filter(enabled=True).exclude(schedule_cron="").iterator()
    for agent in enabled_agents:
        cron = agent.schedule_cron
        try:
            trigger = CronTrigger.from_crontab(cron)
            scheduler.add_job(
                _scheduled_run,
                trigger=trigger,
                id=f"agent_{agent.id}",
                name=f"agent_{agent.slug}",
                args=[str(agent.id)],
                replace_existing=True,
                misfire_grace_time=300,
                max_instances=1,
                coalesce=True,
            )
            logger.info("Scheduled agent '%s' with cron: %s", agent.name, cron)
        except (ValueError, KeyError) as exc:
            logger.warning("Invalid cron '%s' for agent '%s': %s", cron, agent.name, exc)

    scheduler.start()
    _scheduler_started = True
    agent_scheduler_queue_depth.set(scheduler.get_jobs().__len__())
    logger.info("Agent scheduler started with %d jobs", len(scheduler.get_jobs()))


def _scheduled_run(agent_id: str) -> None:
    from apps.agents.services.runner import AgentRunner

    runner = AgentRunner()
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(runner.run_agent(agent_id))
        loop.close()
        logger.info("Scheduled run for agent %s: %s", agent_id, result.get("status"))
    except Exception as exc:
        logger.exception("Scheduled run failed for agent %s: %s", agent_id, exc)


async def run_scheduled_agents() -> None:
    agents = Agent.objects.filter(enabled=True).iterator()
    runner = __import__("apps.agents.services.runner", fromlist=["AgentRunner"]).AgentRunner()
    for agent in agents:
        try:
            result = await runner.run_agent(str(agent.id))
            logger.info("Scheduled run for '%s': %s", agent.name, result.get("status"))
        except Exception as exc:
            logger.exception("Scheduled run failed for '%s': %s", agent.name, exc)


def get_scheduler_status() -> dict:
    return {
        "started": _scheduler_started,
        "enabled_agents_count": Agent.objects.filter(enabled=True).count(),
        "scheduled_agents_count": Agent.objects.filter(enabled=True).exclude(schedule_cron="").count(),
    }
