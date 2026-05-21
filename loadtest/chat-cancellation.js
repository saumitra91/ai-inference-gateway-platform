// k6 cancellation simulation test — client disconnect mid-stream
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/chat-cancellation.js
//
// This test starts streaming requests with a very short client timeout,
// simulating a client disconnecting before the stream completes. The gateway
// should handle CancelledError gracefully (499, not a crash).
// After the cancellation wave, a liveness check verifies the gateway recovered.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8888';
const API_KEY = __ENV.K6_API_KEY || '';
const BACKEND = __ENV.K6_BACKEND || '';

const cancelErrors = new Rate('cancel_errors');
const gatewayOk = new Counter('gateway_healthy_checks');

export const options = {
  scenarios: {
    cancellation_wave: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '5s', target: 5 },
        { duration: '15s', target: 5 },
        { duration: '5s', target: 0 },
      ],
      gracefulStop: '5s',
    },
    liveness_check: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 3,
      startTime: '35s',
      maxDuration: '15s',
    },
  },
};

const PROMPTS = [
  'Explain the concept of attention in transformer neural networks.',
  'Write a short poem about machine learning.',
  'What are the key differences between L1 and L2 regularization?',
  'Describe how gradient descent works in simple terms.',
  'What is the tradeoff between bias and variance in machine learning?',
];

export default function () {
  // cancellation scenario
  if (__SCENARIO_NAME === 'cancellation_wave') {
    const payload = JSON.stringify({
      model: 'default',
      messages: [{ role: 'user', content: PROMPTS[Math.floor(Math.random() * PROMPTS.length)] }],
      max_tokens: 256,
      stream: true,
      temperature: 0.7,
      ...(BACKEND ? { backend: BACKEND } : {}),
    });

    const headers = { 'Content-Type': 'application/json' };
    if (API_KEY) headers['Authorization'] = `Bearer ${API_KEY}`;

    // Short timeout to force client disconnect mid-stream
    const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
      headers,
      timeout: '1s',
      responseType: 'text',
    });

    // We expect this to fail due to the aggressive timeout
    // Gateway should not crash; any result is acceptable
    cancelErrors.add(res.status !== 200 && res.status !== 0);
    return;
  }

  // liveness check scenario
  if (__SCENARIO_NAME === 'liveness_check') {
    const res = http.get(`${BASE_URL}/health`);
    const ok = res.status === 200;
    if (ok) gatewayOk.add(1);
    check(res, {
      'gateway alive after cancellations': () => res.status === 200,
    });
  }
}
