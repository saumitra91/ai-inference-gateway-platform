from __future__ import annotations

from django.urls import path

from apps.inference.views import ui_chat_completions

app_name = "inference_ui"

urlpatterns = [
    path("chat/completions", ui_chat_completions, name="ui_chat_completions"),
]
