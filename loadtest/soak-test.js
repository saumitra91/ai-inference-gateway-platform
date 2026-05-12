// k6 soak test — sustained load over time to detect degradation
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/soak-test.js
//
// This test maintains moderate concurrency for an extended period to
// detect memory leaks, connection pool exhaustion, and performance degradation.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8888';
const API_KEY = __ENV.K6_API_KEY || '';

const errorRate = new Rate('errors');
const latency = new Trend('latency_ms');
const ttft = new Trend('ttft_ms');
const chunkCount = new Trend('chunks_per_response');

export const options = {
  scenarios: {
    soak: {
      executor: 'constant-vus',
      vus: __ENV.K6_VUS ? parseInt(__ENV.K6_VUS) : 3,
      duration: __ENV.K6_DURATION || '30m',
    },
  },
  thresholds: {
    errors: ['rate<0.03'],
    latency: ['p(95)<90000'],
  },
};

const PROMPTS = [
  'Explain the concept of entropy in information theory.',
  'What is the difference between stochastic and batch gradient descent?',
  'Describe how word2vec embeddings are trained.',
  'What is the role of the softmax function in classification?',
  'Explain the concept of a learning rate scheduler.',
  'How does early stopping prevent overfitting?',
  'What is the purpose of a validation set?',
  'Describe the differences between random forest and gradient boosting.',
  'What is the attention mechanism in the Transformer architecture?',
  'How does backpropagation compute gradients?',
  'Explain the concept of model capacity.',
  'What is the tradeoff between model complexity and generalization?',
];

function randomPrompt() {
  return PROMPTS[Math.floor(Math.random() * PROMPTS.length)];
}

export default function () {
  // Alternate between streaming and non-streaming to exercise both paths
  const isStreaming = __ITER % 3 === 0;

  const payload = JSON.stringify({
    model: 'default',
    messages: [{ role: 'user', content: randomPrompt() }],
    max_tokens: isStreaming ? 256 : 64,
    stream: isStreaming,
    temperature: 0.7,
  });

  const headers = { 'Content-Type': 'application/json' };
  if (API_KEY) headers['Authorization'] = `Bearer ${API_KEY}`;

  const startTime = Date.now();
  const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
    headers,
    timeout: '180s',
    responseType: isStreaming ? 'text' : undefined,
  });

  const elapsed = Date.now() - startTime;
  latency.add(elapsed);

  const isOk = res.status === 200;
  errorRate.add(!isOk);

  if (isOk && isStreaming) {
    let first = true;
    let chunks = 0;
    const events = res.body.split('\n\n');
    for (const event of events) {
      if (!event.trim()) continue;
      for (const line of event.split('\n')) {
        if (line.startsWith('data:')) {
          const data = line.slice(5).trim();
          if (data === '[DONE]') continue;
          if (data.startsWith('{"error"')) {
            errorRate.add(true);
            continue;
          }
          if (first) {
            ttft.add(Date.now() - startTime);
            first = false;
          }
          chunks++;
        }
      }
    }
    chunkCount.add(chunks);
  }

  check(res, {
    'status is 200': (r) => r.status === 200,
    'has response body': (r) => r.body.length > 0,
  });

  sleep(1);
}
