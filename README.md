# AI Inference Gateway Platform

Production-grade LLM inference serving platform with concurrency control,
backpressure, comprehensive observability, and OpenAI-compatible streaming APIs.

```
Client → nginx :8888 → FastAPI Gateway :8081 → llama.cpp :8080 → GGUF Model
                           ↓                          ↑
                     Concurrency Queue          SSE token stream
                     Request Batcher             data: {...}
                     Rate Limiter (Redis)       data: [DONE]
                     API Key Auth (Postgres)
                     Prometheus /metrics
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  L1  CLIENT LAYER          k6 │ curl │ OpenAI SDK                    │
├─────────────────────────────────────────────────────────────────────┤
│  L2  API LAYER              nginx :8888 │ FastAPI Gateway :8081      │
│                             ┌──────────────────────────────────┐    │
│                             │  Concurrency Queue                │    │
│                             │  Request Batcher (50ms window)    │    │
│                             │  Rate Limiter (Redis)             │    │
│                             │  API Key Auth (Postgres)          │    │
│                             │  Structured Logging               │    │
│                             │  Prometheus /metrics              │    │
│                             └──────────────────────────────────┘    │
│                             Django Control Plane :8000              │
├─────────────────────────────────────────────────────────────────────┤
│  L3  INFERENCE LAYER       llama.cpp :8080 │ GGUF Mode              │
│                             ┌──────────────────────────────────┐    │
│                             │  Prompt Cache (KV reuse)         │    │
│                             │  SSE Token Stream                │    │
│                             └──────────────────────────────────┘    │
│                             Redis :6379 │ PostgreSQL :5432          │
├─────────────────────────────────────────────────────────────────────┤
│  L4  OBSERVABILITY LAYER   Prometheus :9090 │ Grafana :3000         │
│                             ┌──────────────────────────────────┐    │
│                             │  23-panel dashboard              │    │
│                             │  18 Prometheus recording rules   │    │
│                             └──────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

### Request flow

```mermaid
flowchart LR
    subgraph Client
        K6[k6 / curl / SDK]
    end
    subgraph API["API Layer (Docker)"]
        NX["nginx :8888"]
        GW["FastAPI Gateway :8081"]
        CQ["Concurrency Queue"]
    end
    subgraph Inference["Inference Layer (Docker)"]
        LC["llama.cpp :8080"]
        GGUF["GGUF Model"]
    end
    subgraph Obs["Observability (Docker)"]
        PM["Prometheus :9090"]
        GR["Grafana :3000"]
    end

    K6 -->|"POST /v1/chat/completions"| NX
    NX -->|"proxy_pass"| GW
    GW -->|"acquire / queue / 503"| CQ
    CQ -->|"forward"| LC
    LC -->|"SSE token stream"| GW
    GW -->|"response"| NX
    NX -->|"response"| K6

    GW -.->|"/metrics scrape"| PM
    PM -.->|"datasource"| GR
