#!/usr/bin/env bash
# Login as editor and feed data/books.json through Storage API -> Redis -> indexer.
set -euo pipefail

SEARCH_URL="${SEARCH_URL:-http://localhost:8001}"
STORAGE_URL="${STORAGE_URL:-http://localhost:8000}"
ES_URL="${ES_URL:-http://localhost:9200}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BOOKS_JSON="${BOOKS_JSON:-${ROOT}/data/books.json}"

wait_for() {
  local url="$1"
  local name="$2"
  echo "Waiting for ${name} at ${url}..."
  for _ in $(seq 1 30); do
    if curl -sf "${url}" >/dev/null; then
      echo "${name} is up."
      return 0
    fi
    sleep 2
  done
  echo "${name} did not become healthy in time." >&2
  exit 1
}

wait_for "${SEARCH_URL}/health" "Search API"
wait_for "${STORAGE_URL}/health" "Storage API"

echo
echo "== remove generic Phase 1 harry potter doc =="
curl -sf -X DELETE "${ES_URL}/books/_doc/1?refresh=wait_for" -o /tmp/seed-delete.json || true
python3 -m json.tool /tmp/seed-delete.json 2>/dev/null || echo "no doc id=1 (already gone)"

echo
echo "== login as editor =="
LOGIN="$(curl -sf -X POST "${SEARCH_URL}/login" \
  -H "Content-Type: application/json" \
  -d '{"username":"editor","password":"editorpass"}')"
TOKEN="$(python3 -c "import json,sys; print(json.loads(sys.argv[1])['token'])" "${LOGIN}")"
echo "session ok"

echo
echo "== POST each book from ${BOOKS_JSON} =="
python3 - "${BOOKS_JSON}" "${STORAGE_URL}" "${TOKEN}" <<'PY'
import json, sys, urllib.request

path, storage_url, token = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, encoding="utf-8") as f:
    books = json.load(f)

ok = 0
for book in books:
    req = urllib.request.Request(
        f"{storage_url}/books",
        data=json.dumps(book).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        body = json.loads(resp.read().decode())
    ok += 1
    print(f"  queued {body['id']}  {book['title']}")

print(f"queued {ok} books")
PY

echo
echo "== wait until Harry Potter returns 7 titles =="
found=""
for i in $(seq 1 30); do
  RESULT="$(curl -sf "${SEARCH_URL}/search?q=Harry%20Potter&n=10")"
  COUNT="$(python3 -c "import json,sys; hits=json.loads(sys.argv[1])['candidates']; print(sum(1 for h in hits if 'harry potter' in h['title'].lower()))" "${RESULT}")"
  echo "  attempt ${i}: harry potter titles=${COUNT}"
  if [ "${COUNT}" -ge 7 ]; then
    echo "${RESULT}" | python3 -m json.tool
    found="yes"
    break
  fi
  sleep 1
done

if [ -z "${found}" ]; then
  echo "FAIL: expected 7 Harry Potter titles in top-10 search." >&2
  exit 1
fi

echo
echo "DONE: catalog seeded through the ingest path. Search Harry Potter on http://localhost:8001/"
