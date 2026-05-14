# Eraser.io Architecture Diagram

```diagram
direction down
colorMode bold
typeface clean
styleMode shadow

Client Layer [color: gray] {
  Load_Generator [icon: settings, label: "k6 Load Generator"]
  SDK [icon: github, label: "OpenAI SDK / curl"]
}

API Layer [color: blue] {
  nginx [icon: nginx, label: "nginx :8888"]
  FastAPI_Gateway [color: blue, icon: python, label: "FastAPI Gateway :8081"] {
    Concurrency_Queue [icon: settings, label: "Concurrency Queue"]
    Rate_Limiter [icon: settings, label: "Rate Limiter"]
    API_Key_Auth [icon: lock, label: "API Key Auth"]
    Metrics [icon: chart, label: "Prometheus /metrics"]
    Logger [icon: settings, label: "Structured Logger"]
  }
  Django [icon: python, label: "Django ASGI :8000"]
}

Inference Layer [color: purple] {
  llamacpp [icon: server, label: "llama.cpp :8080"]
  Model [icon: storage, label: "GGUF 7B Q4_K_M"]
  Redis [icon: redis]
  PostgreSQL [icon: postgres]
}

Observability Layer [color: green] {
  Prometheus [icon: prometheus]
  Grafana [icon: grafana]
}

// ── Request flow ──

Load_Generator > nginx: POST /v1/chat/completions
SDK > nginx: Bearer sk_local_...

nginx > FastAPI_Gateway: proxy_pass

FastAPI_Gateway > Concurrency_Queue: acquire_slot()
Concurrency_Queue > Rate_Limiter: wait / 503
Rate_Limiter > API_Key_Auth: ok
API_Key_Auth > llamacpp: POST /v1/chat/completions

llamacpp > Model: load + infer

// ── SSE response return path ──

llamacpp > FastAPI_Gateway: SSE data: {token}
FastAPI_Gateway > nginx: forward chunks
nginx > Load_Generator: 200 OK SSE
nginx > SDK: 200 OK JSON

// ── Metrics scraping ──

FastAPI_Gateway --> Prometheus: GET /metrics
Django --> Prometheus: GET /metrics
Prometheus --> Grafana: PromQL datasource

// ── Async persistence ──

FastAPI_Gateway --> PostgreSQL: _persist_log()
FastAPI_Gateway --> Redis: incr rate limit
API_Key_Auth --> PostgreSQL: verify_bearer_token()

// ── Legend ──

legend [position: bottom-left] {
  [connection: >, color: blue, label: Request flow]
  [connection: >, color: green, label: SSE response return]
  [connection: -->, color: green, label: Metrics scraping]
  [connection: -->, color: purple, label: Async persistence]
  [color: orange, label: Queue / Backpressure]
}
```
