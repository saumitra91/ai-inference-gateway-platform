"""Local developer settings: permissive defaults, ASGI-friendly."""

from .base import *  # noqa: F403

DEBUG = True
ALLOWED_HOSTS = ["*"]
