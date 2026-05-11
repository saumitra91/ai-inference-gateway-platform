from __future__ import annotations

import hmac
import logging
import os
from typing import Any

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connection
from django.http import HttpRequest, HttpResponse, JsonResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from apps.observability.runtime_metrics import collect_runtime_metrics

logger = logging.getLogger(__name__)


def metrics_view(request: HttpRequest) -> HttpResponse:
    expected = os.environ.get("METRICS_SCRAPE_TOKEN", "").strip()
    if expected:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return HttpResponse(status=401)
        token = auth[len("Bearer ") :].strip()
        if not hmac.compare_digest(token, expected):
            return HttpResponse(status=401)

    collect_runtime_metrics()
    data = generate_latest()
    return HttpResponse(data, content_type=CONTENT_TYPE_LATEST)


def live_view(request: HttpRequest) -> JsonResponse:
    _ = request
    return JsonResponse({"status": "live"})


@sync_to_async
def _db_check() -> None:
    connection.ensure_connection()


async def _llama_healthcheck() -> tuple[str, str]:
    base = str(getattr(settings, "LLAMA_CPP_BASE_URL", "") or "").rstrip("/")
    if not base:
        return "skipped", "no_base_url_configured"
    url = f"{base}/health"
    timeout = httpx.Timeout(connect=0.75, read=1.25, write=1.0, pool=1.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            if resp.status_code < 500:
                return "ok", f"http_{resp.status_code}"
            return "failed", f"http_{resp.status_code}"
    except Exception as exc:
        logger.info("llama_healthcheck_failed", extra={"error": str(exc)})
        return "failed", "unreachable"


async def ready_view(request: HttpRequest) -> JsonResponse:
    _ = request
    checks: dict[str, Any] = {}

    try:
        await _db_check()
        checks["database"] = "ok"
    except Exception:
        logger.exception("readiness_database_failed")
        checks["database"] = "failed"
        return JsonResponse({"status": "not_ready", "checks": checks}, status=503)

    if getattr(settings, "READINESS_INCLUDE_LLAMA", False):
        status, detail = await _llama_healthcheck()
        checks["llamacpp"] = {"status": status, "detail": detail}
        if status != "ok" and status != "skipped":
            return JsonResponse({"status": "not_ready", "checks": checks}, status=503)

    return JsonResponse({"status": "ready", "checks": checks})


async def model_status_view(request: HttpRequest) -> JsonResponse:
    """Operational model/runtime status (not OpenAI-compatible)."""
    _ = request
    status, detail = await _llama_healthcheck()
    return JsonResponse(
        {
            "llamacpp": {"reachable": status in {"ok", "skipped"}, "status": status, "detail": detail},
            "django": {"ok": True},
        }
    )
