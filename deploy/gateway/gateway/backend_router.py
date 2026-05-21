from __future__ import annotations

import json
from typing import Any, Literal

from gateway.config import Settings

BackendName = Literal["llamacpp", "vllm"]

MODEL_TO_BACKEND: dict[str, BackendName] = {
    "llama-local": "llamacpp",
    "llama-vllm": "vllm",
    "vllm-model": "vllm",
}


def resolve_backend(
    payload: dict[str, Any] | None,
    headers: dict[str, str],
    settings: Settings,
) -> BackendName:
    if payload and isinstance(payload, dict):
        backend = payload.get("backend")
        if backend in ("llamacpp", "vllm"):
            return backend

    backend = headers.get("X-Inference-Backend")
    if backend in ("llamacpp", "vllm"):
        return backend

    if payload and isinstance(payload, dict):
        model = payload.get("model", "")
        if model in MODEL_TO_BACKEND:
            return MODEL_TO_BACKEND[model]

    return settings.default_backend or "llamacpp"


def backend_url(backend: BackendName, settings: Settings) -> str:
    if backend == "vllm":
        return settings.upstream_vllm_url.rstrip("/")
    return settings.upstream_llama_url.rstrip("/")


def strip_backend_field(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload)
    cleaned.pop("backend", None)
    return cleaned
