from django.urls import path

from . import views

app_name = "agents"

urlpatterns = [
    path("", views.AgentListView.as_view(), name="list"),
    path("create/", views.AgentCreateView.as_view(), name="create"),
    path("results/", views.AgentResultsView.as_view(), name="results"),
    path("telegram/", views.TelegramConfigView.as_view(), name="telegram"),
    path("scheduler/status", views.scheduler_status, name="scheduler_status"),
    path("<uuid:agent_id>/", views.AgentDetailView.as_view(), name="detail"),
    path("<uuid:agent_id>/edit", views.AgentUpdateView.as_view(), name="edit"),
    path("<uuid:agent_id>/run", views.agent_run_now, name="run_now"),
    path("<uuid:agent_id>/toggle", views.agent_toggle_enabled, name="toggle_enabled"),
    path("<uuid:agent_id>/delete", views.agent_delete, name="delete"),
    path("<uuid:agent_id>/runs/<uuid:run_id>/", views.agent_run_detail, name="run_detail"),
    path("api/agents", views.api_agent_list, name="api_agent_list"),
    path("api/agents/<uuid:agent_id>/runs", views.api_agent_runs, name="api_agent_runs"),
    path("api/agents/<uuid:agent_id>/run/latest", views.api_agent_latest_run, name="api_agent_latest_run"),
    path("api/agents/<uuid:agent_id>/results", views.api_agent_results, name="api_agent_results"),
]
