# Building diary

Running log of phases, design choices, and what we learn while building. Brief stays in `context.md`. This file is the "what we decided and why."

---

## Design choices (so far)

| Decision | Choice | Why |
|---|---|---|
| Domain | E-books e-commerce | Two clear production flows: ingest + lexical search |
| Runtime | All services on this MacBook (16GB RAM) | Interview constraint; no cloud-managed cluster |
| Orchestration | **Docker Compose**, not Minikube | 90 minutes; faster start; lighter RAM; still can scale replicas |
| Inverted index | **Elasticsearch single-node** | Real Lucene index, familiar REST API, strong load/saturation story |
| ES memory | Heap **512MB–1GB** (`-Xms512m -Xmx512m`, bump to 1g if needed) | Default ES heap will crowd 16GB with Compose + APIs + Redis |
| ES topology | Single node, security off, **no Kibana** | ES 8 security and Kibana burn time and RAM |
| Queue | **Redis** (not Kafka) | Kafka is too heavy on a laptop |
| Ingest path | Storage API → queue → indexer → ES | Async ingest; Search API stays independent |
| Search path | Search API → ES → n candidates | Sync read path; easy to load-test |
| Reindex | Daily full batch reindex (later) | Production-shaped ops story; not needed to start |
| Not chosen: OpenSearch | Same RAM as ES, more plugin friction | No laptop win |
| Not chosen: embedded Lucene | Lightest, but we write all search code | Fallback only if ES will not start |
| Not chosen: Meilisearch / Typesense | Easy on RAM | Wrong story — not Lucene |
| Language | **Python** (FastAPI + a small worker) | Fastest to ship in 90 minutes; HTTP + Redis clients are boring-reliable |
| Queue type | **Redis LIST** (`books:ingest`) + `RPUSH` / `BLPOP` | Competing consumers when we scale indexer replicas; Streams + consumer groups later |
| Storage API | `POST /books` → 202 `{id, status: queued}` | Accepts the book, does not write ES itself |
| Indexer write | ES `_doc/{id}?refresh=wait_for` | Demo is immediately searchable; drop `wait_for` later for throughput |
| Search API | FastAPI `:8001` `GET /search?q=&n=` | Sync read path; clients never talk to ES; `n` caps candidates (default 10, max 50) |
| Query | `multi_match` on `title^2`, `description` | Title matches rank higher; no fuzziness in v1 |
| Auth | Seeded in-memory users + **Redis sessions** | No Postgres on the laptop; sessions survive API scale-out |
| Users | `editor` / `editorpass`, `shopper` / `shopperpass` | Search is public; only `editor` can `POST /books` |
| Frontend | One static page at Search API `/` (Folio) | Same origin for login, search, and add-book; not a SPA |
| Metrics | Prometheus scrapes Search API `/metrics` | RED + `search_es_took_seconds`; no Grafana in v1 |
| Load test | `scripts/load-search.py` (60s, 20 workers) | Client-side p50/p95/p99 + error rate |

**Fallback if ES will not start on the machine:** embedded Lucene (or in-process index) so we still have a running search path.

---

## Phase 1 — inverted index database

**Goal:** Elasticsearch is running locally and we can index a book and search it. No APIs or queue yet.

### Plan

1. Add a Compose service for Elasticsearch 8 (or 7.17 if 8 is painful).
2. Pin it to laptop size:
   - `discovery.type=single-node`
   - `ES_JAVA_OPTS=-Xms512m -Xmx512m`
   - `xpack.security.enabled=false`
3. Wait until ES is healthy (`GET /_cluster/health`).
4. Create one index: `books`.
5. Apply a simple mapping:
   - `title`: `text`
   - `genre`: `keyword`
   - `description`: `text`
6. Index a few sample docs by hand (curl / DevTools-equivalent).
7. Search with `multi_match` on `title` and `description` (e.g. `"Harry Potter"`).
8. Confirm we get candidates back. Phase 1 is done.

### Sample mapping

```json
{
  "mappings": {
    "properties": {
      "title":       { "type": "text" },
      "genre":       { "type": "keyword" },
      "description": { "type": "text" }
    }
  }
}
```

### Smoke tests

```http
PUT /books/_doc/1
{ "title": "harry potter", "genre": "fantasy", "description": "the boy who lives" }

GET /books/_search
{ "query": { "multi_match": { "query": "Harry Potter", "fields": ["title", "description"] } } }
```

### Done when

- `docker compose up` starts ES.
- `books` index exists with the mapping above.
- A known title query returns the sample doc.
- Heap stays in the 512MB–1GB band; machine remains usable.

### Talking points this phase unlocks

