from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from apps.api_keys.services.auth import APIKeyAuthError, resolve_bearer_api_key
from apps.inference.exceptions import UpstreamHTTPError, UpstreamUnavailableError
from apps.inference.services.llama_cpp import LlamaCppBackend
from apps.observability.context import get_request_id

logger = logging.getLogger(__name__)


@csrf_exempt
@require_GET
async def programmatic_models_list(request: HttpRequest) -> HttpResponse:
    try:
        _ = await resolve_bearer_api_key(request)
    except APIKeyAuthError as exc:
        return JsonResponse(
            {"error": {"message": exc.message, "type": "authentication_error"}},
            status=exc.status,
        )

    backend = LlamaCppBackend()
    extra: dict[str, str] = {}
    rid = get_request_id() or request.headers.get("X-Request-ID")
    if rid:
        extra["X-Request-ID"] = rid

    try:
        body = await backend.list_models(extra_headers=extra or None)
    except UpstreamUnavailableError as exc:
        logger.warning("models_upstream_unavailable", extra={"error": str(exc)})
        return JsonResponse({"error": {"message": "Upstream unavailable", "type": "api_error"}}, status=502)
    except UpstreamHTTPError as exc:
        return HttpResponse(exc.body, status=exc.status_code, content_type="application/json")

    return HttpResponse(body, content_type="application/json")
