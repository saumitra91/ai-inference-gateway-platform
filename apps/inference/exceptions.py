from __future__ import annotations


class InferenceServiceError(Exception):
    """Base class for inference control-plane failures."""


class UpstreamUnavailableError(InferenceServiceError):
    """llama.cpp server unreachable or DNS/TCP failure."""


class UpstreamHTTPError(InferenceServiceError):
    def __init__(self, status_code: int, body: bytes) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"Upstream returned HTTP {status_code}")


class UpstreamTimeoutError(InferenceServiceError):
    """Upstream llama.cpp request exceeded the configured timeout."""
