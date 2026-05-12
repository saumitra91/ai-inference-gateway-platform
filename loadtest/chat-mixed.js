// k6 mixed traffic test — concurrent streaming and non-streaming requests
// Usage:
//   K6_API_KEY="sk_local_..." k6 run loadtest/chat-mixed.js
//
// This test runs streaming and non-streaming scenarios concurrently with
// separate metric tracking for each path. Use this to observe how the
// gateway handles mixed workloads and whether one path starves the other.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend, Counter } from 'k6/metrics';

const BASE_URL = __ENV.K6_BASE_URL || 'http://localhost:8888';
const API_KEY = __ENV.K6_API_KEY || '';

const streamErrorRate = new Rate('stream_errors');
const nonstreamErrorRate = new Rate('nonstream_errors');
const streamLatency = new Trend('stream_latency_ms');
const nonstreamLatency = new Trend('nonstream_latency_ms');
const streamTtft = new Trend('stream_ttft_ms');
const streamChunks = new Counter('stream_chunks_total');
const streamBytes = new Counter('stream_bytes_total');

export const options = {
  scenarios: {
    streaming: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 4 },
        { duration: '2m', target: 4 },
        { duration: '30s', target: 0 },
      ],
      gracefulStop: '30s',
      env: { SCENARIO: 'streaming' },
    },
    nonstreaming: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 6 },
        { duration: '2m', target: 6 },
        { duration: '30s', target: 0 },
      ],
      gracefulStop: '10s',
      env: { SCENARIO: 'nonstreaming' },
    },
  },
  thresholds: {
    stream_errors: ['rate<0.05'],
    nonstream_errors: ['rate<0.02'],
  },
};

const STREAM_PROMPTS = [
  'Explain the concept of attention in transformer neural networks.',
  'Write a short poem about machine learning.',
  'Describe how gradient descent works in simple terms.',
  'What is the tradeoff between bias and variance in machine learning?',
];

const NONSTREAM_PROMPTS = [
  'What is a transformer model?',
  'What is transfer learning?',
  'Explain the vanishing gradient problem.',
  'What is the purpose of an attention mechanism?',
  'Compare RNNs and transformers.',
  'What are residual connections?',
  'Explain the encoder-decoder architecture.',
  'What is the difference between precision and recall?',
];

export default function () {
  const scenario = __ENV.SCENARIO;

  if (scenario === 'streaming') {
    const payload = JSON.stringify({
      model: 'default',
      messages: [{ role: 'user', content: STREAM_PROMPTS[Math.floor(Math.random() * STREAM_PROMPTS.length)] }],
      max_tokens: 128,
      stream: true,
      temperature: 0.7,
    });

    const headers = { 'Content-Type': 'application/json' };
    if (API_KEY) headers['Authorization'] = `Bearer ${API_KEY}`;

    const startTime = Date.now();
    let firstChunk = null;
    let chunkCount = 0;
    let totalBytes = 0;
    let foundDone = false;

    const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
      headers,
      timeout: '120s',
      responseType: 'text',
    });

    if (res.status !== 200) {
      streamErrorRate.add(1);
      return;
    }

    const events = res.body.split('\n\n');
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
            streamErrorRate.add(1);
            continue;
          }
          if (firstChunk === null) {
            firstChunk = Date.now();
            streamTtft.add(firstChunk - startTime);
          }
          chunkCount++;
          totalBytes += data.length;
        }
      }
    }

    streamChunks.add(chunkCount);
    streamBytes.add(totalBytes);
    streamLatency.add(Date.now() - startTime);

    check(res, {
      'stream status is 200': (r) => r.status === 200,
      'stream has chunks': () => chunkCount > 0,
      'stream has DONE sentinel': () => foundDone,
    });

    sleep(0.5);
  }

  if (scenario === 'nonstreaming') {
    const payload = JSON.stringify({
      model: 'default',
      messages: [{ role: 'user', content: NONSTREAM_PROMPTS[Math.floor(Math.random() * NONSTREAM_PROMPTS.length)] }],
      max_tokens: 64,
      stream: false,
      temperature: 0.7,
    });

    const headers = { 'Content-Type': 'application/json' };
    if (API_KEY) headers['Authorization'] = `Bearer ${API_KEY}`;

    const res = http.post(`${BASE_URL}/v1/chat/completions`, payload, {
      headers,
      timeout: '60s',
    });

    nonstreamLatency.add(res.timings.duration);

    const isOk = res.status === 200;
    nonstreamErrorRate.add(!isOk);

    if (isOk) {
      try {
        const body = JSON.parse(res.body);
        check(res, {
          'nonstream has choices': () => (body.choices || []).length > 0,
        });
      } catch (e) {
        nonstreamErrorRate.add(true);
      }
    }

    sleep(0.2);
  }
}
