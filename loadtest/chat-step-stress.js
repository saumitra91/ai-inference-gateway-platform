// k6 step stress test — find the system's saturation knee
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/chat-step-stress.js
//
// Steps VUs from 2 → 14 over 7 minutes.  Each step holds for 1 minute
// to reach steady state.  Queue wait at each step tells you exactly
// where the system saturates.
//
// Expected behavior with 8 parallel slots and ~10s response time:
//   2 VUs — queue ~0s      (25% utilization)
//   4 VUs — queue ~0s      (50%)
//   6 VUs — queue ~0s      (75%)  ← sweet spot
//   8 VUs — queue 2-10s    (100%) ← knee
//  10 VUs — queue 15-25s   (125%) ← overload
//  12 VUs — queue 25s+     (150%) ← saturation
//  14 VUs — timeouts        (175%)
//
// Watch in Grafana:
//   inference_queue_wait_seconds  — spikes at the knee
//   inference_batch_size          — improves under load
//   inference_rejected_overload_total — appears at saturation

import http from 'k6/http';
import { check } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8888';
const API_KEY = __ENV.K6_API_KEY || '';
const BACKEND = __ENV.K6_BACKEND || '';

const errorRate = new Rate('stress_errors');
const rejectedRate = new Rate('stress_rejected');
const queueWait = new Trend('stress_queue_wait_ms');
const ttft = new Trend('stress_ttft_ms');

export const options = {
  scenarios: {
    step_stress: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '1m', target: 2  },  // light
        { duration: '1m', target: 4  },  // moderate
        { duration: '1m', target: 6  },  // sweet spot
        { duration: '1m', target: 8  },  // capacity — should see first queue
        { duration: '1m', target: 10 },  // overload
        { duration: '1m', target: 12 },  // saturation
        { duration: '1m', target: 14 },  // heavy saturation
        { duration: '30s', target: 0 },  // cooldown
      ],
      gracefulStop: '30s',
    },
  },
  thresholds: {
    stress_errors: ['rate<0.10'],
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

  const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
    headers,
    timeout: '120s',
    responseType: 'text',
  });

  if (res.status === 429) {
    rejectedRate.add(1);
    return;
  }
  if (res.status === 503) {
    rejectedRate.add(1);
    return;
  }
  if (res.status !== 200) {
    errorRate.add(1);
    return;
  }

  // Check for queue wait time in the body — not directly available in k6
  // Instead we rely on Grafana metrics for queue analysis
  let foundDone = false;
  for (const event of res.body.split('\n\n')) {
    if (!event.trim()) continue;
    for (const line of event.split('\n')) {
      if (line.startsWith('data:')) {
        const data = line.slice(5).trim();
        if (data === '[DONE]') foundDone = true;
      }
    }
  }

  check(res, {
    'stress status is 200': (r) => r.status === 200,
    'stress has DONE': () => foundDone,
  });
}
