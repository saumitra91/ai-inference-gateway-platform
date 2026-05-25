from django.apps import AppConfig


class AgentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.agents"
    verbose_name = "Agents (Research & Job Intelligence)"

    def ready(self):
        import apps.agents.metrics  # noqa: F401
