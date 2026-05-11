from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth.models import AbstractBaseUser

from apps.api_keys.models import APIKey, APIKeyAuditLog

logger = logging.getLogger(__name__)


def write_audit(
    *,
    action: str,
    actor: AbstractBaseUser | None,
    api_key: APIKey | None = None,
    api_key_public_id: str = "",
    message: str = "",
) -> None:
    APIKeyAuditLog.objects.create(
        actor=actor,
        action=action,
        api_key=api_key,
        api_key_public_id=api_key_public_id or (api_key.public_id if api_key else ""),
        message=message,
    )
    logger.info(
        "api_key_audit",
        extra={
            "audit_action": action,
            "audit_actor_id": getattr(actor, "pk", None),
            "audit_api_key_public_id": api_key_public_id or (api_key.public_id if api_key else ""),
        },
    )
