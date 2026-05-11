"""Root URL configuration."""

from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from apps.observability.views import live_view, metrics_view, model_status_view, ready_view

urlpatterns = [
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html")),
    path("accounts/logout/", auth_views.LogoutView.as_view()),
    path("admin/", admin.site.urls),
    path("health/", include(("apps.observability.urls", "observability"), namespace="observability")),
    path("health", live_view, name="health"),
    path("ready", ready_view, name="ready"),
    path("metrics", metrics_view, name="metrics"),
    path("internal/model-status", model_status_view, name="model_status"),
    path("v1/", include(("apps.inference.urls", "inference"), namespace="inference")),
    path("ui/v1/", include(("apps.inference.ui_urls", "inference_ui"), namespace="inference_ui")),
    path("", include(("apps.dashboard.urls", "dashboard"), namespace="dashboard")),
]
