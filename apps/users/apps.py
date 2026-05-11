from __future__ import annotations

import logging

from django.apps import AppConfig
from django.contrib.auth import get_user_model
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)


def _create_user_profile(sender: object, instance: object, created: bool, **kwargs: object) -> None:
    _ = sender
    _ = kwargs
    if not created:
        return
    from apps.users.models import UserProfile

    try:
        UserProfile.objects.get_or_create(user=instance)  # type: ignore[arg-type]
    except Exception:
        logger.exception("user_profile_create_failed", extra={"user_id": getattr(instance, "pk", None)})


class UsersConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.users"
    verbose_name = "Users"

    def ready(self) -> None:
        User = get_user_model()
        post_save.connect(_create_user_profile, sender=User, dispatch_uid="apps.users.create_user_profile")
