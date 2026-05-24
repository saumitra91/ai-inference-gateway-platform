// Head-to-head benchmark: alternates requests between llama.cpp and vLLM.
// Produces comparative statistics from a single test run.
//
// NOTE: k6's http.post() buffers the full SSE response before returning.
// TTFT is measured during client-side SSE parsing, AFTER the entire
// stream is received.  The value approximates full completion latency,
// NOT true server-side time-to-first-token.  Use the server-side
// `inference_backend_ttft_seconds` Prometheus metric for accurate TTFT.
//
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/benchmark-headtohead.js
//
// Environment variables:
//   K6_API_KEY     - Bearer token (required)
//   K6_BASE_URL    - Gateway URL (default http://localhost:8888)
//   K6_VUS         - Concurrent VUs (default 6 — 3 per backend)
//   K6_DURATION    - Test duration (default 5m)
//   K6_BACKEND_A   - First backend name (default: llamacpp)
//   K6_BACKEND_B   - Second backend name (default: vllm)

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate, Counter } from "k6/metrics";

const BASE_URL = __ENV.K6_BASE_URL || "http://localhost:8888";
const API_KEY = __ENV.K6_API_KEY || "";
const VUS = parseInt(__ENV.K6_VUS || "6", 10);
const DURATION = __ENV.K6_DURATION || "5m";

const SYSTEM_PROMPT = "You are a helpful assistant.";
const USER_PROMPTS = [
  "Explain the concept of recursion in programming.",
  "Write a haiku about databases.",
  "What is the difference between TCP and UDP?",
  "Summarize the water cycle in three sentences.",
  "List three benefits of functional programming.",
];

const BACKEND_A = __ENV.K6_BACKEND_A || "llamacpp";
const BACKEND_B = __ENV.K6_BACKEND_B || "vllm";

// Half the VUs target backend A, half target backend B
const BACKEND = __VU <= (VUS / 2) ? BACKEND_A : BACKEND_B;

// llama.cpp metrics
const l_ttft = new Trend("head2head_llamacpp_ttft_ms", true);
const l_latency = new Trend("head2head_llamacpp_completion_latency_ms", true);
const l_tps = new Trend("head2head_llamacpp_tokens_per_sec", true);
const l_failures = new Rate("head2head_llamacpp_failures");
const l_requests = new Counter("head2head_llamacpp_requests_total");

// vLLM metrics
const v_ttft = new Trend("head2head_vllm_ttft_ms", true);
const v_latency = new Trend("head2head_vllm_completion_latency_ms", true);
const v_tps = new Trend("head2head_vllm_tokens_per_sec", true);
const v_failures = new Rate("head2head_vllm_failures");
const v_requests = new Counter("head2head_vllm_requests_total");

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    "head2head_llamacpp_failures": ["rate<0.05"],
    "head2head_vllm_failures": ["rate<0.05"],
  },
  noConnectionReuse: false,
};

export default function () {
  const prompt = USER_PROMPTS[__ITER % USER_PROMPTS.length];
  const payload = JSON.stringify({
    model: "default",
    messages: [
      { role: "system", content: SYSTEM_PROMPT },
      { role: "user", content: prompt },
    ],
    stream: true,
    max_tokens: 256,
    temperature: 0.7,
    backend: BACKEND,
  });

  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${API_KEY}`,
  };

  const t0 = Date.now();
  const resp = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
    headers: headers,
    timeout: "300s",
  });
  const total = Date.now() - t0;

  if (BACKEND === "llamacpp") {
    l_requests.add(1);
  } else {
    v_requests.add(1);
  }

  const failed = resp.status !== 200;
  if (failed) {
    if (BACKEND === "llamacpp") {
      l_failures.add(1);
    } else {
      v_failures.add(1);
    }
    console.error(`${BACKEND} error: status=${resp.status}`);
    return;
  }

  const body = resp.body;
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
      // skip
    }
  }

  if (BACKEND === "llamacpp") {
    l_ttft.add(firstTokenTime);
    l_latency.add(total);
    if (total > 0 && charCount > 0) {
      l_tps.add((charCount / 4) / (total / 1000));
    }
  } else {
    v_ttft.add(firstTokenTime);
    v_latency.add(total);
    if (total > 0 && charCount > 0) {
      v_tps.add((charCount / 4) / (total / 1000));
    }
  }

  sleep(0.2);
}