```

### Streaming lifecycle

1. **Client** sends `POST /v1/chat/completions` with `stream: true` to `nginx :8888`
2. **nginx** routes to **FastAPI Gateway :8081** (`proxy_buffering off` for SSE)
3. **Gateway** verifies Bearer API key (HMAC-SHA256, Postgres lookup, timing-safe compare)
4. **Gateway** checks rate limit (Redis `incr` 60s sliding window, fail-open on Redis outage)
5. **Gateway** acquires concurrency slot (`asyncio.Semaphore(4)`, queue depth 10, 30s timeout)
6. **Gateway** enters batch barrier — waits for other concurrent requests (default 50ms window, max 8 per batch)
7. **Batch dispatches** — all accumulated requests are released simultaneously
8. **Gateway** proxies to `llama.cpp :8080` — httpx streaming `aiter_bytes()` (concurrent with batch peers)
9. **llama.cpp** streams SSE tokens back through the gateway (server-side slot batching improves prompt processing)
10. **Gateway** records metrics (TTFT, stream duration, token count, queue wait time, batch wait time)
11. **Gateway** persists audit log to Postgres (async `create_task`, 100-char redacted preview)
12. **Client** receives SSE chunks terminated by `data: [DONE]`

### Key design decisions

| Decision | Rationale |
|---|---|
| **Gateway single-worker ASGI** | Semaphore-based concurrency requires single event loop; multi-worker needs Redis distributed semaphore |
| **Gateway proxies directly to llama.cpp** | Django NOT in hot path — faster streaming, lower latency, independent scaling |
| **503 (not 429) for overload** | 503 signals server capacity exhaustion; 429 implies client is sending too fast |
| **Fail-open on Redis outage** | Availability over strict rate enforcement; rate limiting is operational protection, not security |
| **Prometheus in-process** | Lowest friction for internal platform; `/metrics` is standard pattern |
| **Queue tracking via plain int** | Atomic between `await` points in asyncio cooperative multitasking — no lock needed |
| **Dispatch-time batching (not request fusion)** | Requests are held briefly then released simultaneously, letting llama.cpp batch prompt processing internally. No HTTP body merging needed — preserves per-request streaming and OpenAI compatibility |
| **Batching after concurrency slot acquisition** | Slots are held during batching wait, maintaining proper backpressure. Without this, the batch could grow unbounded while the upstream is saturated |
| **Small default window (50ms)** | Balances TTFT increase against batching opportunity. Under light load, 50ms penalty is negligible. Under heavy load, multiple requests accumulate within the window |

---

## Features

### Inference Gateway
- OpenAI-compatible `POST /v1/chat/completions` (streaming + non-streaming)
- `GET /v1/models` — lists available models from llama.cpp
- SSE streaming with `data: [DONE]` termination sentinel
- API key authentication — HMAC-SHA256 with timing-safe comparison
- Format: `sk_local_{public_id}_{secret}` (128-bit + 256-bit entropy)

### Request Batching
- **Dispatch-time batching** — concurrent requests arriving within a configurable window (default 50ms) are released simultaneously to llama.cpp
- **Configurable max batch size** (default 8) — limits worst-case batch wait
- **No HTTP body merging** — preserves per-request streaming and OpenAI compatibility
- **Safe fallback** — single requests flush after the window timeout with no starvation risk
- **Algorithm**: `asyncio.Event`-based barrier with generation counter to prevent stale timer flushes

### Concurrency Control
- `asyncio.Semaphore`-based slot limiting (configurable: `inference_max_concurrency`)
- Request queue with depth tracking (configurable: `inference_queue_size`)
- Queue timeout — 503 if slot not acquired in time (configurable: `inference_queue_timeout_s`)
- Overload rejection — 503 when queue is full
- Queue saturation monitoring via Prometheus gauge

### Observability (25 Prometheus metrics)
| Metric | Type | Labels | Description |
|---|---|---|---|
| `inference_chat_requests_total` | Counter | `mode` | Request count |
| `inference_time_to_first_token_seconds` | Histogram | — | TTFT distribution |
| `inference_upstream_wall_seconds` | Histogram | — | llama.cpp latency |
| `inference_streaming_duration_seconds` | Histogram | — | Stream session length |
| `inference_queue_wait_seconds` | Histogram | — | Time in concurrency queue |
| `inference_queue_depth` | Gauge | — | Current queue length |
| `inference_active_requests` | Gauge | `mode` | In-flight requests |
| `inference_streaming_requests_in_flight` | Gauge | — | Active streams |
| `inference_chat_completions_errors_total` | Counter | `kind` | Error breakdown |
| `inference_tokens_total` | Counter | `kind` | Completion token count |
| `inference_rejected_overload_total` | Counter | — | 503 rejection count |
| `inference_rate_limit_exceeded_total` | Counter | — | 429 rate limit hits |
| `gateway_process_uptime_seconds` | Gauge | — | Process uptime |
| `gateway_process_resident_memory_bytes` | Gauge | — | RSS |
| `gateway_process_cpu_percent` | Gauge | — | CPU % |
| `inference_batch_dispatches_total` | Counter | — | Batch dispatch count |
| `inference_batch_single_dispatches_total` | Counter | — | Single-request batch count |
| `inference_batch_size` | Histogram | — | Requests per batch |
| `inference_batch_wait_seconds` | Histogram | — | Time in batch barrier |
| `inference_batch_queue_depth` | Gauge | — | Current barrier queue |
| `inference_batch_efficiency` | Gauge | — | Cumulative batching efficiency |

### Grafana Dashboard (29 panels)
- **Stat row**: Active Requests, Queue Depth, Overload Rejections, Request Rate, Error Rate, Upstream Timeouts
- **Latency**: Upstream Latency p50/p95/p99, TTFT p50/p95/p99, Queue Wait p50/p95/p99, Streaming Duration p50/p95/p99
- **Throughput**: Token Throughput, Request Rate by Mode, Streams In-Flight
- **Health**: Error Rate by Kind, Validation & Rejection Rate, Rate Limit & Quota Hits, Django Process Health, Gateway Process Health, Overload & Timeout Rate
- **Heatmap**: TTFT distribution over time
- **Batching row**: Batch Queue Depth, Batch Efficiency, Avg Batch Size, Batch Dispatch Rate, Batch Size Over Time, Batch Wait Time p50/p95

### Prometheus Recording Rules (30 rules)
- `inference:request_rate:5m`, `inference:error_rate:5m`, `inference:error_ratio:5m`
- `inference:upstream_latency_p50/p95/p99:5m`
- `inference:ttft_p50/p95/p99:5m`
- `inference:stream_duration_p50/p95:5m`
- `inference:token_throughput:1m`, `inference:token_throughput:5m`
- `inference:queue_saturation:1m`, `inference:active_requests:1m`
- `inference:gateway_memory_gb:1m`, `inference:gateway_cpu:1m`
- `inference:batch_size_avg:5m`, `inference:batch_efficiency:5m`, `inference:batch_single_ratio:5m`
- `inference:batch_wait_p50/p95:5m`

### Resilience
- Graceful shutdown with 60s connection drain (`--timeout-graceful-shutdown`)
- Memory leak protection (`--limit-max-requests 10000`)
- `restart: unless-stopped` on all services
- Resource limits (llamacpp: 48g, gateway: 2g, nginx: 256m)
- Healthchecks with start periods on all services
- Llamacpp can restart independently — gateway returns 502/504 errors, stays up
- Redis fail-open — requests allowed during Redis outage
- Client disconnect handling — `CancelledError` caught, slot released, `client_disconnected` tracked

### Structured Logging
```json
{
  "level": "info",
  "event": "request_complete",
  "request_id": "abc-123",
  "route": "chat",
  "stream": true,
  "latency_ms": 45200,
  "ttft_ms": 3200,
  "bytes": 8240,
  "est_tokens_per_sec": 12.4,
  "queue_wait_ms": 1500,
  "api_key_id": "...",
  "status": 200,
  "error_kind": ""
}
```

---

## Quickstart

### Prerequisites
- Docker & Docker Compose
- A GGUF model file (e.g., Llama 3.2 3B Q4_K_M from Hugging Face)

### Setup

```bash
# 1. Copy environment defaults
cp .env.example .env

