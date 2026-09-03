#!/usr/bin/env bash
# Query Search API for a known title and confirm candidates come back.
set -euo pipefail

SEARCH_URL="${SEARCH_URL:-http://localhost:8001}"

wait_for_search() {
  echo "Waiting for Search API at ${SEARCH_URL}..."
  for _ in $(seq 1 30); do
    if curl -sf "${SEARCH_URL}/health" >/dev/null; then
      echo "Search API is up."
      return 0
    fi
    sleep 2
  done
  echo "Search API did not become healthy in time." >&2
  exit 1
}

wait_for_search

echo
echo "== GET /health =="
curl -sf "${SEARCH_URL}/health" | python3 -m json.tool

echo
echo "== GET /search?q=Harry+Potter&n=5 =="
RESULT="$(curl -sf "${SEARCH_URL}/search?q=Harry%20Potter&n=5")"
echo "${RESULT}" | python3 -m json.tool

if echo "${RESULT}" | grep -qi "harry potter"; then
  echo
  echo "DONE: Search API returned Harry Potter candidates from Elasticsearch."
else
  echo
  echo "FAIL: expected harry potter in Search API candidates." >&2
  exit 1
fi
