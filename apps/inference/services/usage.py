from __future__ import annotations

from asgiref.sync import sync_to_async
from django.db.models import F
from django.utils import timezone

from apps.api_keys.models import APIKey


@sync_to_async(thread_sensitive=True)
def bump_api_key_usage(
    *,
    api_key: APIKey,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    APIKey.objects.filter(pk=api_key.pk).update(
        last_used_at=timezone.now(),
        requests_count=F("requests_count") + 1,
        prompt_tokens_total=F("prompt_tokens_total") + max(0, int(prompt_tokens)),
        completion_tokens_total=F("completion_tokens_total") + max(0, int(completion_tokens)),
    )
