#!/usr/bin/env bash
# Frontend is served, search is public, writes require an editor session.
set -euo pipefail

SEARCH_URL="${SEARCH_URL:-http://localhost:8001}"
STORAGE_URL="${STORAGE_URL:-http://localhost:8000}"
TITLE="phase4 catalog ${RANDOM}"

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
echo "== GET / frontend =="
if ! curl -sf "${SEARCH_URL}/" | grep -q "FOLIO"; then
  echo "FAIL: frontend did not contain FOLIO." >&2
  exit 1
fi
echo "frontend ok"

echo
echo "== public search still works =="
curl -sf "${SEARCH_URL}/search?q=Harry%20Potter&n=3" | python3 -m json.tool | head -20

echo
echo "== unauthenticated POST /books is 401 =="
CODE="$(curl -s -o /tmp/p4-unauth.json -w "%{http_code}" -X POST "${STORAGE_URL}/books" \
  -H "Content-Type: application/json" \
  -d '{"title":"nope","genre":"x","description":"x"}')"
echo "storage status=${CODE}"
if [ "${CODE}" != "401" ]; then
  echo "FAIL: expected 401 from storage." >&2
  cat /tmp/p4-unauth.json
  exit 1
fi

echo
echo "== shopper cannot add books =="
SHOPPER="$(curl -sf -X POST "${SEARCH_URL}/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"shopper","password":"shopperpass"}')"
SHOPPER_TOKEN="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['token'])" "${SHOPPER}")"
CODE="$(curl -s -o /tmp/p4-shopper.json -w "%{http_code}" -X POST "${SEARCH_URL}/books" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${SHOPPER_TOKEN}" \
  -d "{\"title\":\"${TITLE}\",\"genre\":\"fantasy\",\"description\":\"should be forbidden\"}")"
echo "shopper status=${CODE}"
if [ "${CODE}" != "403" ]; then
  echo "FAIL: expected 403 for shopper." >&2
  cat /tmp/p4-shopper.json
  exit 1
fi

echo
echo "== editor can add books through Search API =="
EDITOR="$(curl -sf -X POST "${SEARCH_URL}/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"editor","password":"editorpass"}')"
EDITOR_TOKEN="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['token'])" "${EDITOR}")"
ENQUEUE="$(curl -sf -X POST "${SEARCH_URL}/books" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${EDITOR_TOKEN}" \
  -d "{\"title\":\"${TITLE}\",\"genre\":\"fantasy\",\"description\":\"added from the folio ui path\"}")"
echo "${ENQUEUE}"
BOOK_ID="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['id'])" "${ENQUEUE}")"

found=""
for i in $(seq 1 20); do
  RESULT="$(curl -sf "${SEARCH_URL}/search?q=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "${TITLE}")&n=5")"
  if echo "${RESULT}" | grep -q "${BOOK_ID}"; then
    echo "${RESULT}" | python3 -m json.tool
    found="yes"
    break
  fi
  echo "  attempt ${i}: waiting for index"
  sleep 1
done

if [ -z "${found}" ]; then
  echo "FAIL: editor-queued book never became searchable." >&2
  exit 1
fi

echo
echo "DONE: public search, shopper denied, editor ingest via frontend origin."