- Elasticsearch **is** Lucene (terms, postings, inverted index).
- **Refresh interval** explains why a just-written doc may not be searchable yet.
- Later saturation: heap, GC, query latency — not just "the container died."
- With more time: replicas, fuzzy match for `"J RR Tolkine"`, synonyms, Kibana.

### Explicitly not in Phase 1

- Search API / Storage API / indexer
- Redis
- Prometheus / Grafana
- Daily batch reindex
- Fuzzy / typo-tolerant search

---

## Phase 2 — Storage API + Redis + indexer

**Goal:** A client can `POST /books` and the book becomes searchable in Elasticsearch without the Storage API talking to ES.

### Plan

1. Add Redis 7 (Alpine, 128MB) to Compose.
2. Storage API (FastAPI `:8000`): validate book, assign UUID, `RPUSH` JSON onto `books:ingest`.
3. Indexer worker: `BLPOP` the queue, `PUT` into `books` with `refresh=wait_for`.
4. Indexer creates `books` on startup if missing (so Compose is self-contained).
5. ES write failures are requeued; poison JSON is dropped (DLQ is “more time”).
6. Smoke: enqueue a unique title, wait until it is gettable and searchable.

### Done when

- `POST /books` returns 202 with an `id`.
- Indexer consumes the message and writes that `_id` to ES.
- A title search returns the new document.
- `GET /queue/depth` is back to 0.

### Explicitly not in Phase 2

- Search API
- Auth, retries with backoff, dead-letter queue
- Redis Streams / consumer groups
- Daily batch reindex

---

## Phase 3 — Search API

**Goal:** Clients query `GET /search` and get n book candidates from Elasticsearch. They never talk to ES directly.

### Plan

1. Add `search-api` (FastAPI `:8001`) to Compose; no `container_name` so we can `--scale` later.
2. `GET /health` pings ES cluster health.
3. `GET /search?q=...&n=...` runs `multi_match` on `title^2` + `description`, returns n candidates with id, fields, and score.
4. Smoke: `q=Harry Potter` returns the Phase 1 sample doc.

### Done when

- Search API is healthy against ES.
- `GET /search?q=Harry+Potter` returns the harry potter candidate.
- Response includes `n`, `total`, `took_ms`, and scored candidates.

### Explicitly not in Phase 3

- Fuzzy / typo-tolerant queries (e.g. dedicated handling for `"J RR Tolkine"`)
- Auth, caching, ranking beyond ES `_score`
- Observability beyond `took_ms` in the response

---

## Phase 4 — frontend + auth

**Goal:** A usable catalog page. Anyone can search. Only an editor session can add books.

### Plan

1. Seed two users in Search API memory (`editor`, `shopper`); hash passwords with PBKDF2.
2. `POST /login` writes `session:{token}` in Redis (TTL 1h) and sets an HttpOnly cookie.
3. Storage API requires role `editor` on `POST /books`.
4. Search API serves `GET /` (Folio UI) and proxies editor `POST /books` to Storage API.
5. Shopper can log in but cannot ingest.

### Done when

- `GET http://localhost:8001/` serves the Folio page.
- Unauthenticated search still works.
- Unauthenticated `POST /books` is 401.
- Shopper `POST /books` is 403.
- Editor `POST /books` is 202 and the book becomes searchable.

### Explicitly not in Phase 4

- Postgres / signup / OAuth
- Observability dashboards
- Load-test / saturation

---

## Phase 5 — observe + 60s load

**Goal:** Search API exports Prometheus metrics; we can run a 60-second load and talk numbers.

### Done when

- `GET /metrics` has `http_requests_total` and `search_es_took_seconds`.
- Prometheus at `:9090` scrapes Search API (`health=up`).
- `./scripts/load-search.sh` prints rps, p95, errors.

---

## Later phases (not started)

- **Phase 6:** Raise concurrency until saturation, change one thing, re-measure.

---

## Log

### 2026-09-03 — kickoff

- Wrote `context.md` (challenge, architecture, Compose choice).
- Chose Elasticsearch single-node over OpenSearch and embedded Lucene for the 16GB laptop + 90-minute interview.
- Phase 1 plan: stand up ES, create `books`, index and search by hand.

### 2026-09-03 — Phase 1 complete

Stood up Elasticsearch 8.15.3 via `docker-compose.yml`:
- single-node, security off, ML off
- heap `512m`, container `mem_limit: 1g`
- port `9200`

Created `books` with 1 shard / 0 replicas and mapping `title` (text), `genre` (keyword), `description` (text). Indexed two sample docs (`harry potter`, `the hobbit`) with `refresh=wait_for`.

