from __future__ import annotations

import json
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_GET, require_POST

from asgiref.sync import async_to_sync

from apps.agents.models import Agent, AgentResult, AgentRun, TelegramConfig
from apps.agents.scheduler import get_scheduler_status
from apps.agents.services.runner import AgentRunner

logger = logging.getLogger(__name__)


@method_decorator(login_required, name="dispatch")
class AgentListView(View):
    def get(self, request: HttpRequest):
        agents = Agent.objects.all().order_by("-created_at")
        return render(request, "agents/list.html", {"agents": agents})


@method_decorator(login_required, name="dispatch")
class AgentCreateView(View):
    def get(self, request: HttpRequest):
        return render(request, "agents/create.html")

    def post(self, request: HttpRequest):
        from django.utils.text import slugify

        data = json.loads(request.body)
        name = data.get("name", "").strip()
        if not name:
            return JsonResponse({"error": "Name is required"}, status=400)

        slug_base = slugify(name)
        slug = slug_base
        counter = 1
        while Agent.objects.filter(slug=slug).exists():
            slug = f"{slug_base}-{counter}"
            counter += 1

        agent = Agent.objects.create(
            name=name,
            slug=slug,
            type=data.get("type", Agent.Type.MARKET_RESEARCH),
            instructions=data.get("instructions", ""),
            search_query=data.get("search_query", ""),
            schedule_cron=data.get("schedule_cron", ""),
            digest_frequency=data.get("digest_frequency", Agent.DigestFrequency.DISABLED),
            llm_backend_preference=data.get("llm_backend_preference", Agent.BackendPreference.LLAMACPP),
            sources=data.get("sources", []),
            max_results=int(data.get("max_results", 25)),
            enabled=True,
        )
        return JsonResponse({"id": str(agent.id), "slug": agent.slug}, status=201)


@method_decorator(login_required, name="dispatch")
class AgentUpdateView(View):
    def get(self, request: HttpRequest, agent_id: str):
        agent = get_object_or_404(Agent, id=agent_id)
        return render(request, "agents/edit.html", {"agent": agent})

    def post(self, request: HttpRequest, agent_id: str):
        agent = get_object_or_404(Agent, id=agent_id)
        data = json.loads(request.body)
        name = data.get("name", "").strip()
        if not name:
            return JsonResponse({"error": "Name is required"}, status=400)
        agent.name = name
        agent.type = data.get("type", agent.type)
        agent.instructions = data.get("instructions", "")
        agent.search_query = data.get("search_query", "")
        agent.schedule_cron = data.get("schedule_cron", "")
        agent.digest_frequency = data.get("digest_frequency", Agent.DigestFrequency.DISABLED)
        agent.llm_backend_preference = data.get("llm_backend_preference", Agent.BackendPreference.LLAMACPP)
        agent.sources = data.get("sources", [])
        agent.max_results = int(data.get("max_results", 25))
        agent.save()
        return JsonResponse({"id": str(agent.id), "slug": agent.slug})


@method_decorator(login_required, name="dispatch")
class AgentDetailView(View):
    def get(self, request: HttpRequest, agent_id: str):
        agent = get_object_or_404(Agent, id=agent_id)
        runs = AgentRun.objects.filter(agent=agent).order_by("-started_at")[:50]
        return render(request, "agents/detail.html", {"agent": agent, "runs": runs})


@method_decorator(login_required, name="dispatch")
class AgentResultsView(View):
    def get(self, request: HttpRequest):
        agents = Agent.objects.all()
        results = AgentResult.objects.select_related("agent", "run").all().order_by("-created_at")
        source_filter = request.GET.get("source", "")
        agent_filter = request.GET.get("agent_id", "")
        search = request.GET.get("q", "")

        if source_filter:
            results = results.filter(source=source_filter)
        if agent_filter:
            results = results.filter(agent_id=agent_filter)
        if search:
            results = results.filter(title__icontains=search)

        sources = AgentResult.objects.values_list("source", flat=True).distinct().order_by("source")

        return render(request, "agents/results.html", {
            "results": results[:200],
            "agents": agents,
            "sources": sources,
            "current_source": source_filter,
            "current_agent": agent_filter,
            "search": search,
        })


@method_decorator(login_required, name="dispatch")
class TelegramConfigView(View):
    def get(self, request: HttpRequest):
        config = TelegramConfig.objects.first()
        return render(request, "agents/telegram.html", {"config": config})

    def post(self, request: HttpRequest):
        data = json.loads(request.body)
        first = TelegramConfig.objects.first()
        config, _ = TelegramConfig.objects.update_or_create(
            pk=first.pk if first else None,
            defaults={
                "enabled": data.get("enabled", False),
                "bot_token": data.get("bot_token", ""),
                "chat_id": data.get("chat_id", ""),
                "digest_enabled": data.get("digest_enabled", True),
                "digest_schedule": data.get("digest_schedule", "daily"),
            },
        )
        return JsonResponse({"status": "saved", "id": config.pk})


@require_POST
@login_required
def agent_run_now(request: HttpRequest, agent_id: str):
    agent = get_object_or_404(Agent, id=agent_id)
    runner = AgentRunner()
    result = async_to_sync(runner.run_agent)(agent_id)
    return JsonResponse(result)


@require_POST
@login_required
def agent_toggle_enabled(request: HttpRequest, agent_id: str):
    agent = get_object_or_404(Agent, id=agent_id)
    data = json.loads(request.body)
    agent.enabled = data.get("enabled", not agent.enabled)
    agent.save(update_fields=["enabled"])
    return JsonResponse({"id": str(agent.id), "enabled": agent.enabled})


@require_POST
@login_required
def agent_delete(request: HttpRequest, agent_id: str):
    agent = get_object_or_404(Agent, id=agent_id)
    agent.delete()
    return JsonResponse({"status": "deleted"})


@require_GET
@login_required
def agent_run_detail(request: HttpRequest, agent_id: str, run_id: str):
    run = get_object_or_404(AgentRun, id=run_id, agent_id=agent_id)
    results = AgentResult.objects.filter(run=run).order_by("-match_score", "-created_at")
    return render(request, "agents/run_detail.html", {"run": run, "results": results})


@require_GET
@login_required
def scheduler_status(request: HttpRequest):
    return JsonResponse(get_scheduler_status())


@require_GET
@login_required
def api_agent_list(request: HttpRequest):
    agents = Agent.objects.all().values("id", "name", "slug", "type", "enabled", "last_run_at", "created_at")
    return JsonResponse({"agents": list(agents)})


@require_GET
@login_required
def api_agent_runs(request: HttpRequest, agent_id: str):
    runs = AgentRun.objects.filter(agent_id=agent_id).values(
        "id", "status", "started_at", "completed_at", "duration_ms",
        "discovered_count", "sent_count", "tokens_used",
    )
    return JsonResponse({"runs": list(runs)})


@require_GET
@login_required
def api_agent_results(request: HttpRequest, agent_id: str):
    results = AgentResult.objects.filter(agent_id=agent_id).values(
        "id", "title", "url", "source", "match_score", "created_at"
    )[:100]
    return JsonResponse({"results": list(results)})


@require_GET
@login_required
def api_agent_latest_run(request: HttpRequest, agent_id: str):
    run = AgentRun.objects.filter(agent_id=agent_id).order_by("-started_at").first()
    if not run:
        return JsonResponse({"status": "none"})
    return JsonResponse({
        "id": str(run.id),
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "duration_ms": run.duration_ms,
        "discovered_count": run.discovered_count,
        "sent_count": run.sent_count,
        "error_message": run.error_message,
        "raw_logs": run.raw_logs,
    })