# 2. Place a GGUF model
#     curl -L -o models/model.gguf <hf-model-url>

# 3. Start the stack
docker compose up --build

# 4. Create an admin user (required — no registration UI exists)
docker compose exec django python manage.py createsuperuser

# 5. Create an API key
#     Open http://localhost:8888/staff/api-keys/ (login with the user above)
```

### Entry points

| Service | URL |
|---|---|
| App via nginx | `http://localhost:8888/` |
| Gateway direct | `http://127.0.0.1:18081/` |
| Prometheus | `http://localhost:9090/` |
| Grafana | `http://localhost:3000/` (admin/admin) |

### Test a request

```bash
# Streaming chat completion
curl -X POST http://localhost:8888/v1/chat/completions \
  -H "Authorization: Bearer sk_local_<your-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true,
    "max_tokens": 64
  }'

# Non-streaming
curl -X POST http://localhost:8888/v1/chat/completions \
  -H "Authorization: Bearer sk_local_<your-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "default",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false,
    "max_tokens": 64
  }'
```

---

## k6 Load Testing

Nine scripts in `loadtest/`:

| Script | Description | VUs | Duration |
|---|---|---|---|
| `chat-streaming.js` | Streaming completions with TTFT tracking | 5 | 3m |
| `chat-nonstreaming.js` | Non-streaming completions with usage validation | 10 | 3m |
| `chat-mixed.js` | Concurrent streaming + non-streaming scenarios | 4+6 | 3m |
| `chat-batch.js` | Constant-arrival-rate streaming — exercises batch barrier | 10 | 3m |
| `chat-step-stress.js` | Step stress test: 2→14 VUs in 1m steps — finds saturation knee | 14 | 7.5m |
| `chat-cancellation.js` | Client disconnect simulation + liveness check | 5 | ~50s |
| `chat-timeout.js` | Upstream timeout simulation + recovery check | 3 | ~50s |
| `spike-test.js` | Sudden burst (0→20→0 VUs) — tests backpressure | 20 | 2m |
| `soak-test.js` | Sustained moderate load, alternates streaming/non-streaming | 3 | 30m |

