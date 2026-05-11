from __future__ import annotations

import logging
import uuid
from typing import Callable

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse

from apps.observability.context import request_id_ctx

logger = logging.getLogger(__name__)


class RequestContextMiddleware:
    """Attach a stable request id for logs and downstream propagation."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        header_name = getattr(settings, "REQUEST_ID_HEADER", "X-Request-ID")
        incoming = request.headers.get(header_name)
        rid = incoming or str(uuid.uuid4())
        token = request_id_ctx.set(rid)
        try:
            response = self.get_response(request)
        finally:
            request_id_ctx.reset(token)

        if isinstance(response, HttpResponse):
            response.headers["X-Request-ID"] = rid
        return response


class BodySizeLimitMiddleware:
    """Reject oversized bodies using Content-Length (does not stream-read the body)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        max_bytes = int(getattr(settings, "INFERENCE_MAX_REQUEST_BODY_BYTES", 2_000_000))
        length = request.META.get("CONTENT_LENGTH")
        if length is None:
            return self.get_response(request)
        try:
            n = int(length)
        except ValueError:
            return self.get_response(request)
        if n > max_bytes:
            logger.warning("request_body_too_large", extra={"content_length": n, "max": max_bytes})
            return JsonResponse(
                {"error": {"message": "Request body too large", "type": "invalid_request_error"}},
                status=413,
            )
        return self.get_response(request)


class SecurityHeadersMiddleware:
    """Defense-in-depth headers (TLS-aware via settings)."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        _ = request
        response = self.get_response(request)
        if not isinstance(response, HttpResponse):
            return response

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

        if getattr(settings, "ENABLE_CROSS_ORIGIN_OPENER_POLICY", False):
            response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")

        return response
