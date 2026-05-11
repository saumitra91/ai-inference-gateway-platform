"""Legacy URL module — programmatic OpenAI routes are served by `deploy/gateway` behind nginx."""

from __future__ import annotations

from django.urls import path

app_name = "inference"

urlpatterns: list = []