```bash
K6_API_KEY="sk_local_..." k6 run loadtest/spike-test.js
```

All scripts support `K6_API_KEY`, `K6_BASE_URL`, `K6_VUS` env vars.

---

## Configuration

Gateway settings (`deploy/gateway/gateway/config.py`):

| Variable | Default | Description |
|---|---|---|
| `inference_max_concurrency` | 4 | Max simultaneous llama.cpp calls. Must NOT exceed llama.cpp `--parallel` |
| `inference_queue_size` | 10 | Max queued requests before 503 |
| `inference_queue_timeout_s` | 30.0 | Queue wait timeout before 503 |
| `batch_window_ms` | 50.0 | Time window for aggregating requests into a batch (ms) |
| `batch_max_size` | 8 | Maximum requests per batch |
| `gateway_persist_logs` | True | Persist request logs to Postgres |

### llama.cpp parallel slots

The gateway's `inference_max_concurrency` must match llama.cpp's `--parallel` slots:

| Gateway config | llama.cpp `EXTRA_ARGS` | Behavior |
|---|---|---|
| `inference_max_concurrency=4` | `--parallel 4` | 4 concurrent requests processed simultaneously with prompt batching ✓ |
| `inference_max_concurrency=4` | `--parallel 1` (default) | 1 processed, 3 queue inside llama.cpp — **queue wait spikes** ✗ |

Set in `docker-compose.yml`:
```yaml
EXTRA_ARGS: "--parallel 4 --batch-size 1024 --ubatch-size 512"
```

---

## Repository layout

```
deploy/
├── gateway/              # FastAPI inference gateway
│   └── gateway/
│       ├── main.py       # Streaming + non-streaming handlers
│       ├── concurrency.py # Semaphore + queue with backpressure
│       ├── batcher.py    # Request batch barrier (dispatch-time coordination)
│       ├── metrics.py    # 25 Prometheus metric families
│       ├── limits.py     # Redis rate limiter (fail-open)
│       ├── crypto_auth.py # HMAC-SHA256 API key auth
│       ├── runtime_metrics.py # Process RSS, CPU%, uptime
│       └── config.py     # Pydantic settings
├── prometheus/
│   ├── prometheus.yml    # Scrape config (django + gateway)
│   └── rules.yml         # 22 recording rules
├── grafana/
│   └── provisioning/
│       └── dashboards/
│           └── inference-dashboard.json  # 29 panels
├── nginx/
│   └── default.conf      # Route /v1/ to gateway, / to Django
└── llamacpp/             # Multi-arch llama.cpp Docker build

loadtest/                 # 7 k6 test scripts
docs/
├── architecture.md       # Full architecture reference
├── architecture-diagram.md # Diagram spec (Mermaid + Excalidraw)
├── performance.md        # Tuning guide and bottleneck analysis
└── load-testing.md       # k6 usage guide and scenario reference
```

---

## Production readiness

| Category | Status | Notes |
|---|---|---|
| Observability | 9/10 | 25 metrics, 29 Grafana panels, structured JSON logging, p50/p95/p99 latency, batching metrics |
| Concurrency | 9/10 | Semaphore + queue, proper 503, queue saturation tracking, request batching (50ms window). Multi-worker needs Redis semaphore |
| Streaming | 8/10 | SSE, CancelledError handling, timeout/unavailable errors, `[DONE]` sentinel |
| Resilience | 7/10 | Redis fail-open, graceful shutdown, restart policies. Missing: circuit breaker |
| Security | 8/10 | HMAC-SHA256, timing-safe compare, Bearer auth, CSRF for UI, rate limiting |
| Docker | 8/10 | Multi-stage builds, healthchecks, resource limits, restart policies |
| Testing | 8/10 | 7 k6 scripts: streaming, non-streaming, mixed, cancellation, timeout, spike, soak |

---

## License

MIT
