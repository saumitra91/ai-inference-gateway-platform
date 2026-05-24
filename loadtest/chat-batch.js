// k6 batching load test — concurrent requests to exercise batch barrier
//
// NOTE: k6's http.post() buffers the full SSE response before returning.
// TTFT is measured during client-side SSE parsing, AFTER the entire
// stream is received.  The value approximates full completion latency,
// NOT true server-side time-to-first-token.  Use the server-side
// `inference_backend_ttft_seconds` Prometheus metric for accurate TTFT.
//
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/chat-batch.js
//
// IMPORTANT: Default API keys have rate_limit_rpm=120 (2 req/s).
// For meaningful batching you need higher concurrency. Either:
//   a) Increase your API key's rate_limit_rpm in the Django admin
//      (Settings → API Keys → edit key → set RPM to 600+)
//   b) Or pass a custom rate: K6_RATE=10 K6_VUS=20 k6 run ...
//
// This test uses ramping-vus (not constant-arrival-rate) so that
// multiple VUs send requests simultaneously, creating true concurrency
// within the batch barrier's 50ms window.
//
// Metrics to watch in Grafana:
//   inference_batch_size        — average batch size (higher → better batching)
//   inference_batch_efficiency   — cumulative efficiency ratio
//   inference_batch_wait_seconds — time spent in batch barrier

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8888';
const API_KEY = __ENV.K6_API_KEY || '';
const BACKEND = __ENV.K6_BACKEND || '';

const errorRate = new Rate('batch_errors');
const ttft = new Trend('batch_ttft_ms');
const latency = new Trend('batch_latency_ms');
const streamChunks = new Counter('batch_chunks_total');
const streamBytes = new Counter('batch_bytes_total');

// Default VUs = 4, no rate control needed since ramping-vus paces VUs.
// Default API key RPM is 120.  4 VUs × ~15 iterations/min = 60 req/min ✓
// Override: K6_VUS=12 k6 run ...  (requires API key RPM ≥ 360+)
const TARGET_VUS = __ENV.K6_VUS ? parseInt(__ENV.K6_VUS) : 4;

export const options = {
  scenarios: {
    batch_burst: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '10s', target: TARGET_VUS },
        { duration: '2m', target: TARGET_VUS },
        { duration: '10s', target: 0 },
      ],
      gracefulStop: '30s',
    },
  },
  thresholds: {
    batch_errors: ['rate<0.05'],
    batch_ttft_ms: ['p(95)<60000'],
  },
};

const PROMPTS = [
  'Explain the concept of attention in transformer neural networks.',
  'Write a short poem about machine learning.',
  'What are the key differences between L1 and L2 regularization?',
  'Describe how gradient descent works in simple terms.',
  'What is the tradeoff between bias and variance in machine learning?',
  'Explain the role of activation functions in neural networks.',
  'How does dropout prevent overfitting?',
  'What is the purpose of batch normalization?',
];

export default function () {
  const payload = JSON.stringify({
    model: 'default',
    messages: [{ role: 'user', content: PROMPTS[Math.floor(Math.random() * PROMPTS.length)] }],
    max_tokens: 128,
    stream: true,
    temperature: 0.7,
    ...(BACKEND ? { backend: BACKEND } : {}),
  });

  const headers = { 'Content-Type': 'application/json' };
  if (API_KEY) headers['Authorization'] = `Bearer ${API_KEY}`;

  const startTime = Date.now();
  let firstChunk = null;
  let chunkCount = 0;
  let totalBytes = 0;

  const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
    headers,
    timeout: '120s',
    responseType: 'text',
  });

  if (res.status === 429) {
    errorRate.add(1);
    return;
  }

  if (res.status !== 200) {
    errorRate.add(1);
    console.error(`batch error: status=${res.status} body=${(res.body || "").substring(0, 200)}`);
    return;
  }

  errorRate.add(0);

  const events = res.body.split('\n\n');
  let foundDone = false;
  for (const event of events) {
    if (!event.trim()) continue;
    for (const line of event.split('\n')) {
      if (line.startsWith('data:')) {
        const data = line.slice(5).trim();
        if (data === '[DONE]') {
          foundDone = true;
          continue;
        }
        if (data.startsWith('{"error"')) {
          errorRate.add(1);
          continue;
        }
        if (firstChunk === null) {
          firstChunk = Date.now();
          ttft.add(firstChunk - startTime);
        }
        chunkCount++;
        totalBytes += data.length;
      }
    }
  }

  streamChunks.add(chunkCount);
  streamBytes.add(totalBytes);
  latency.add(Date.now() - startTime);

  check(res, {
    'batch status is 200': (r) => r.status === 200,
    'batch has chunks': () => chunkCount > 0,
    'batch has DONE sentinel': () => foundDone,
  });

  // Pace requests to stay within API key rate limits.
  // Without sleep, VUs cycle at response-speed → burst can exceed RPM.
  // With sleep(1), each VU sends ~1 req per (response_time + 1s).
  // For max batching throughput, set K6_VUS=12 and increase key RPM to 600+.
  sleep(1);
}
