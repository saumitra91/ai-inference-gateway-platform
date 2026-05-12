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

### 2.2 Basic script

Save as `loadtest.js`:

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const ttft = new Trend('ttft_ms');
const streamDuration = new Trend('stream_duration_ms');
const errorRate = new Rate('errors');

export const options = {
  stages: [
    { duration: '30s', target: 5 },   // ramp up to 5 VUs
    { duration: '1m', target: 10 },   // ramp to 10 VUs
    { duration: '30s', target: 0 },   // ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<30000'],  // 95% under 30s
    errors: ['rate<0.05'],               // <5% error rate
  },
};

const API_KEY = __ENV.API_KEY || 'sk_local_...';
const BASE_URL = __ENV.BASE_URL || 'http://localhost:8888';

export default function () {
  const payload = JSON.stringify({
    model: 'default',
    messages: [
      { role: 'user', content: 'Explain quantum computing in simple terms' },
    ],
    max_tokens: 128,
    stream: false,
  });

  const params = {
    headers: {
      'Authorization': `Bearer ${API_KEY}`,
      'Content-Type': 'application/json',
    },
    timeout: '120s',
  };

  const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, params);

  check(res, {
    'status is 200': (r) => r.status === 200,
    'response has choices': (r) => {
      try { return JSON.parse(r.body).choices?.length > 0; }
      catch { return false; }
    },
  });

  errorRate.add(res.status !== 200);
  sleep(1);
}
```

Run:

```bash
API_KEY="sk_local_..." k6 run loadtest.js
```

### 2.3 Streaming test with k6

k6 does not natively support SSE streaming. For streaming load, use `hey` (above)
or measure TTFT via the `inference_time_to_first_token_seconds` Prometheus metric.

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

## 4. Known bottlenecks

| Bottleneck | Symptom | Mitigation |
|---|---|---|
| llama.cpp CPU-bound | High `upstream_wall_seconds`, low token throughput | Reduce context size, use smaller quantized model, increase threads |
| Django worker saturation | 502/503, connection timeouts | Increase `--workers` in uvicorn command |
| Redis contention | RPM/quota checks slow | Use dedicated Redis instance, or in-memory for single-worker dev |
| Body size limits | 413 responses | Increase `INFERENCE_MAX_REQUEST_BODY_BYTES` if needed |
| Too many concurrent streams | OOM in Django | Monitor `inference_active_requests`, set uvicorn `--limit-max-requests` |

---

## 5. Suggested load-test scenarios

| Scenario | VUs | Duration | Key metric | Pass criteria |
|---|---|---|---|---|
| Light smoke | 1 | 30s | All 200s | 100% success |
| Sustained load | 10 | 5m | p95 < 30s | <5% errors |
| Burst | 0→20→0 | 1m | Recovery time | No persistent errors |
| Long prompts | 5 | 2m | Memory stable | No OOM |
| Error injection | 5 | 1m | Correct 400/413 | No 5xx for validation errors |
| Streaming | 5 | 3m | TTFT p95 < 10s | No stream drop |
