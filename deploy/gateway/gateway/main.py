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

from gateway.config import Settings
from gateway.crypto_auth import APIKeyContext, touch_api_key_used, verify_bearer_token
from gateway.limits import consume_rate_limit

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
UPSTREAM_SECONDS = Histogram(
    "gateway_upstream_seconds",
    "Upstream llama-server wall time",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600),
)
STREAM_BYTES = Counter("gateway_stream_bytes_total", "Bytes streamed from upstream to clients")


class _State:
    pool: asyncpg.Pool
    redis: Redis
    http: httpx.AsyncClient


state = _State()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ = app
    state.pool = await asyncpg.create_pool(settings.dsn_asyncpg(), min_size=1, max_size=10)
    state.redis = redis_from_url(settings.redis_url, decode_responses=False)
    state.http = httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=600.0, write=60.0, pool=5.0))
    log.info("level=info event=gateway_startup upstream=%s", settings.upstream_llama_url)
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

    REQUESTS.labels(route="ready", status="200").inc()
    return JSONResponse({"status": "ready", "checks": checks})


@app.get("/metrics")
async def metrics() -> Response:
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
        log.warning("level=warn event=rate_limited request_id=%s api_key_id=%s", rid, str(ctx.id))
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    try:
        payload = json.loads(body.decode("utf-8"))
        stream = bool(payload.get("stream")) if isinstance(payload, dict) else False
        model_name = str(payload.get("model", "default")) if isinstance(payload, dict) else "default"
    except Exception:
        stream = False
        model_name = "default"

    url = f"{settings.upstream_llama_url.rstrip('/')}/v1/chat/completions"
    headers = _upstream_headers(request)

    if stream:

        async def event_stream() -> AsyncIterator[bytes]:
            t0 = time.perf_counter()
            ttft: float | None = None
            total_bytes = 0
            status = 200
            err = ""
            try:
                async with state.http.stream("POST", url, content=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        status = resp.status_code
                        err = "upstream_http"
                        err_body = await resp.aread()
                        yield err_body
                        return
                    async for chunk in resp.aiter_bytes():
                        if ttft is None and chunk:
                            ttft = time.perf_counter()
                        total_bytes += len(chunk)
                        STREAM_BYTES.inc(len(chunk))
                        yield chunk
            except httpx.RequestError as exc:
                status = 502
                err = "upstream_unavailable"
                log.error("level=error event=upstream_error request_id=%s error=%s", rid, exc)
                yield (
                    f'data: {json.dumps({"error": {"message": "Upstream unavailable", "type": "api_error"}})}\n\n'
                ).encode()
            finally:
                elapsed = time.perf_counter() - t0
                UPSTREAM_SECONDS.observe(elapsed)
                ttft_ms = int((ttft - t0) * 1000) if ttft else None
                est_tps = (total_bytes / max(elapsed, 1e-9)) / 4.0
                log.info(
                    "level=info event=request_complete request_id=%s route=chat stream=true "
                    "latency_ms=%d stream_duration_ms=%d ttft_ms=%s bytes=%d est_tokens_per_sec=%.2f "
                    "api_key_id=%s status=%d error_kind=%s",
                    rid,
                    int(elapsed * 1000),
                    int(elapsed * 1000),
                    ttft_ms,
                    total_bytes,
                    est_tps,
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
                        completion_tokens=max(0, int(total_bytes // 4)),
                        preview=preview,
                        error_kind=err,
                    ),
                )

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream; charset=utf-8",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    t0 = time.perf_counter()
    status = 200
    err = ""
    try:
        resp = await state.http.post(url, content=body, headers=headers)
        status = resp.status_code
        out = resp.content
        if status >= 400:
            err = "upstream_http"
        ctok_final = 0
        try:
            parsed = json.loads(out.decode("utf-8"))
            usage = parsed.get("usage") if isinstance(parsed, dict) else None
            if isinstance(usage, dict):
                ctok_final = int(usage.get("completion_tokens") or 0)
        except Exception:
            ctok_final = 0
        if ctok_final == 0 and status < 400:
            ctok_final = max(1, len(out) // 200)
    except httpx.RequestError as exc:
        status = 502
        err = "upstream_unavailable"
        out = json.dumps({"error": {"message": "Upstream unavailable", "type": "api_error"}}).encode()
        log.error("level=error event=upstream_error request_id=%s error=%s", rid, exc)
        ctok_final = 0

    elapsed = time.perf_counter() - t0
    UPSTREAM_SECONDS.observe(elapsed)
    tps = (len(out) / max(elapsed, 1e-9)) / 200.0
    log.info(
        "level=info event=request_complete request_id=%s route=chat stream=false "
        "latency_ms=%d est_tokens_per_sec=%.2f api_key_id=%s status=%d error_kind=%s",
        rid,
        int(elapsed * 1000),
        tps,
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


@app.get("/v1/models")
async def list_models(request: Request, ctx: APIKeyDep) -> Response:
    rid = _rid(request)
    url = f"{settings.upstream_llama_url.rstrip('/')}/v1/models"
    headers = _upstream_headers(request)
    try:
        r = await state.http.get(url, headers=headers, timeout=30.0)
        REQUESTS.labels(route="models", status=str(r.status_code)).inc()
        log.info(
            "level=info event=request_complete request_id=%s route=models status=%d api_key_id=%s",
            rid,
            r.status_code,
            str(ctx.id),
        )
        return Response(content=r.content, status_code=r.status_code, media_type="application/json")
    except httpx.RequestError as exc:
        REQUESTS.labels(route="models", status="502").inc()
        log.error("level=error event=models_upstream_error request_id=%s error=%s", rid, exc)
        return JSONResponse({"error": {"message": "Upstream unavailable", "type": "api_error"}}, status_code=502)
