"""HTTP surface — session-backed UI inference only (programmatic `/v1` is on the FastAPI gateway)."""

from __future__ import annotations

from asgiref.sync import sync_to_async
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from apps.inference.services.chat_completion import ChatCompletionService
from apps.users.models import UserProfile


async def _read_body(request: HttpRequest) -> bytes:
    read = getattr(request, "aread", None)
    if callable(read):
        return await read()
    return request.body


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