**Done when — checked:**
- `docker compose up` starts ES — healthy, cluster `green`
- `books` index exists with the planned mapping
- `multi_match` `"Harry Potter"` on `title` + `description` returned doc `1` (`harry potter`, score 1.386)
- Heap max **512MB**, used ~281MB (54%). Container ~946MB / 1GB. Machine still usable.

Repeat with `docker compose up -d` then `./scripts/phase1-setup.sh`.

Note: container RAM is close to the 1GB cap (off-heap + Lucene files). If we OOM later, raise `mem_limit` to 1.5g and keep heap at 512m.

### 2026-09-03 — Phase 2 complete

Added Redis + Storage API + indexer to Compose.

Flow: `POST http://localhost:8000/books` → Redis LIST `books:ingest` → indexer → `PUT /books/_doc/{id}?refresh=wait_for`.

**Done when — checked:**
- `POST /books` returned 202 `id=613733bd-de03-4e6e-9809-1567122311ba`
- Indexer log: `indexed book id=613733bd-... title='the name of the wind ...'`
- ES `_doc/{id}` found; `multi_match` on the title returned that id
- Queue depth returned to 0

Repeat with `docker compose up -d --build` then `./scripts/phase2-smoke.sh`.

Talking points: async ingest vs sync search; LIST competing consumers (`docker compose up --scale indexer=3`); `refresh=wait_for` buys visibility at the cost of write latency. More time: Streams, DLQ, idempotency keys, drop `wait_for`.

### 2026-09-03 — Phase 3 complete

Search API on `http://localhost:8001`. Clients call `GET /search?q=...&n=...`; the API queries ES and returns n candidates.

**Done when — checked:**
- `GET /health` → `{ "status": "ok", "elasticsearch": "green" }`
- `GET /search?q=Harry+Potter&n=5` → 1 candidate, `id=1`, title `harry potter`, `took_ms=36`
- `"J RR Tolkine"` still returned The Hobbit (token overlap on `J R R Tolkien` in description, score 0.83) — not real fuzzy typo tolerance

Repeat with `docker compose up -d --build search-api` then `./scripts/phase3-smoke.sh`.

Talking points: sync search vs async ingest; scale with `docker compose up --scale search-api=3` (need a load balancer later; Compose publishes 8001 on one replica unless we change ports). More time: fuzziness, synonyms, caching.

### 2026-09-03 — Phase 4 complete

Folio UI at `http://localhost:8001/`. Users live in process memory; sessions live in Redis. Search stays public; `POST /books` needs role `editor` at Storage API (Search API proxies for the page).

**Done when — checked:**
- Frontend HTML contains FOLIO
- Public `GET /search?q=Harry+Potter` still returns doc `1`
- Storage `POST /books` without token → 401
- Shopper token → 403
- Editor token → 202; book became searchable (`471586d6-...`)
- Indexer needed a restart after Redis was recreated; added `restart: unless-stopped`

Demo logins: `editor` / `editorpass`, `shopper` / `shopperpass`.

Repeat with `docker compose up -d --build` then `./scripts/phase4-smoke.sh`.

Talking points: Postgres would be the production user store; Redis is the right session store and we already had it. More time: signup, CSRF, HTTPS cookies.

### 2026-09-03 — seed catalog for the search demo

Added `data/books.json` (7 Harry Potter novels + 15 others). `scripts/seed-books.sh` logs in as `editor` and POSTs each book through Storage API so ingest stays on the real path. Deleted the generic Phase 1 `harry potter` doc (`_id=1`).

**Checked:** `GET /search?q=Harry+Potter&n=10` returns `total: 7` — all seven titles, ranked by `_score`.

Repeat with `./scripts/seed-books.sh`. Demo on http://localhost:8001/ — search `Harry Potter`.

This JSON stands in for a catalog extract. Daily full reindex would reload from the same kind of dump.

### 2026-09-03 — Phase 5 complete

Prometheus `prom/prometheus:v2.54.1` on `http://localhost:9090` scrapes `search-api:8001/metrics` every 5s. Search API uses `prometheus-fastapi-instrumentator` plus histogram `search_es_took_seconds`.

**60s load** (`concurrency=20`, `q=Harry Potter`):
- 10,120 requests, **168.7 rps**, **0 errors**
- p50 **83ms**, p95 **296ms**, p99 **495ms**, max **779ms**
- After the run: `http_requests_total{/search}` = 10121, ES heap still ~950MB / 1GB, Search API ~43MB

Not saturated yet (no errors). Next: raise `--concurrency` until p95/errors explode.

Repeat: `docker compose up -d --build` then `./scripts/load-search.sh`. Watch http://localhost:9090.
