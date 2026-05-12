// k6 non-streaming chat completion load test
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/chat-nonstreaming.js
//
// Options:
//   K6_API_KEY    - Bearer token for auth
//   K6_BASE_URL   - target URL (default http://localhost:8888)
//   K6_VUS        - virtual users (default 10)
//   K6_DURATION   - test duration (default 3m)

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8888';
const API_KEY = __ENV.K6_API_KEY || '';

const errorRate = new Rate('errors');
const latency = new Trend('latency_ms');
const responseSize = new Trend('response_size_bytes');

export const options = {
  scenarios: {
    nonstreaming: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: __ENV.K6_VUS ? parseInt(__ENV.K6_VUS) : 10 },
        { duration: '2m', target: __ENV.K6_VUS ? parseInt(__ENV.K6_VUS) : 10 },
        { duration: '30s', target: 0 },
      ],
      gracefulStop: '10s',
    },
  },
  thresholds: {
    errors: ['rate<0.02'],
    latency: ['p(95)<60000'],
  },
};

const PROMPTS = [
  'Explain what a transformer model is and how it works.',
  'What is the difference between bagging and boosting?',
  'Describe the concept of transfer learning.',
  'How does a convolutional neural network process images?',
  'What is the role of the learning rate in training?',
  'Explain the vanishing gradient problem.',
  'What is the purpose of an attention mechanism?',
  'Compare recurrent networks and transformers.',
  'What are the advantages of residual connections?',
  'How does data augmentation improve model performance?',
  'Describe the encoder-decoder architecture.',
  'What is the difference between precision and recall?',
  'Explain the concept of embedding spaces.',
  'What is curriculum learning?',
  'How does beam search work in sequence generation?',
];

function randomPrompt() {
  return PROMPTS[Math.floor(Math.random() * PROMPTS.length)];
}

export default function () {
  const payload = JSON.stringify({
    model: 'default',
    messages: [{ role: 'user', content: randomPrompt() }],
    max_tokens: 64,
    stream: false,
    temperature: 0.7,
  });

  const headers = {
    'Content-Type': 'application/json',
  };
  if (API_KEY) {
    headers['Authorization'] = `Bearer ${API_KEY}`;
  }

  const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
    headers,
    timeout: '120s',
  });

  const elapsed = res.timings.duration;
  latency.add(elapsed);

  const isOk = res.status === 200;
  errorRate.add(!isOk);

  if (isOk) {
    try {
      const body = JSON.parse(res.body);
      const usage = body.usage || {};
      responseSize.add(res.body.length);
      check(res, {
        'has choices': () => (body.choices || []).length > 0,
        'has usage': () => Object.keys(usage).length > 0,
      });
    } catch (e) {
      errorRate.add(true);
      console.error(`parse error: ${e.message}`);
    }
  } else {
    console.error(`non-200: status=${res.status} body=${res.body.substring(0, 200)}`);
  }

  sleep(0.2);
}
