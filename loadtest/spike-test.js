// k6 spike test — sudden burst of traffic to test backpressure
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/spike-test.js
//
// This test rapidly increases concurrency to stress the concurrency limiter
// and backpressure mechanisms. Expect 503/429 responses under load.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8888';
const API_KEY = __ENV.K6_API_KEY || '';

const errorRate = new Rate('errors');
const rejectedRate = new Rate('rejected');
const latency = new Trend('latency_ms');
const statusCounts = {
  '2xx': new Counter('status_2xx'),
  '4xx': new Counter('status_4xx'),
  '5xx': new Counter('status_5xx'),
  '503': new Counter('status_503'),
  '429': new Counter('status_429'),
};

export const options = {
  scenarios: {
    spike: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 2 },    // warmup
        { duration: '10s', target: 20 },    // spike
        { duration: '1m', target: 20 },     // sustain
        { duration: '30s', target: 0 },     // cooldown
      ],
      gracefulStop: '30s',
    },
  },
  thresholds: {
    errors: ['rate<0.10'],
    rejected: ['rate<0.50'],
  },
};

const PROMPTS = [
  'Write a short story about a robot learning to paint.',
  'Explain the water cycle to a 5-year-old.',
  'What are the three laws of thermodynamics?',
  'Describe how a database index works.',
  'What is the difference between TCP and UDP?',
];

export default function () {
  const prompt = PROMPTS[Math.floor(Math.random() * PROMPTS.length)];
  const payload = JSON.stringify({
    model: 'default',
    messages: [{ role: 'user', content: prompt }],
    max_tokens: 256,
    stream: false,
    temperature: 0.7,
  });

  const headers = { 'Content-Type': 'application/json' };
  if (API_KEY) headers['Authorization'] = `Bearer ${API_KEY}`;

  const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
    headers,
    timeout: '120s',
  });

  const elapsed = res.timings.duration;
  latency.add(elapsed);

  const is2xx = res.status >= 200 && res.status < 300;
  const is4xx = res.status >= 400 && res.status < 500;
  const is5xx = res.status >= 500;

  if (is2xx) statusCounts['2xx'].add(1);
  if (is4xx) statusCounts['4xx'].add(1);
  if (is5xx) statusCounts['5xx'].add(1);
  if (res.status === 503) statusCounts['503'].add(1);
  if (res.status === 429) statusCounts['429'].add(1);

  const isOk = is2xx;
  errorRate.add(!isOk);
  rejectedRate.add(!isOk);

  if (!isOk) {
    console.log(`spike: status=${res.status} body=${res.body.substring(0, 150)}`);
  }

  check(res, {
    'acceptable status': () => is2xx || res.status === 429 || res.status === 503,
  });

  // No sleep — max throughput during spike
}
