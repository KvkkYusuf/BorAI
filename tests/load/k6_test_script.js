import http from 'k6/http';
import { check, sleep } from 'k6';

/**
 * k6 Load Test Script for Borsa Analiz Sistemi API
 *
 * Verifies SLAs defined in docs/ACCEPTANCE_CRITERIA.md:
 * - Cached API p95 < 300ms
 * - Uncached API p95 < 800ms
 * - Error rate < 1% (target: 0 server errors)
 *
 * Run:
 *   k6 run --vus 20 --duration 30s tests/load/k6_test_script.js
 */

export const options = {
  stages: [
    { duration: '5s', target: 10 },  // Ramp-up
    { duration: '20s', target: 20 }, // Steady state load
    { duration: '5s', target: 0 },   // Ramp-down
  ],
  thresholds: {
    // docs/ACCEPTANCE_CRITERIA.md p95 limits
    'http_req_duration{type:cached}': ['p(95)<300'],
    'http_req_duration{type:uncached}': ['p(95)<800'],
    'http_req_failed': ['rate==0'], // No 500 or unhandled failures
  },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export default function () {
  // 1. Cached Symbols Request
  const symbolsRes = http.get(`${BASE_URL}/api/v1/market/symbols?page=1&page_size=20`, {
    tags: { type: 'cached', name: 'GET /symbols' },
  });
  check(symbolsRes, {
    'symbols status is 200': (r) => r.status === 200,
  });

  sleep(0.1);

  // 2. Cached Price History Query
  const cachedHistoryRes = http.get(`${BASE_URL}/api/v1/market/history/THYAO?range=1m&interval=1d&price_type=adjusted`, {
    tags: { type: 'cached', name: 'GET /history/THYAO (cached)' },
  });
  check(cachedHistoryRes, {
    'cached history status is 200': (r) => r.status === 200,
  });

  sleep(0.2);

  // 3. Uncached Price History Query
  const uncachedHistoryRes = http.get(`${BASE_URL}/api/v1/market/history/ASELS?range=5y&interval=1d&price_type=adjusted`, {
    tags: { type: 'uncached', name: 'GET /history/ASELS (uncached)' },
  });
  check(uncachedHistoryRes, {
    'uncached history status is 200': (r) => r.status === 200,
  });

  sleep(0.1);
}
