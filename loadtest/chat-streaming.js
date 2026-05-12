// k6 streaming chat completion load test
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/chat-streaming.js
//
// Options:
//   K6_API_KEY    - Bearer token for auth
//   K6_BASE_URL   - target URL (default http://localhost:8888)
//   K6_VUS        - virtual users (default 5)
//   K6_DURATION   - test duration (default 3m)

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8888';
const API_KEY = __ENV.K6_API_KEY || '';

const streamErrors = new Rate('stream_errors');
const ttft = new Trend('ttft_ms');
const streamDuration = new Trend('stream_duration_ms');
const streamChunks = new Counter('stream_chunks_total');
const streamBytes = new Counter('stream_bytes_total');

export const options = {
  scenarios: {
    streaming: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: __ENV.K6_VUS ? parseInt(__ENV.K6_VUS) : 5 },
        { duration: '2m', target: __ENV.K6_VUS ? parseInt(__ENV.K6_VUS) : 5 },
        { duration: '30s', target: 0 },
      ],
      gracefulStop: '30s',
    },
  },
  thresholds: {
    stream_errors: ['rate<0.05'],
    http_req_duration: ['p(95)<120000'],
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
  'Compare supervised and unsupervised learning.',
  'What are embeddings in the context of NLP?',
];

function randomPrompt() {
  return PROMPTS[Math.floor(Math.random() * PROMPTS.length)];
}

export default function () {
  const payload = JSON.stringify({
    model: 'default',
    messages: [{ role: 'user', content: randomPrompt() }],
    max_tokens: 128,
    stream: true,
    temperature: 0.7,
  });

  const headers = {
    'Content-Type': 'application/json',
  };
  if (API_KEY) {
    headers['Authorization'] = `Bearer ${API_KEY}`;
  }

  const startTime = Date.now();
  let firstChunk = null;
  let chunkCount = 0;
  let totalBytes = 0;

  const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
    headers,
    timeout: '120s',
    responseType: 'text',
  });

  if (res.status !== 200) {
    streamErrors.add(1);
    console.error(`stream error: status=${res.status} body=${res.body.substring(0, 200)}`);
    return;
  }

  // Parse SSE events from response body
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
          streamErrors.add(1);
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

  const elapsed = Date.now() - startTime;
  streamDuration.add(elapsed);

  check(res, {
    'status is 200': (r) => r.status === 200,
    'has chunks': () => chunkCount > 0,
    'has DONE sentinel': () => foundDone,
  });

  sleep(0.5);
}
