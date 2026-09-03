#!/usr/bin/env bash
# 60s Search API load test (override with DURATION / CONCURRENCY).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "${ROOT}/scripts/load-search.py" \
  --url "${SEARCH_URL:-http://localhost:8001}" \
  --q "${QUERY:-Harry Potter}" \
  --duration "${DURATION:-60}" \
  --concurrency "${CONCURRENCY:-20}"
