#!/usr/bin/env bash
# Create the books index, mapping, sample docs, and run Phase 1 smoke tests.
set -euo pipefail

ES_URL="${ES_URL:-http://localhost:9200}"

wait_for_es() {
  echo "Waiting for Elasticsearch at ${ES_URL}..."
  for i in $(seq 1 60); do
    if curl -sf "${ES_URL}/_cluster/health" >/dev/null; then
      echo "Elasticsearch is up."
      return 0
    fi
    sleep 2
  done
  echo "Elasticsearch did not become healthy in time." >&2
  exit 1
}

wait_for_es

echo
echo "== cluster health =="
curl -sf "${ES_URL}/_cluster/health?pretty"

echo
echo "== create index books (ignore if exists) =="
curl -sf -X PUT "${ES_URL}/books" \
  -H "Content-Type: application/json" \
  -d '{
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "refresh_interval": "1s"
    },
    "mappings": {
      "properties": {
        "title":       { "type": "text" },
        "genre":       { "type": "keyword" },
        "description": { "type": "text" }
      }
    }
  }' || curl -sf "${ES_URL}/books" >/dev/null

echo
echo "== mapping =="
curl -sf "${ES_URL}/books/_mapping?pretty"

echo
echo "== index sample docs (refresh=wait_for) =="
curl -sf -X PUT "${ES_URL}/books/_doc/1?refresh=wait_for" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "harry potter",
    "genre": "fantasy",
    "description": "the boy who lives"
  }'
echo
curl -sf -X PUT "${ES_URL}/books/_doc/2?refresh=wait_for" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "the hobbit",
    "genre": "fantasy",
    "description": "there and back again, a tale by J R R Tolkien"
  }'
echo

echo
echo "== search: Harry Potter =="
SEARCH_RESULT="$(curl -sf "${ES_URL}/books/_search?pretty" \
  -H "Content-Type: application/json" \
  -d '{
    "query": {
      "multi_match": {
        "query": "Harry Potter",
        "fields": ["title", "description"]
      }
    }
  }')"
echo "${SEARCH_RESULT}"

if echo "${SEARCH_RESULT}" | grep -q "harry potter"; then
  echo
  echo "DONE: books index is searchable; Harry Potter returned a candidate."
else
  echo
  echo "FAIL: expected harry potter in search results." >&2
  exit 1
fi
