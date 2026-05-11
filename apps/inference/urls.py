from __future__ import annotations

from django.urls import path

from apps.inference.views import programmatic_chat_completions
from apps.inference.views_models import programmatic_models_list

app_name = "inference"

urlpatterns = [
    path("chat/completions", programmatic_chat_completions, name="chat_completions"),
    path("models", programmatic_models_list, name="models_list"),
]
