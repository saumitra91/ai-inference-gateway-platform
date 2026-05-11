from __future__ import annotations

from django.urls import path

from apps.dashboard.views import (
    ChatView,
    HomeView,
    StaffAPIKeyCreateView,
    StaffAPIKeyRevealView,
)

app_name = "dashboard"

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("chat/", ChatView.as_view(), name="chat"),
    path("staff/api-keys/", StaffAPIKeyCreateView.as_view(), name="api_keys_create"),
    path("staff/api-keys/reveal/", StaffAPIKeyRevealView.as_view(), name="api_keys_reveal"),
]
