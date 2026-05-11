from __future__ import annotations

from django.urls import path

from apps.observability.views import live_view, ready_view

app_name = "observability"

urlpatterns = [
    path("live", live_view, name="live"),
    path("ready", ready_view, name="ready"),
]
