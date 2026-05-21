from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

import asyncpg
import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from redis.asyncio import Redis
from redis.asyncio import from_url as redis_from_url

from gateway.backend_router import BackendName, backend_url, resolve_backend, strip_backend_field
from gateway.batcher import BatchBarrier
from gateway.concurrency import acquire_slot as acquire_concurrency_slot
from gateway.concurrency import release_slot as release_concurrency_slot
from gateway.config import Settings
from gateway.crypto_auth import APIKeyContext, touch_api_key_used, verify_bearer_token
from gateway.limits import consume_rate_limit
from gateway.runtime_metrics import collect_runtime_metrics
from gateway.metrics import (
    ACTIVE_INFERENCE_REQUESTS,
    BATCH_DISPATCH_COUNT,
    BATCH_EFFICIENCY,
    BATCH_QUEUE_DEPTH,
    BATCH_SIZE,
    BATCH_SINGLE_COUNT,
    BATCH_WAIT_SECONDS,
    BACKEND_ACTIVE_REQUESTS,
    BACKEND_BYTES_TOTAL,
    BACKEND_CHAT_COMPLETIONS_NONSTREAMING,
    BACKEND_CHAT_COMPLETIONS_STREAMING,
    BACKEND_CHAT_COMPLETION_ERRORS,
    BACKEND_CHAT_REQUESTS,
    BACKEND_REJECTED_OVERLOAD,
    BACKEND_STREAMING_DURATION_SECONDS,
    BACKEND_STREAMING_IN_FLIGHT,
    BACKEND_STREAM_TOKENS,
    BACKEND_TTFT_SECONDS,
    BACKEND_UPSTREAM_LATENCY_SECONDS,
    BACKEND_UPSTREAM_TIMEOUTS,
    CHAT_COMPLETIONS_NONSTREAMING,
    CHAT_COMPLETIONS_STREAMING,
    CHAT_COMPLETION_ERRORS,
    CHAT_REQUESTS,
    QUEUE_DEPTH,
    RATE_LIMIT_EXCEEDED,
    REJECTED_OVERLOAD,
    STREAMING_DURATION_SECONDS,
    STREAMING_IN_FLIGHT,
    STREAM_TOKENS,
    TTFT_SECONDS,
    UPSTREAM_LATENCY_SECONDS,
    UPSTREAM_TIMEOUTS,
)

settings = Settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(message)s",
)
log = logging.getLogger("gateway")

REQUESTS = Counter(
    "gateway_http_requests_total",
    "HTTP requests handled by the FastAPI gateway",
    labelnames=("route", "status"),
)


class _State:
    pool: asyncpg.Pool
    redis: Redis
    http: httpx.AsyncClient
    batcher: BatchBarrier


state = _State()

_BATCH_TOTAL_REQS: int = 0
_BATCH_TOTAL_DISPATCHES: int = 0


def _on_batch_flush(batch_size: int, wait_times: list[float]) -> None:
    """Callback invoked by BatchBarrier after each flush. Records metrics."""
    global _BATCH_TOTAL_REQS, _BATCH_TOTAL_DISPATCHES
    BATCH_DISPATCH_COUNT.inc()
    if batch_size == 1:
        BATCH_SINGLE_COUNT.inc()
    BATCH_SIZE.observe(batch_size)
    for wt in wait_times:
        BATCH_WAIT_SECONDS.observe(wt)

    _BATCH_TOTAL_REQS += batch_size
    _BATCH_TOTAL_DISPATCHES += 1
    if _BATCH_TOTAL_REQS > 0:
        efficiency = max(0.0, (_BATCH_TOTAL_REQS - _BATCH_TOTAL_DISPATCHES) / _BATCH_TOTAL_REQS)
        BATCH_EFFICIENCY.set(efficiency)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ = app
    state.pool = await asyncpg.create_pool(settings.dsn_asyncpg(), min_size=1, max_size=10)
    state.redis = redis_from_url(settings.redis_url, decode_responses=False)
    state.http = httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=600.0, write=60.0, pool=5.0))
    state.batcher = BatchBarrier(
        window_ms=settings.batch_window_ms,
        max_batch_size=settings.batch_max_size,
        on_flush=_on_batch_flush,
        queue_depth_gauge=BATCH_QUEUE_DEPTH,
    )
    log.info("level=info event=gateway_startup llama=%s vllm=%s default_backend=%s",
             settings.upstream_llama_url, settings.upstream_vllm_url, settings.default_backend)
    log.info(
        "level=info event=batch_config window_ms=%s max_batch_size=%s",
        settings.batch_window_ms,
        settings.batch_max_size,
    )
    yield
    await state.http.aclose()
    await state.redis.close()
    await state.pool.close()


