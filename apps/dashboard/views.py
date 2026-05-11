from __future__ import annotations

from typing import Any

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import FormView, TemplateView

from apps.api_keys.services.keys import create_api_key
from apps.dashboard.forms import APIKeyCreateForm


class HomeView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/home.html"

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        ctx = super().get_context_data(**kwargs)
        ctx["llama_cpp_base_url"] = getattr(settings, "LLAMA_CPP_BASE_URL", "")
        return ctx


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ChatView(LoginRequiredMixin, TemplateView):
    template_name = "dashboard/chat.html"


class StaffAPIKeyCreateView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    template_name = "dashboard/api_keys_create.html"
    form_class = APIKeyCreateForm

    def test_func(self) -> bool:
        return bool(self.request.user.is_staff)

    def form_valid(self, form: Any) -> HttpResponseRedirect:
        created = create_api_key(
            owner=self.request.user,
            actor=self.request.user,
            label=form.cleaned_data.get("label") or "",
            rate_limit_rpm=int(form.cleaned_data["rate_limit_rpm"]),
        )
        self.request.session["ephemeral_api_key"] = created.raw_key
        return HttpResponseRedirect(str(reverse_lazy("dashboard:api_keys_reveal")))


class StaffAPIKeyRevealView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "dashboard/api_keys_reveal.html"

    def test_func(self) -> bool:
        return bool(self.request.user.is_staff)

    def get(self, request: Any, *args: Any, **kwargs: Any) -> Any:
        if "ephemeral_api_key" not in request.session:
            return HttpResponseRedirect(str(reverse_lazy("dashboard:api_keys_create")))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs: object) -> dict[str, object]:
        ctx = super().get_context_data(**kwargs)
        ctx["raw_key"] = self.request.session.pop("ephemeral_api_key", None)
        return ctx
