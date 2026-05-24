// Benchmark script targeting llama.cpp backend exclusively.
// Measures TTFT, completion latency, tokens/sec, and request success rate.
//
// NOTE: k6's http.post() buffers the full SSE response before returning.
// TTFT is measured during client-side SSE parsing, AFTER the entire
// stream is received.  The value approximates full completion latency,
// NOT true server-side time-to-first-token.  Use the server-side
// `inference_backend_ttft_seconds` Prometheus metric for accurate TTFT.
//
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/benchmark-llamacpp.js
//
// Environment variables:
//   K6_API_KEY   - Bearer token (required)
//   K6_BASE_URL  - Gateway URL (default http://localhost:8888)
//   K6_VUS       - Concurrent VUs (default 4)
//   K6_DURATION  - Test duration (default 5m)
//   K6_BACKEND   - Backend override: "llamacpp" or "vllm" (default: llamacpp)

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate, Counter } from "k6/metrics";

const BASE_URL = __ENV.K6_BASE_URL || "http://localhost:8888";
const API_KEY = __ENV.K6_API_KEY || "";
const VUS = parseInt(__ENV.K6_VUS || "4", 10);
const DURATION = __ENV.K6_DURATION || "5m";
const BACKEND = __ENV.K6_BACKEND || "llamacpp";

const PAYLOAD = JSON.stringify({
  model: "default",
  messages: [{ role: "user", content: "Write a short paragraph about the history of machine learning." }],
  stream: true,
  max_tokens: 256,
  temperature: 0.7,
  backend: BACKEND,
});

const HEADERS = {
  "Content-Type": "application/json",
  Authorization: `Bearer ${API_KEY}`,
};

const ttft = new Trend("llamacpp_ttft_ms", true);
const completion_latency = new Trend("llamacpp_completion_latency_ms", true);
const tokens_per_sec = new Trend("llamacpp_tokens_per_sec", true);
const response_size = new Trend("llamacpp_response_bytes", true);
const failures = new Rate("llamacpp_failures");
const requests = new Counter("llamacpp_requests_total");

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    llamacpp_failures: ["rate<0.05"],
  },
  noConnectionReuse: false,
};

export default function () {
  const t0 = Date.now();
  const resp = http.post(`${BASE_URL}/v1/chat/completions`, PAYLOAD, {
    headers: HEADERS,
    timeout: "300s",
  });
  const total = Date.now() - t0;

  requests.add(1);

  if (resp.status !== 200) {
    failures.add(1);
    console.error(`llamacpp error: status=${resp.status} body=${resp.body.substring(0, 200)}`);
    return;
  }

  const body = resp.body;
  response_size.add(body.length);

  // Parse SSE to find TTFT and token count
  let firstTokenTime = total;
  let charCount = 0;
  const lines = body.split("\n");
  for (const line of lines) {
    if (!line.startsWith("data: ")) continue;
    const data = line.slice(6).trim();
    if (data === "[DONE]") continue;
    try {
      const parsed = JSON.parse(data);
      const choices = parsed.choices;
      if (choices && choices.length > 0) {
        const delta = choices[0].delta;
        if (delta && delta.content) {
          if (firstTokenTime === total) {
            firstTokenTime = Date.now() - t0;
          }
          charCount += delta.content.length;
        }
      }
    } catch (e) {
      // skip parse errors
    }
  }

  ttft.add(firstTokenTime);
  completion_latency.add(total);
  if (total > 0 && charCount > 0) {
    tokens_per_sec.add((charCount / 4) / (total / 1000));
  }

  sleep(0.1);
}