app = FastAPI(title="Inference gateway", lifespan=lifespan)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    hdr = settings.request_id_header
    rid = request.headers.get(hdr) or str(uuid.uuid4())
    request.state.request_id = rid
    response = await call_next(request)
    response.headers[hdr] = rid
    return response


def _rid(request: Request) -> str:
    return getattr(request.state, "request_id", "-")


def _parse_bearer(authorization: str | None) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    return authorization.removeprefix("Bearer ").strip()


async def require_api_key(request: Request) -> APIKeyContext:
    token = _parse_bearer(request.headers.get("Authorization"))
    async with state.pool.acquire() as conn:
        ctx = await verify_bearer_token(
            conn=conn,
            token=token,
            settings_pepper=settings.api_key_hmac_pepper,
            settings_secret=settings.secret_key,
        )
    if ctx is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return ctx


APIKeyDep = Annotated[APIKeyContext, Depends(require_api_key)]


@app.get("/health")
async def health_live() -> dict[str, str]:
    REQUESTS.labels(route="health", status="200").inc()
    return {"status": "live", "service": "gateway"}


@app.get("/ready")
async def health_ready() -> JSONResponse:
    checks: dict[str, Any] = {}
    try:
        async with state.pool.acquire() as conn:
            await conn.execute("SELECT 1")
        checks["database"] = "ok"
    except Exception as exc:
        log.error("level=error event=ready_db_failed error=%s", exc)
        checks["database"] = "failed"
        REQUESTS.labels(route="ready", status="503").inc()
        return JSONResponse({"status": "not_ready", "checks": checks}, status_code=503)

    try:
        r = await state.http.get(f"{settings.upstream_llama_url.rstrip('/')}/health", timeout=5.0)
        checks["llamacpp"] = {"http": r.status_code}
        if r.status_code >= 500:
            REQUESTS.labels(route="ready", status="503").inc()
            return JSONResponse({"status": "not_ready", "checks": checks}, status_code=503)
    except Exception as exc:
        log.error("level=error event=ready_llama_failed error=%s", exc)
        checks["llamacpp"] = {"error": str(exc)}
        REQUESTS.labels(route="ready", status="503").inc()
        return JSONResponse({"status": "not_ready", "checks": checks}, status_code=503)

    try:
        r = await state.http.get(f"{settings.upstream_vllm_url.rstrip('/')}/health", timeout=5.0)
        checks["vllm"] = {"http": r.status_code}
    except Exception as exc:
        log.warning("level=warn event=ready_vllm_unavailable error=%s", exc)
        checks["vllm"] = {"error": str(exc), "available": False}

    REQUESTS.labels(route="ready", status="200").inc()
    return JSONResponse({"status": "ready", "checks": checks})


@app.get("/metrics")
async def metrics() -> Response:
    collect_runtime_metrics()
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


def _upstream_headers(request: Request) -> dict[str, str]:
    rid = _rid(request)
    h: dict[str, str] = {}
    if ct := request.headers.get("content-type"):
        h["content-type"] = ct
    h["accept"] = request.headers.get("accept") or "application/json"
    h[settings.request_id_header] = rid
    return h


