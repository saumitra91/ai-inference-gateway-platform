"""Orchestrates chat completions: validation, generation controls, auth, quotas, upstream proxy, logging, metrics."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any, Literal

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser
from django.http import HttpRequest, HttpResponse, JsonResponse, StreamingHttpResponse
from pydantic import ValidationError

from apps.api_keys.models import APIKey
from apps.api_keys.services.limits import (
    check_user_daily_quota,
    consume_rate_limit,
    record_user_quota_success,
)
from apps.inference.exceptions import (
    UpstreamHTTPError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from apps.inference.metrics import (
    ACTIVE_INFERENCE_REQUESTS,
    CHAT_COMPLETION_ERRORS,
    CHAT_COMPLETIONS_NONSTREAMING,
    CHAT_COMPLETIONS_STREAMING,
    CHAT_REQUESTS,
    CLAMPED_REQUESTS,
    MAX_TOKENS_REQUESTED,
    QUOTA_EXCEEDED,
    RATE_LIMIT_EXCEEDED,
    REJECTED_OVERLOAD,
    REJECTED_REQUESTS,
    STREAM_TOKENS,
    STREAMING_DURATION_SECONDS,
    STREAMING_IN_FLIGHT,
    TTFT_SECONDS,
    UPSTREAM_LATENCY_SECONDS,
    UPSTREAM_TIMEOUTS,
    VALIDATION_ERRORS,
)
from apps.inference.schemas import ChatCompletionRequest
from apps.inference.services.concurrency import acquire as acquire_slot
from apps.inference.services.concurrency import release as release_slot
from apps.inference.services.llama_cpp import LlamaCppBackend
from apps.inference.services.vllm import VLLMBackend
from apps.inference.services.prompt_stats import prompt_char_length, rough_token_estimate_from_chars
from apps.inference.services.request_log import (
    maybe_debug_full_prompt,
    persist_inference_request_log,
    serialize_messages_for_preview,
)
from apps.inference.services.usage import bump_api_key_usage
from apps.observability.context import get_request_id
from apps.observability.redaction import preview_from_messages

logger = logging.getLogger(__name__)

Mode = Literal["programmatic", "ui"]


def _openai_error(*, message: str, type_: str, code: str | None = None, status: int = 400) -> JsonResponse:
    payload: dict[str, Any] = {"error": {"message": message, "type": type_}}
    if code is not None:
        payload["error"]["code"] = code
    return JsonResponse(payload, status=status)


class ChatCompletionService:
    """Inference control-plane entrypoint — keeps views thin and llama.cpp integration swappable."""

    def __init__(
        self,
        *,
        request: HttpRequest,
        mode: Mode,
        actor_user: AbstractBaseUser,
        api_key: APIKey | None,
    ) -> None:
        self.request = request
        self.mode = mode
        self.actor_user = actor_user
        self.api_key = api_key

    async def handle(self, raw_body: bytes) -> HttpResponse:
        rid = get_request_id() or "-"
        model_name = ""
        stream_flag = False

        # ── 1. Empty body check ────────────────────────────────────────
        if not raw_body:
            VALIDATION_ERRORS.labels(kind="empty_body").inc()
            await self._log_failure(
                request_id=rid, status=400, latency_ms=0, stream=False,
                model="", prompt_chars=0, prompt_tok_est=0, completion_tok=0,
                preview="", full_prompt="", error_kind="empty_body",
            )
            return _openai_error(message="Empty body", type_="invalid_request_error", status=400)

        # ── 2. JSON parse + Pydantic validation ────────────────────────
        try:
            req = ChatCompletionRequest.model_validate_json(raw_body)
        except json.JSONDecodeError as exc:
            VALIDATION_ERRORS.labels(kind="malformed_json").inc()
            await self._log_failure(
                request_id=rid, status=400, latency_ms=0, stream=False,
                model="", prompt_chars=0, prompt_tok_est=0, completion_tok=0,
                preview="", full_prompt="", error_kind="malformed_json",
            )
            return _openai_error(
                message=f"Malformed JSON: {exc.args[0]}" if exc.args else "Malformed JSON",
                type_="invalid_request_error", status=400,
            )
        except ValidationError as exc:
            VALIDATION_ERRORS.labels(kind="schema_validation").inc()
            errors = exc.errors()
            first_msg = errors[0]["msg"] if errors else "Validation failed"
            await self._log_failure(
                request_id=rid, status=400, latency_ms=0, stream=False,
                model="", prompt_chars=0, prompt_tok_est=0, completion_tok=0,
                preview="", full_prompt="", error_kind="validation_error",
            )
            return _openai_error(message=first_msg, type_="invalid_request_error", status=400)

        model_name = req.model
        stream_flag = bool(req.stream)

        # ── 3. Prompt size guard (before any work) ─────────────────────
        pchars = prompt_char_length(req)
        max_prompt_chars = getattr(settings, "INFERENCE_MAX_PROMPT_CHARS", 100_000)
        if pchars > max_prompt_chars:
            REJECTED_REQUESTS.labels(reason="prompt_too_long").inc()
            logger.warning("prompt_too_long", extra={
                "request_id": rid, "prompt_chars": pchars, "max_chars": max_prompt_chars,
            })
            await self._log_failure(
                request_id=rid, status=413,
                latency_ms=0, stream=stream_flag,
                model=model_name, prompt_chars=pchars,
                prompt_tok_est=rough_token_estimate_from_chars(pchars),
                completion_tok=0, preview="", full_prompt="",
                error_kind="prompt_too_long",
            )
            return _openai_error(
                message=f"Prompt exceeds maximum length of {max_prompt_chars} characters",
                type_="invalid_request_error", status=413,
            )

        ptok_est = rough_token_estimate_from_chars(pchars)
        preview = preview_from_messages(serialize_messages_for_preview(req.messages))
        full_prompt_dbg = maybe_debug_full_prompt(req.messages)

        # ── 4. Apply server-side defaults + clamping ───────────────────
        original_max_tokens = req.max_tokens
        original_temperature = req.temperature
        original_top_p = req.top_p

        req.apply_defaults_and_clamp()

        if original_max_tokens is not None and original_max_tokens != req.max_tokens:
            CLAMPED_REQUESTS.labels(field="max_tokens").inc()
            logger.info("clamped_max_tokens", extra={
                "request_id": rid, "requested": original_max_tokens, "clamped_to": req.max_tokens,
            })
        if original_temperature is not None and original_temperature != req.temperature:
            CLAMPED_REQUESTS.labels(field="temperature").inc()
        if original_top_p is not None and original_top_p != req.top_p:
            CLAMPED_REQUESTS.labels(field="top_p").inc()

        MAX_TOKENS_REQUESTED.observe(req.max_tokens)

        CHAT_REQUESTS.labels(mode=self.mode).inc()

        # ── 5. Rate limit check ────────────────────────────────────────
        if self.api_key is not None:
            rl = await sync_to_async(consume_rate_limit)(api_key=self.api_key)
            if not rl.allowed:
                RATE_LIMIT_EXCEEDED.inc()
                await self._log_failure(
                    request_id=rid, status=429, latency_ms=0,
                    stream=stream_flag, model=model_name,
                    prompt_chars=pchars, prompt_tok_est=ptok_est,
                    completion_tok=0, preview=preview,
                    full_prompt=full_prompt_dbg, error_kind="rate_limited",
                )
                return _openai_error(message="Rate limit exceeded", type_="rate_limit_error", status=429)

        # ── 6. Daily quota check ───────────────────────────────────────
        pessimistic_completion_budget = max(256, ptok_est)
        quota = await sync_to_async(check_user_daily_quota)(
            user=self.actor_user,
            prompt_tokens=ptok_est,
            completion_tokens=pessimistic_completion_budget,
        )
        if not quota.allowed:
            QUOTA_EXCEEDED.inc()
            await self._log_failure(
                request_id=rid, status=429, latency_ms=0,
                stream=stream_flag, model=model_name,
                prompt_chars=pchars, prompt_tok_est=ptok_est,
                completion_tok=0, preview=preview,
                full_prompt=full_prompt_dbg,
                error_kind=quota.reason or "quota_exceeded",
            )
            return _openai_error(message="Quota exceeded", type_="rate_limit_error", status=429)

        # ── 7. Concurrency slot ────────────────────────────────────────
        _acquired = False
        wait_time = await acquire_slot(request_id=rid)
        if wait_time is None:
            REJECTED_OVERLOAD.inc()
            await self._log_failure(
                request_id=rid, status=503, latency_ms=0,
                stream=False, model=model_name,
                prompt_chars=pchars, prompt_tok_est=ptok_est,
                completion_tok=0, preview=preview,
                full_prompt=full_prompt_dbg,
                error_kind="overloaded",
            )
            return _openai_error(
                message="Server is at capacity, try again later",
                type_="overload_error", status=503,
            )
        _acquired = True

        # ── 8. Backend selection ────────────────────────────────────────
        MODEL_TO_BACKEND = {"llama-local": "llamacpp", "llama-vllm": "vllm"}
        raw_backend: str | None = getattr(req, "backend", None)
        if not raw_backend:
            raw_backend = MODEL_TO_BACKEND.get(req.model, None)
        if not raw_backend:
            raw_backend = getattr(settings, "DEFAULT_INFERENCE_BACKEND", "llamacpp")
        if raw_backend not in ("llamacpp", "vllm"):
            raw_backend = "llamacpp"
        chosen_backend: str = raw_backend

        try:
            if chosen_backend == "vllm":
                backend: LlamaCppBackend | VLLMBackend = VLLMBackend()
            else:
                backend = LlamaCppBackend()
            extra_headers: dict[str, str] = {}
            if rid and rid != "-":
                extra_headers["X-Request-ID"] = rid

            ACTIVE_INFERENCE_REQUESTS.labels(mode=self.mode).inc()

            if req.stream:
                CHAT_COMPLETIONS_STREAMING.inc()
                _acquired = False  # _stream_sse owns release
                return StreamingHttpResponse(
                    self._stream_sse(
                        backend=backend,
                        req=req,
                        request_id=rid,
                        extra_headers=extra_headers,
                        prompt_chars=pchars,
                        prompt_tok_est=ptok_est,
                        preview=preview,
                        full_prompt_dbg=full_prompt_dbg,
                        wait_time=wait_time,
                    ),
                    content_type="text/event-stream; charset=utf-8",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )

            CHAT_COMPLETIONS_NONSTREAMING.inc()
            _acquired = False  # _handle_nonstreaming owns release
            return await self._handle_nonstreaming(
                backend=backend, req=req, rid=rid,
                extra_headers=extra_headers,
                pchars=pchars, ptok_est=ptok_est,
                preview=preview, full_prompt_dbg=full_prompt_dbg,
                wait_time=wait_time,
            )
        finally:
            if _acquired:
                release_slot()

    async def _handle_nonstreaming(
        self,
        *,
        backend: LlamaCppBackend,
        req: ChatCompletionRequest,
        rid: str,
        extra_headers: dict[str, str],
        pchars: int,
        ptok_est: int,
        preview: str,
        full_prompt_dbg: str,
        wait_time: float | None,
    ) -> HttpResponse:
        start = time.perf_counter()
        status = 200
        completion_tokens = 0
        error_kind = ""
        body = b""
        try:
            try:
                body = await backend.chat_completion(req, extra_headers=extra_headers)
                try:
                    parsed = json.loads(body.decode("utf-8"))
                    usage = parsed.get("usage") if isinstance(parsed, dict) else None
                    if isinstance(usage, dict):
                        completion_tokens = int(usage.get("completion_tokens") or 0)
                        ptok_usage = int(usage.get("prompt_tokens") or 0)
                        if ptok_usage > 0:
                            ptok_est = ptok_usage
                    elif isinstance(parsed, dict):
                        text = parsed.get("choices", [{}])[0].get("message", {}).get("content") or ""
                        completion_tokens = rough_token_estimate_from_chars(len(text))
                except Exception:
                    body_len = len(body)
                    completion_tokens = max(1, body_len // 200) if body_len > 0 else 1
            except UpstreamTimeoutError:
                UPSTREAM_TIMEOUTS.inc()
                CHAT_COMPLETION_ERRORS.labels(kind="upstream_timeout").inc()
                status = 504
                error_kind = "upstream_timeout"
                UPSTREAM_LATENCY_SECONDS.observe(time.perf_counter() - start)
                await self._persist_success_log(
                    request_id=rid, status=504,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    stream_duration_ms=None, ttft_ms=None,
                    model=req.model, stream=False,
                    prompt_chars=pchars, prompt_tok_est=ptok_est,
                    completion_tokens=0, preview=preview,
                    full_prompt_dbg=full_prompt_dbg,
                    error_kind=error_kind,
                )
                return _openai_error(
                    message="Upstream inference timed out",
                    type_="api_error", status=504,
                )
            except UpstreamUnavailableError as exc:
                CHAT_COMPLETION_ERRORS.labels(kind="upstream_unavailable").inc()
                logger.warning("upstream_unavailable", extra={"error": str(exc)})
                status = 502
                error_kind = "upstream_unavailable"
                UPSTREAM_LATENCY_SECONDS.observe(time.perf_counter() - start)
                await self._persist_success_log(
                    request_id=rid, status=502,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    stream_duration_ms=None, ttft_ms=None,
                    model=req.model, stream=False,
                    prompt_chars=pchars, prompt_tok_est=ptok_est,
                    completion_tokens=0, preview=preview,
                    full_prompt_dbg=full_prompt_dbg,
                    error_kind=error_kind,
                )
                return _openai_error(message="Upstream inference unavailable", type_="api_error", status=502)
            except UpstreamHTTPError as exc:
                CHAT_COMPLETION_ERRORS.labels(kind="upstream_http").inc()
                status = exc.status_code
                error_kind = "upstream_http"
                UPSTREAM_LATENCY_SECONDS.observe(time.perf_counter() - start)
                await self._persist_success_log(
                    request_id=rid, status=status,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    stream_duration_ms=None, ttft_ms=None,
                    model=req.model, stream=False,
                    prompt_chars=pchars, prompt_tok_est=ptok_est,
                    completion_tokens=0, preview=preview,
                    full_prompt_dbg=full_prompt_dbg,
                    error_kind=error_kind,
                )
                return HttpResponse(exc.body, status=exc.status_code, content_type="application/json")

            wall = time.perf_counter() - start
            UPSTREAM_LATENCY_SECONDS.observe(wall)
            await self._finalize_success_metrics(
                stream=False, wall_s=wall, ttft_s=wall,
                stream_s=None, completion_tokens=completion_tokens,
            )

            await self._persist_success_log(
                request_id=rid, status=status,
                latency_ms=int(wall * 1000),
                stream_duration_ms=None, ttft_ms=int(wall * 1000),
                model=req.model, stream=False,
                prompt_chars=pchars, prompt_tok_est=ptok_est,
                completion_tokens=completion_tokens,
                preview=preview, full_prompt_dbg=full_prompt_dbg,
                error_kind=error_kind,
            )

            await self._post_success_hooks(ptok_est=ptok_est, completion_tokens=completion_tokens)

            logger.info("nonstreaming_complete", extra={
                "request_id": rid, "model": req.model,
                "status": status, "latency_ms": int(wall * 1000),
                "prompt_tokens": ptok_est, "completion_tokens": completion_tokens,
                "stream": False, "queue_wait_ms": int(wait_time * 1000) if wait_time else 0,
            })

            return HttpResponse(body, content_type="application/json")
        finally:
            ACTIVE_INFERENCE_REQUESTS.labels(mode=self.mode).dec()
            release_slot()

    async def _stream_sse(
        self,
        *,
        backend: LlamaCppBackend,
        req: ChatCompletionRequest,
        request_id: str,
        extra_headers: dict[str, str],
        prompt_chars: int,
        prompt_tok_est: int,
        preview: str,
        full_prompt_dbg: str,
        wait_time: float | None,
    ) -> AsyncIterator[bytes]:
        STREAMING_IN_FLIGHT.inc()
        start = time.perf_counter()
        first: float | None = None
        counter = _SseTokenCounter()
        status = 200
        error_kind = ""
        timed_out = False

        try:
            async for chunk in backend.stream_chat_completion(req, extra_headers=extra_headers):
                if first is None and chunk:
                    first = time.perf_counter()
                counter.feed(chunk)
                yield chunk
        except asyncio.CancelledError:
            error_kind = "client_disconnected"
            logger.info("stream_cancelled", extra={"request_id": request_id})
            raise
        except UpstreamTimeoutError:
            UPSTREAM_TIMEOUTS.inc()
            CHAT_COMPLETION_ERRORS.labels(kind="upstream_timeout").inc()
            error_kind = "upstream_timeout"
            timed_out = True
            yield _sse_error_chunk("Upstream inference timed out")
        except UpstreamUnavailableError as exc:
            CHAT_COMPLETION_ERRORS.labels(kind="upstream_unavailable").inc()
            logger.warning("upstream_unavailable", extra={"error": str(exc)})
            error_kind = "upstream_unavailable"
            yield _sse_error_chunk("Upstream inference unavailable")
        except UpstreamHTTPError as exc:
            CHAT_COMPLETION_ERRORS.labels(kind="upstream_http").inc()
            error_kind = "upstream_http"
            yield _sse_error_bytes(exc.body, status_code=exc.status_code)
        finally:
            end = time.perf_counter()
            wall = end - start
            STREAMING_IN_FLIGHT.dec()
            ACTIVE_INFERENCE_REQUESTS.labels(mode=self.mode).dec()
            UPSTREAM_LATENCY_SECONDS.observe(wall)
            STREAMING_DURATION_SECONDS.observe(wall)
            release_slot()

            completion_tokens = counter.completion_token_estimate()
            await self._persist_success_log(
                request_id=request_id, status=status,
                latency_ms=int(wall * 1000),
                stream_duration_ms=int(wall * 1000),
                ttft_ms=int((first - start) * 1000) if first is not None else None,
                model=req.model, stream=True,
                prompt_chars=prompt_chars,
                prompt_tok_est=prompt_tok_est,
                completion_tokens=completion_tokens,
                preview=preview, full_prompt_dbg=full_prompt_dbg,
                error_kind=error_kind,
            )
            await self._finalize_success_metrics(
                stream=True,
                wall_s=wall,
                ttft_s=(first - start) if first is not None else None,
                stream_s=wall,
                completion_tokens=completion_tokens,
            )
            if error_kind == "" and not timed_out:
                await self._post_success_hooks(ptok_est=prompt_tok_est, completion_tokens=completion_tokens)

            logger.info("streaming_complete", extra={
                "request_id": request_id, "model": req.model,
                "status": status, "latency_ms": int(wall * 1000),
                "stream_duration_ms": int(wall * 1000),
                "ttft_ms": int((first - start) * 1000) if first is not None else None,
                "prompt_tokens": prompt_tok_est,
                "completion_tokens": completion_tokens,
                "stream": True, "error_kind": error_kind,
                "queue_wait_ms": int(wait_time * 1000) if wait_time else 0,
            })

    async def _finalize_success_metrics(
        self,
        *,
        stream: bool,
        wall_s: float,
        ttft_s: float | None,
        stream_s: float | None,
        completion_tokens: int,
    ) -> None:
        _ = stream_s
        if completion_tokens > 0:
            STREAM_TOKENS.labels(kind="completion").inc(max(0, int(completion_tokens)))
        if ttft_s is not None:
            TTFT_SECONDS.observe(ttft_s)

    async def _post_success_hooks(self, *, ptok_est: int, completion_tokens: int) -> None:
        await sync_to_async(record_user_quota_success)(
            user=self.actor_user,
            prompt_tokens=ptok_est,
            completion_tokens=completion_tokens,
        )
        if self.api_key is not None:
            await bump_api_key_usage(
                api_key=self.api_key,
                prompt_tokens=ptok_est,
                completion_tokens=completion_tokens,
            )

    async def _persist_success_log(
        self,
        *,
        request_id: str,
        status: int,
        latency_ms: int,
        stream_duration_ms: int | None,
        ttft_ms: int | None,
        model: str,
        stream: bool,
        prompt_chars: int,
        prompt_tok_est: int,
        completion_tokens: int,
        preview: str,
        full_prompt_dbg: str,
        error_kind: str,
    ) -> None:
        await persist_inference_request_log(
            request_id=request_id,
            user=self.actor_user,
            api_key=self.api_key,
            model_name=model,
            stream=stream,
            status_code=status,
            latency_ms=latency_ms,
            stream_duration_ms=stream_duration_ms,
            ttft_ms=ttft_ms,
            prompt_char_length=prompt_chars,
            prompt_token_estimate=prompt_tok_est,
            completion_tokens=completion_tokens,
            preview=preview,
            full_prompt=full_prompt_dbg,
            error_kind=error_kind,
        )

    async def _log_failure(
        self,
        *,
        request_id: str,
        status: int,
        latency_ms: int,
        stream: bool,
        model: str,
        prompt_chars: int,
        prompt_tok_est: int,
        completion_tok: int,
        preview: str,
        full_prompt: str,
        error_kind: str,
    ) -> None:
        await persist_inference_request_log(
            request_id=request_id,
            user=self.actor_user,
            api_key=self.api_key,
            model_name=model or "unknown",
            stream=stream,
            status_code=status,
            latency_ms=latency_ms,
            stream_duration_ms=None,
            ttft_ms=None,
            prompt_char_length=prompt_chars,
            prompt_token_estimate=prompt_tok_est,
            completion_tokens=completion_tok,
            preview=preview,
            full_prompt=full_prompt,
            error_kind=error_kind,
        )


class _SseTokenCounter:
    def __init__(self) -> None:
        self._buf = ""
        self._completion_chars = 0

    def feed(self, chunk: bytes) -> None:
        self._buf += chunk.decode("utf-8", errors="ignore")
        while "\n\n" in self._buf:
            evt, self._buf = self._buf.split("\n\n", 1)
            self._consume_event(evt)

    def _consume_event(self, evt: str) -> None:
        for line in evt.split("\n"):
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                usage = obj.get("usage")
                if isinstance(usage, dict):
                    ct = usage.get("completion_tokens")
                    if isinstance(ct, int) and ct > 0:
                        self._completion_chars = max(self._completion_chars, ct * 4)
                for choice in obj.get("choices") or []:
                    if not isinstance(choice, dict):
                        continue
                    delta = choice.get("delta")
                    if isinstance(delta, dict):
                        c = delta.get("content")
                        if isinstance(c, str):
                            self._completion_chars += len(c)

    def completion_token_estimate(self) -> int:
        return max(0, int(self._completion_chars // 4))


def _sse_error_chunk(message: str) -> bytes:
    payload = {"error": {"message": message, "type": "api_error"}}
    return f"data: {json.dumps(payload)}\n\n".encode()


def _sse_error_bytes(body: bytes, status_code: int) -> bytes:
    try:
        parsed = json.loads(body.decode("utf-8"))
        wrapped = {"error": parsed.get("error", {"message": body.decode("utf-8"), "type": "upstream_error"})}
    except json.JSONDecodeError:
        wrapped = {"error": {"message": body.decode("utf-8", errors="replace"), "type": "upstream_error"}}
    wrapped["error"]["http_status"] = status_code
    return f"data: {json.dumps(wrapped)}\n\n".encode()
