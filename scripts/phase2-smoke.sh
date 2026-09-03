#!/usr/bin/env bash
# Enqueue a book through Storage API and confirm the indexer wrote it to ES.
set -euo pipefail

STORAGE_URL="${STORAGE_URL:-http://localhost:8000}"
SEARCH_URL="${SEARCH_URL:-http://localhost:8001}"
ES_URL="${ES_URL:-http://localhost:9200}"
TITLE="the name of the wind $(date +%s)"

wait_for_storage() {
  echo "Waiting for Storage API at ${STORAGE_URL}..."
  for _ in $(seq 1 30); do
    if curl -sf "${STORAGE_URL}/health" >/dev/null; then
      echo "Storage API is up."
      return 0
    fi
    sleep 2
  done
  echo "Storage API did not become healthy in time." >&2
  exit 1
}

wait_for_storage

echo "Waiting for Search API at ${SEARCH_URL}..."
for _ in $(seq 1 30); do
  if curl -sf "${SEARCH_URL}/health" >/dev/null; then
    break
  fi
  sleep 2
done

echo
echo "== login as editor =="
LOGIN="$(curl -sf -X POST "${SEARCH_URL}/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"editor","password":"editorpass"}')"
TOKEN="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['token'])" "${LOGIN}")"

echo
echo "== POST /books =="
ENQUEUE="$(curl -sf -X POST "${STORAGE_URL}/books" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${TOKEN}" \
  -d "{
    \"title\": \"${TITLE}\",
    \"genre\": \"fantasy\",
    \"description\": \"a story of kvothe, the red-haired musician\"
  }")"
echo "${ENQUEUE}"

BOOK_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['id'])" "${ENQUEUE}")"
echo "queued id=${BOOK_ID}"

echo
echo "== wait for document in Elasticsearch =="
found=""
for i in $(seq 1 20); do
  RESULT="$(curl -sf "${ES_URL}/books/_doc/${BOOK_ID}" || true)"
  if echo "${RESULT}" | grep -q "\"found\":true"; then
    echo "${RESULT}" | python3 -m json.tool
    found="yes"
    break
  fi
  echo "  attempt ${i}: not searchable yet"
  sleep 1
done

if [ -z "${found}" ]; then
  echo "FAIL: book ${BOOK_ID} never appeared in Elasticsearch." >&2
  exit 1
fi

echo
echo "== search by title =="
SEARCH="$(curl -sf "${ES_URL}/books/_search" \
  -H "Content-Type: application/json" \
  -d "{
    \"query\": {
      \"multi_match\": {
        \"query\": \"${TITLE}\",
        \"fields\": [\"title\", \"description\"]
      }
    }
  }")"
echo "${SEARCH}" | python3 -m json.tool

if echo "${SEARCH}" | grep -q "${BOOK_ID}"; then
  echo
  echo "DONE: Storage API -> Redis -> indexer -> Elasticsearch works."
else
  echo
  echo "FAIL: document exists by id but search did not return it." >&2
  exit 1
fi