def _rough_prompt_stats(body: bytes) -> tuple[int, int, str]:
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return 0, 0, ""
    msgs = data.get("messages") if isinstance(data, dict) else None
    if not isinstance(msgs, list):
        return 0, 0, ""
    total = 0
    parts: list[str] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        if isinstance(c, str):
            total += len(c)
            parts.append(f"{m.get('role', '')}: {c[:200]}")
    preview = "\n".join(parts)[:100]
    est = max(1, total // 4) if total else 0
    return total, est, preview


async def _persist_log(
    *,
    request_id: str,
    user_id: int | None,
    api_key_id: uuid.UUID | None,
    model_name: str,
    stream: bool,
    status_code: int,
    latency_ms: int,
    stream_duration_ms: int | None,
    ttft_ms: int | None,
    prompt_chars: int,
    prompt_tok_est: int,
    completion_tokens: int,
    preview: str,
    error_kind: str,
) -> None:
    if not settings.gateway_persist_logs:
        return
    sql = """
        INSERT INTO inference_inferencerequestlog (
            id, created_at, request_id, user_id, api_key_id, model_name, stream,
            status_code, latency_ms, stream_duration_ms, ttft_ms,
            prompt_char_length, prompt_token_estimate, completion_tokens,
            preview, full_prompt, error_kind
        ) VALUES (
            $1::uuid, NOW(), $2, $3, $4::uuid, $5, $6,
            $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16
        )
    """
    try:
        log_id = uuid.uuid4()
        async with state.pool.acquire() as conn:
            await conn.execute(
                sql,
                log_id,
                request_id,
                user_id,
                api_key_id,
                model_name,
                stream,
                status_code,
                latency_ms,
                stream_duration_ms,
                ttft_ms,
                prompt_chars,
                prompt_tok_est,
                completion_tokens,
                preview[:100],
                "",
                error_kind[:64],
            )
    except Exception as exc:
        log.error("level=error event=persist_log_failed error=%s", exc)


async def _touch_key(key_id: uuid.UUID) -> None:
    try:
        async with state.pool.acquire() as conn:
            await touch_api_key_used(conn, key_id)
    except Exception as exc:
        log.warning("level=warn event=touch_api_key_failed error=%s", exc)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request, ctx: APIKeyDep) -> Response:
    body = await request.body()
    rid = _rid(request)
    pchars, ptok_est, preview = _rough_prompt_stats(body)

    if not await consume_rate_limit(redis=state.redis, api_key_id=ctx.id, rpm=ctx.rate_limit_rpm):
        REQUESTS.labels(route="chat", status="429").inc()
        RATE_LIMIT_EXCEEDED.inc()
        log.warning("level=warn event=rate_limited request_id=%s api_key_id=%s", rid, str(ctx.id))
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    try:
        payload = json.loads(body.decode("utf-8"))
        stream = bool(payload.get("stream")) if isinstance(payload, dict) else False
        model_name = str(payload.get("model", "default")) if isinstance(payload, dict) else "default"
    except Exception:
        payload = None
        stream = False
        model_name = "default"

    # ── Backend resolution ────────────────────────────────────────────
    backend: BackendName = resolve_backend(
        payload=payload,
        headers=dict(request.headers),
        settings=settings,
    )
    upstream_base = backend_url(backend, settings)
    url = f"{upstream_base}/v1/chat/completions"

    # Strip backend field before forwarding upstream
    if payload and isinstance(payload, dict):
        cleaned = strip_backend_field(payload)
        body = json.dumps(cleaned).encode("utf-8")

    # ── Concurrency slot (backpressure) ─────────────────────────────────
    wait_time = await acquire_concurrency_slot(request_id=rid, settings=settings)
    if wait_time is None:
        REQUESTS.labels(route="chat", status="503").inc()
        REJECTED_OVERLOAD.inc()
        BACKEND_REJECTED_OVERLOAD.labels(backend=backend).inc()
        log.warning("level=warn event=concurrency_rejected request_id=%s backend=%s", rid, backend)
        raise HTTPException(status_code=503, detail="Server is at capacity, try again later")

    # ── Batch barrier (coordinate dispatch timing) ─────────────────────
    batch_wait = await state.batcher.wait()

    headers = _upstream_headers(request)

    if stream:

        async def event_stream() -> AsyncIterator[bytes]:
            CHAT_COMPLETIONS_STREAMING.inc()
            BACKEND_CHAT_COMPLETIONS_STREAMING.labels(backend=backend).inc()
            CHAT_REQUESTS.labels(mode="programmatic").inc()
            BACKEND_CHAT_REQUESTS.labels(backend=backend, mode="programmatic").inc()
            ACTIVE_INFERENCE_REQUESTS.labels(mode="programmatic").inc()
            BACKEND_ACTIVE_REQUESTS.labels(backend=backend, mode="programmatic").inc()
            STREAMING_IN_FLIGHT.inc()
            BACKEND_STREAMING_IN_FLIGHT.labels(backend=backend).inc()
            t0 = time.perf_counter()
            ttft: float | None = None
            total_bytes = 0
            est_tokens = 0
            status = 200
            err = ""
            try:
                async with state.http.stream("POST", url, content=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        status = resp.status_code
                        err = "upstream_http"
                        CHAT_COMPLETION_ERRORS.labels(kind="upstream_http").inc()
                        BACKEND_CHAT_COMPLETION_ERRORS.labels(backend=backend, kind="upstream_http").inc()
                        err_body = await resp.aread()
                        yield f"data: {err_body.decode()}\n\n".encode()
                        return
                    async for chunk in resp.aiter_bytes():
                        if ttft is None and chunk:
                            ttft = time.perf_counter()
                            TTFT_SECONDS.observe(ttft - t0)
                            BACKEND_TTFT_SECONDS.labels(backend=backend).observe(ttft - t0)
                        total_bytes += len(chunk)
                        BACKEND_BYTES_TOTAL.labels(backend=backend).inc(len(chunk))
                        yield chunk
            except asyncio.CancelledError:
                status = 499
                err = "client_disconnected"
                CHAT_COMPLETION_ERRORS.labels(kind="client_disconnected").inc()
                BACKEND_CHAT_COMPLETION_ERRORS.labels(backend=backend, kind="client_disconnected").inc()
                raise
            except httpx.ReadTimeout as exc:
                status = 504
                err = "upstream_timeout"
                UPSTREAM_TIMEOUTS.inc()
                BACKEND_UPSTREAM_TIMEOUTS.labels(backend=backend).inc()
                CHAT_COMPLETION_ERRORS.labels(kind="upstream_timeout").inc()
                BACKEND_CHAT_COMPLETION_ERRORS.labels(backend=backend, kind="upstream_timeout").inc()
                log.error("level=error event=upstream_timeout request_id=%s backend=%s error=%s", rid, backend, exc)
                yield (
                    f'data: {json.dumps({"error": {"message": "Upstream timed out", "type": "api_error"}})}\n\n'
                ).encode()
            except httpx.RequestError as exc:
                status = 502
                err = "upstream_unavailable"
                CHAT_COMPLETION_ERRORS.labels(kind="upstream_unavailable").inc()
                BACKEND_CHAT_COMPLETION_ERRORS.labels(backend=backend, kind="upstream_unavailable").inc()
                log.error("level=error event=upstream_error request_id=%s backend=%s error=%s", rid, backend, exc)
                yield (
                    f'data: {json.dumps({"error": {"message": "Upstream unavailable", "type": "api_error"}})}\n\n'
                ).encode()
            finally:
                try:
                    elapsed = time.perf_counter() - t0
                    UPSTREAM_LATENCY_SECONDS.observe(elapsed)
                    BACKEND_UPSTREAM_LATENCY_SECONDS.labels(backend=backend).observe(elapsed)
                    STREAMING_IN_FLIGHT.dec()
                    BACKEND_STREAMING_IN_FLIGHT.labels(backend=backend).dec()
                    ACTIVE_INFERENCE_REQUESTS.labels(mode="programmatic").dec()
                    BACKEND_ACTIVE_REQUESTS.labels(backend=backend, mode="programmatic").dec()
                    STREAMING_DURATION_SECONDS.observe(elapsed)
                    BACKEND_STREAMING_DURATION_SECONDS.labels(backend=backend).observe(elapsed)
                    est_tokens = max(0, int(total_bytes // 4))
                    if est_tokens > 0:
                        STREAM_TOKENS.labels(kind="completion").inc(est_tokens)
                        BACKEND_STREAM_TOKENS.labels(backend=backend, kind="completion").inc(est_tokens)
                    ttft_ms = int((ttft - t0) * 1000) if ttft else None
                    est_tps = (total_bytes / max(elapsed, 1e-9)) / 4.0
                    queue_wait_ms = int(wait_time * 1000) if wait_time else 0
                    batch_wait_ms = int(batch_wait * 1000) if batch_wait else 0
                    log.info(
                        "level=info event=request_complete request_id=%s route=chat backend=%s stream=true "
                        "latency_ms=%d stream_duration_ms=%d ttft_ms=%s bytes=%d est_tokens_per_sec=%.2f "
                        "queue_wait_ms=%d batch_wait_ms=%d api_key_id=%s status=%d error_kind=%s",
                        rid, backend,
                        int(elapsed * 1000),
                        int(elapsed * 1000),
                        ttft_ms,
                        total_bytes,
                        est_tps,
                        queue_wait_ms,
                        batch_wait_ms,
                        str(ctx.id),
                        status,
                        err,
                    )
                    REQUESTS.labels(route="chat", status=str(status)).inc()
                    asyncio.create_task(_touch_key(ctx.id))
                    asyncio.create_task(
                        _persist_log(
                            request_id=rid,
                            user_id=ctx.user_id,
                            api_key_id=ctx.id,
                            model_name=model_name,
                            stream=True,
                            status_code=status,
                            latency_ms=int(elapsed * 1000),
                            stream_duration_ms=int(elapsed * 1000),
                            ttft_ms=ttft_ms,
                            prompt_chars=pchars,
                            prompt_tok_est=ptok_est,
                            completion_tokens=est_tokens,
                            preview=preview,
                            error_kind=err,
                        ),
                    )
                finally:
                    release_concurrency_slot(settings)

        try:
            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream; charset=utf-8",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        except Exception:
            release_concurrency_slot(settings)
            raise

    try:
        t0 = time.perf_counter()
        CHAT_COMPLETIONS_NONSTREAMING.inc()
        BACKEND_CHAT_COMPLETIONS_NONSTREAMING.labels(backend=backend).inc()
        CHAT_REQUESTS.labels(mode="programmatic").inc()
        BACKEND_CHAT_REQUESTS.labels(backend=backend, mode="programmatic").inc()
        ACTIVE_INFERENCE_REQUESTS.labels(mode="programmatic").inc()
        BACKEND_ACTIVE_REQUESTS.labels(backend=backend, mode="programmatic").inc()
        status = 200
        err = ""
        ctok_final = 0
        out = b""
        try:
            resp = await state.http.post(url, content=body, headers=headers)
            status = resp.status_code
            out = resp.content
            if status >= 400:
                err = "upstream_http"
                CHAT_COMPLETION_ERRORS.labels(kind="upstream_http").inc()
                BACKEND_CHAT_COMPLETION_ERRORS.labels(backend=backend, kind="upstream_http").inc()
            try:
                parsed = json.loads(out.decode("utf-8"))
                usage = parsed.get("usage") if isinstance(parsed, dict) else None
                if isinstance(usage, dict):
                    ctok_final = int(usage.get("completion_tokens") or 0)
            except Exception:
                ctok_final = 0
            if ctok_final == 0 and status < 400:
                ctok_final = max(1, len(out) // 200)
        except httpx.ReadTimeout as exc:
            status = 504
            err = "upstream_timeout"
            UPSTREAM_TIMEOUTS.inc()
            BACKEND_UPSTREAM_TIMEOUTS.labels(backend=backend).inc()
            CHAT_COMPLETION_ERRORS.labels(kind="upstream_timeout").inc()
            BACKEND_CHAT_COMPLETION_ERRORS.labels(backend=backend, kind="upstream_timeout").inc()
            out = json.dumps({"error": {"message": "Upstream timed out", "type": "api_error"}}).encode()
            log.error("level=error event=upstream_timeout request_id=%s backend=%s error=%s", rid, backend, exc)
            ctok_final = 0
        except httpx.RequestError as exc:
            status = 502
            err = "upstream_unavailable"
            CHAT_COMPLETION_ERRORS.labels(kind="upstream_unavailable").inc()
            BACKEND_CHAT_COMPLETION_ERRORS.labels(backend=backend, kind="upstream_unavailable").inc()
            out = json.dumps({"error": {"message": "Upstream unavailable", "type": "api_error"}}).encode()
            log.error("level=error event=upstream_error request_id=%s backend=%s error=%s", rid, backend, exc)
            ctok_final = 0

        elapsed = time.perf_counter() - t0
        ACTIVE_INFERENCE_REQUESTS.labels(mode="programmatic").dec()
        BACKEND_ACTIVE_REQUESTS.labels(backend=backend, mode="programmatic").dec()
        UPSTREAM_LATENCY_SECONDS.observe(elapsed)
        BACKEND_UPSTREAM_LATENCY_SECONDS.labels(backend=backend).observe(elapsed)
        if status < 400:
            TTFT_SECONDS.observe(elapsed)
            BACKEND_TTFT_SECONDS.labels(backend=backend).observe(elapsed)
        if ctok_final > 0:
            STREAM_TOKENS.labels(kind="completion").inc(ctok_final)
            BACKEND_STREAM_TOKENS.labels(backend=backend, kind="completion").inc(ctok_final)
        tps = (len(out) / max(elapsed, 1e-9)) / 200.0
        queue_wait_ms = int(wait_time * 1000) if wait_time else 0
        batch_wait_ms = int(batch_wait * 1000) if batch_wait else 0
        log.info(
            "level=info event=request_complete request_id=%s route=chat backend=%s stream=false "
            "latency_ms=%d est_tokens_per_sec=%.2f queue_wait_ms=%d batch_wait_ms=%d api_key_id=%s status=%d error_kind=%s",
            rid, backend,
            int(elapsed * 1000),
            tps,
            queue_wait_ms,
            batch_wait_ms,
            str(ctx.id),
            status,
            err,
        )
        REQUESTS.labels(route="chat", status=str(status)).inc()
        asyncio.create_task(_touch_key(ctx.id))
        asyncio.create_task(
            _persist_log(
                request_id=rid,
                user_id=ctx.user_id,
                api_key_id=ctx.id,
                model_name=model_name,
                stream=False,
                status_code=status,
                latency_ms=int(elapsed * 1000),
                stream_duration_ms=None,
                ttft_ms=int(elapsed * 1000) if status < 400 else None,
                prompt_chars=pchars,
                prompt_tok_est=ptok_est,
                completion_tokens=ctok_final,
                preview=preview,
                error_kind=err,
            ),
        )
        return Response(content=out, status_code=status, media_type="application/json")
    finally:
        release_concurrency_slot(settings)


@app.get("/v1/models")
async def list_models(request: Request, ctx: APIKeyDep) -> Response:
    rid = _rid(request)
    headers = _upstream_headers(request)

    async def _fetch_models(base_url: str) -> list[dict[str, Any]]:
        try:
            r = await state.http.get(f"{base_url}/v1/models", headers=headers, timeout=15.0)
            if r.status_code == 200:
                data = r.json()
                return data.get("data", []) if isinstance(data, dict) else []
        except Exception:
            pass
        return []

    models_llama = await _fetch_models(settings.upstream_llama_url.rstrip("/"))
    models_vllm = await _fetch_models(settings.upstream_vllm_url.rstrip("/"))

    merged = models_llama + [m for m in models_vllm if m.get("id") not in {x.get("id") for x in models_llama}]

    payload = {"object": "list", "data": merged}
    REQUESTS.labels(route="models", status="200").inc()
    log.info(
        "level=info event=request_complete request_id=%s route=models llama_models=%d vllm_models=%d total=%d api_key_id=%s",
        rid, len(models_llama), len(models_vllm), len(merged), str(ctx.id),
    )
    return JSONResponse(content=payload)
