"""OpenAI-compatible HTTP surface — thin controllers delegating to `ChatCompletionService`."""

from __future__ import annotations

import logging

from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from django.views.decorators.http import require_POST

from apps.api_keys.services.auth import APIKeyAuthError, resolve_bearer_api_key
from apps.inference.services.chat_completion import ChatCompletionService
from apps.users.models import UserProfile

logger = logging.getLogger(__name__)


async def _read_body(request: HttpRequest) -> bytes:
    read = getattr(request, "aread", None)
    if callable(read):
        return await read()
    return request.body


@csrf_exempt
@require_POST
async def programmatic_chat_completions(request: HttpRequest) -> HttpResponse:
    """POST /v1/chat/completions — Bearer `sk_local_...` API key required."""
    try:
        resolved = await resolve_bearer_api_key(request)
    except APIKeyAuthError as exc:
        return JsonResponse(
            {"error": {"message": exc.message, "type": "authentication_error"}},
            status=exc.status,
        )

    raw = await _read_body(request)
    service = ChatCompletionService(
        request=request,
        mode="programmatic",
        actor_user=resolved.api_key.user,
        api_key=resolved.api_key,
    )
    return await service.handle(raw)


@csrf_protect
@login_required
@require_POST
async def ui_chat_completions(request: HttpRequest) -> HttpResponse:
    """POST /ui/v1/chat/completions — session-authenticated dashboard path (CSRF required)."""
    user = request.user
    profile = await sync_to_async(
        lambda: UserProfile.objects.filter(user=user).select_related("default_api_key").first()
    )()
    api_key = profile.default_api_key if profile else None

    raw = await _read_body(request)
    service = ChatCompletionService(
        request=request,
        mode="ui",
        actor_user=user,
        api_key=api_key,
    )
    return await service.handle(raw)
