// k6 upstream timeout simulation test
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/chat-timeout.js
//
// This test sends requests with a very short client timeout to verify the
// gateway handles upstream timeout scenarios correctly (returns 504 or
// appropriate error, does not crash, recovers between bursts).

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Counter } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8888';
const API_KEY = __ENV.K6_API_KEY || '';
const BACKEND = __ENV.K6_BACKEND || '';

const errorRate = new Rate('timeout_errors');
const healthyCount = new Counter('healthy_checks');

export const options = {
  scenarios: {
    timeout_bursts: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '5s', target: 3 },
        { duration: '10s', target: 3 },
        { duration: '5s', target: 0 },
      ],
      gracefulStop: '10s',
    },
    recovery_check: {
      executor: 'per-vu-iterations',
      vus: 1,
      iterations: 5,
      startTime: '30s',
      maxDuration: '20s',
    },
  },
  thresholds: {
    healthy_checks: ['count >= 5'],
  },
};

export default function () {
  if (__SCENARIO_NAME === 'timeout_bursts') {
    const payload = JSON.stringify({
      model: 'default',
      messages: [{ role: 'user', content: 'Write a very long story about AI.' }],
      max_tokens: 2048,
      stream: false,
      temperature: 0.7,
      ...(BACKEND ? { backend: BACKEND } : {}),
    });

    const headers = { 'Content-Type': 'application/json' };
    if (API_KEY) headers['Authorization'] = `Bearer ${API_KEY}`;

    const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
      headers,
      timeout: '2s',
    });

    // Accept any response — gateway should return 504 or similar error.
    // 200 is unexpected given the short timeout but still "ok" for the test.
    errorRate.add(res.status !== 200 && res.status !== 504 && res.status !== 0);
  }

  if (__SCENARIO_NAME === 'recovery_check') {
    // Normal request to verify gateway recovered
    const payload = JSON.stringify({
      model: 'default',
      messages: [{ role: 'user', content: 'Hi' }],
      max_tokens: 16,
      stream: false,
      temperature: 0.7,
      ...(BACKEND ? { backend: BACKEND } : {}),
    });

    const headers = { 'Content-Type': 'application/json' };
    if (API_KEY) headers['Authorization'] = `Bearer ${API_KEY}`;

    const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
      headers,
      timeout: '30s',
    });

    const ok = res.status === 200;
    if (ok) healthyCount.add(1);
    check(res, {
      'gateway recovered after timeouts': () => ok,
    });
    sleep(1);
  }
}
