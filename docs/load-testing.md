# Load Testing Guide

## Quick start

### Prerequisites

- [hey](https://github.com/rakyll/hey) — HTTP load generator
- [k6](https://k6.io) — scriptable load testing (optional, for richer metrics)
- Stack running: `docker compose up -d`

### Smoke test (single request)

```bash
hey -n 1 -m POST \
  -H "Authorization: Bearer $(your-api-key)" \
  -H "Content-Type: application/json" \
  -d '{"model":"default","messages":[{"role":"user","content":"Hello"}],"max_tokens":10}' \
  http://localhost:8888/v1/chat/completions
```

---

## 1. `hey` based load tests

### 1.1 Throughput — non-streaming

```bash
# 100 requests, 10 concurrent workers, 30s timeout
hey -n 100 -c 10 -m POST \
  -H "Authorization: Bearer $(your-api-key)" \
  -H "Content-Type: application/json" \
  -d '{"model":"default","messages":[{"role":"user","content":"What is the capital of France?"}],"max_tokens":32}' \
  -t 30 \
  http://localhost:8888/v1/chat/completions
```

Output includes:
- **p50/p95/p99 latency**
- **Requests/sec**
- **Error rate**

### 1.2 Throughput — streaming

```bash
hey -n 50 -c 5 -m POST \
  -H "Authorization: Bearer $(your-api-key)" \
  -H "Content-Type: application/json" \
  -d '{"model":"default","messages":[{"role":"user","content":"Write a 3-paragraph story about AI"}],"stream":true,"max_tokens":256}' \
  -t 120 \
  http://localhost:8888/v1/chat/completions
```

> **Note**: `hey` measures time-to-last-byte for streaming requests, which includes
> the full generation time. This is useful for end-to-end latency but does not
> break out TTFT.

### 1.3 Vary prompt size

```bash
# Short prompt
hey -n 50 -c 5 -d '{"messages":[{"role":"user","content":"Hi"}],"max_tokens":16}' ...

# Long prompt (use a file)
LONG_PROMPT=$(python -c "print('hello world ' * 5000)")
hey -n 20 -c 2 -d "{\"messages\":[{\"role\":\"user\",\"content\":\"$LONG_PROMPT\"}],\"max_tokens\":32}" ...
```

### 1.4 Error injection

```bash
# Malformed JSON — expect 400
hey -n 10 -c 2 -d "not json" http://localhost:8888/v1/chat/completions

# Empty messages — expect 400
hey -n 10 -c 2 \
  -d '{"model":"default","messages":[]}' \
  http://localhost:8888/v1/chat/completions

# Over max_tokens — expect clamped or 400 depending on config
hey -n 10 -c 2 \
  -d '{"model":"default","messages":[{"role":"user","content":"hi"}],"max_tokens":999999}' \
  http://localhost:8888/v1/chat/completions
```

---

## 2. `k6` based load tests

### 2.1 Install k6

```bash
# macOS
brew install k6

# Linux
curl -fsSL https://dl.k6.io/install.sh | sh
```

### 2.2 Using the k6 scripts

The `loadtest/` directory contains four k6 scripts:

| Script | Description | Default VUs | Duration |
|---|---|---|---|
| `chat-streaming.js` | Streaming completions with TTFT tracking | 5 | 3m |
| `chat-nonstreaming.js` | Non-streaming completions with usage validation | 10 | 3m |
| `spike-test.js` | Sudden burst (0→20→0 VUs) — tests backpressure | 20 peak | 2m |
| `soak-test.js` | Sustained moderate load, alternates streaming/non-streaming | 3 | 30m |

All scripts support `K6_API_KEY`, `K6_BASE_URL`, and `K6_VUS` env vars.
The soak test also respects `K6_DURATION`.

Example — run the spike test:

```bash
K6_API_KEY="sk_local_..." k6 run loadtest/spike-test.js
```

Custom concurrency:

```bash
K6_API_KEY="sk_local_..." K6_VUS=15 k6 run loadtest/chat-nonstreaming.js
```

### 2.3 Streaming test with k6

k6 buffers the entire HTTP response body, so streaming tests parse SSE events
_post-hoc_ from the body. This means:
- `ttft_ms` is measured as time-to-first-SSE-event-in-buffer (approximate)
- `stream_duration_ms` is end-to-end (request start to last byte)
- Chunks and bytes are counted for throughput estimation

For real-time TTFT measurement, use the Prometheus metric
`inference_time_to_first_token_seconds`.

### 2.4 Key metrics to monitor during load

| Metric | Where | What to watch |
|---|---|---|
| `inference_upstream_wall_seconds` | Prometheus/Django `/metrics` | P50/P95 upstream latency |
| `inference_time_to_first_token_seconds` | Prometheus | TTFT distribution |
| `inference_streaming_duration_seconds` | Prometheus | Streaming session length |
| `inference_chat_completions_errors_total` | Prometheus | Error rate by kind |
| `inference_rejected_requests_total` | Prometheus | Validation/policy rejections |
| `inference_upstream_timeouts_total` | Prometheus | Timeout count |
| `inference_active_requests` | Prometheus | Current concurrency |
| `django_process_resident_memory_bytes` | Prometheus | Django RSS during load |

---

## 3. Measuring TTFT

**Time to First Token** is the most important streaming latency metric.

### Via Prometheus

```promql
# P50 TTFT over last 5 minutes
histogram_quantile(0.50,
  rate(inference_time_to_first_token_seconds_bucket[5m])
)

# P95 TTFT
histogram_quantile(0.95,
  rate(inference_time_to_first_token_seconds_bucket[5m])
)
```

### Via structured logs

The Django JSON log includes `ttft_ms` for streaming requests.
```bash
docker compose logs django | grep '"stream": true' | jq '.ttft_ms' | sort -n \
  | awk '{a[NR]=$1} END{print NR?"P50: "a[int(NR*0.5)]"\nP95: "a[int(NR*0.95)]:""}'
```

---

## 4. Backpressure testing

The inference gateway implements per-process concurrency limiting via
`asyncio.Semaphore`. When all slots are occupied, requests queue (up to
`INFERENCE_QUEUE_SIZE`, default 10) and wait up to `INFERENCE_QUEUE_TIMEOUT_S`
(default 30s) for a slot. If the queue is full, the server returns **503** with
a structured JSON body.

### Verifying backpressure

Run the spike test and watch for 503 responses:

```bash
K6_API_KEY="sk_local_..." k6 run loadtest/spike-test.js
```

Expected behavior:
1. At 20 VUs, 503s appear as the concurrency limiter activates
2. `inference_rejected_overload_total` increments
3. `inference_queue_depth` shows non-zero values
4. Active requests saturate at `INFERENCE_MAX_CONCURRENCY`
5. After the spike, 503s stop and queue drains

### Tuning backpressure

| Parameter | Effect | Tuning guidance |
|---|---|---|
| `INFERENCE_MAX_CONCURRENCY=4` | Max llama.cpp calls at once | Increase if llama.cpp has CPU headroom; decrease if OOM |
| `INFERENCE_QUEUE_SIZE=10` | Max queued requests | Larger = more burst tolerance; smaller = faster 503 |
| `INFERENCE_QUEUE_TIMEOUT_S=30.0` | Max queue wait time | Shorter for interactive UI; longer for batch jobs |

### Monitoring during backpressure tests

```promql
# Are we rejecting?
rate(inference_rejected_overload_total[1m])

# How deep is the queue?
inference_queue_depth

# How long do requests wait?
histogram_quantile(0.95, rate(inference_queue_wait_seconds_bucket[1m]))
```

---

## 5. Known bottlenecks

| Bottleneck | Symptom | Mitigation |
|---|---|---|
| llama.cpp CPU-bound | High `upstream_wall_seconds`, low token throughput | Reduce context size, use smaller quantized model, increase threads |
| Django worker saturation | 502/503, connection timeouts | Increase `--workers` in uvicorn command |
| Redis contention | RPM/quota checks slow | Use dedicated Redis instance, or in-memory for single-worker dev |
| Body size limits | 413 responses | Increase `INFERENCE_MAX_REQUEST_BODY_BYTES` if needed |
| Too many concurrent streams | OOM in Django | Monitor `inference_active_requests`, set uvicorn `--limit-max-requests` |

---

## 6. Suggested load-test scenarios

| Scenario | Script | VUs | Duration | Key metric | Pass criteria |
|---|---|---|---|---|---|---|
| Light smoke | any | 1 | 30s | All 200s | 100% success |
| Sustained non-streaming | `chat-nonstreaming.js` | 10 | 5m | p95 < 30s | <5% errors |
| Streaming | `chat-streaming.js` | 5 | 3m | TTFT p95 < 10s | No stream drop |
| Spike/burst | `spike-test.js` | 0→20→0 | 2m | Recovery time | 503s expected, no persistent errors |
| Soak | `soak-test.js` | 3 | 30m | Latency trend | No degradation over time |
| Long prompts | manual | 5 | 2m | Memory stable | No OOM |
| Error injection | manual | 5 | 1m | Correct 400/413 | No 5xx for validation errors |
