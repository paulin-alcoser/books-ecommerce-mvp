# Demo script — Folio e-books

All services on this MacBook via Docker Compose. System diagram: [architecture.md](architecture.md).

| What | URL |
|---|---|
| Catalog UI | http://localhost:8001/ |
| Search API | http://localhost:8001/search |
| Storage API | http://localhost:8000 |
| Prometheus | http://localhost:9090 |
| Elasticsearch | http://localhost:9200 |

Demo users: `editor` / `editorpass` (can add books) · `shopper` / `shopperpass` (search only).

---

## 0. Start (if nothing is running)

```bash
docker compose up -d
./scripts/seed-books.sh    # only if the catalog is empty
```

Wait until Search API is up: http://localhost:8001/health

---

## 1. Query books (the product demo)

### In the browser

1. Open http://localhost:8001/
2. Do **not** log in yet. Search is public.
3. Search `Harry Potter`.
4. You should see **all 7 novels**, ranked by Elasticsearch `_score` (top 10 candidates, `n=10`).
5. Search `Tolkien` or `Dune` to show other titles from the seed catalog.
6. Log in as `shopper` / `shopperpass`. Add-book stays hidden (403 if they POST).
7. Log out. Log in as `editor` / `editorpass`. The **Add a book** panel appears.
8. Add a title (e.g. `Harry Potter and the Cursed Child`). Search again after ~1s — it went Storage API → Redis → indexer → ES.

Talking point: ingest is async; search is sync. The UI never talks to Elasticsearch.

### Same thing via curl

```bash
curl -s "http://localhost:8001/search?q=Harry%20Potter&n=10" | python3 -m json.tool
```

Expect `"total": 7` and seven `Harry Potter and the …` candidates.

---

## 2. Show we can observe the system

1. Open http://localhost:9090/targets  
   `search-api` should be **UP** (`http://search-api:8001/metrics`).
2. Optional raw metrics: http://localhost:8001/metrics  
   Look for `http_requests_total` and `search_es_took_seconds`.

---

## 3. Run the 60-second load test

In a terminal:

```bash
./scripts/load-search.sh
```

That hits `GET /search?q=Harry+Potter&n=10` for 60s with 20 concurrent workers.

While it runs (or right after), use Prometheus.

---

## 4. Prometheus queries (Graph → insert query → Execute)

Open http://localhost:9090/graph

**Request rate (search QPS)**

```promql
rate(http_requests_total{job="search-api", handler="/search"}[1m])
```

**Success vs errors**

```promql
sum by (status) (rate(http_requests_total{job="search-api", handler="/search"}[1m]))
```

**API latency (if the instrumentator histogram is present)**

```promql
histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{job="search-api", handler="/search"}[1m]))
```

```promql
histogram_quantile(0.50, rate(http_request_duration_seconds_bucket{job="search-api", handler="/search"}[1m]))
```

**Elasticsearch time inside the request (`took_ms`)**

```promql
histogram_quantile(0.95, rate(search_es_took_seconds_bucket[1m]))
```

**Total search requests since process start**

```promql
http_requests_total{job="search-api", handler="/search"}
```

If a graph is flat after the test, the 1m window has rolled off. Use `[5m]` or look at the load-script JSON in the terminal.

Also glance at `docker stats` — ES heap sits near 1GB; Search API stays small.

---

## 5. Load-test summary and saturation

**Healthy baseline:** 60s · 20 workers · 5s timeout · `q=Harry Potter`

| Metric | Value |
|---|---|
| Requests | 10,120 |
| Throughput | 168.7 rps |
| Errors | 0 |
| p50 / p95 / p99 | 83 / 296 / 495 ms |
| Search API | ~43 MB, ~40 worker threads (PIDs stuck at 41) |
| ES container | ~950 MB / 1 GB, CPU mostly idle |

**Ramp (25s, 5s timeout) — latency saturates, no HTTP 5xx**

| Concurrency | rps | p50 | p95 | errors |
|---|---|---|---|---|
| 20 (60s) | 169 | 83 ms | 296 ms | 0 |
| 50 | 337 | 126 ms | 295 ms | 0 |
| 80 | 334 | 216 ms | 408 ms | 0 |
| 120 | 385 | 296 ms | 503 ms | 0 |
| 200 | 361 | 512 ms | 923 ms | 0 |
| 400 | 425 | 901 ms | 1.1 s | 0 |
| 800 | 383 | 2.0 s | 2.9 s | 0 |

Throughput **peaks ~350–425 rps around 80–120 concurrent clients**, then latency grows with concurrency (queueing). That is the saturation point for capacity.

**Hard failure (1s client timeout = “users leave”)**

| Concurrency | rps | error rate | what failed |
|---|---|---|---|
| 200 | 306 | **18%** | timeouts (`status=0`) |
| 400 | 411 | **67%** | timeouts |
| 800 | 543 | **97%** | timeouts |

**What breaks**

- Search API uses sync handlers; default thread pool is ~40 (container PIDs stay at 41). Extra clients wait in line.
- ES heap is tight (93%) but CPU is idle — we did **not** crash ES; we queued in the API.
- No 5xx from the app. Failure is **latency / client timeout**, not a process death.

**What we would change**

- Run Search API with more uvicorn workers or `async` + httpx AsyncClient so we are not capped at ~40 threads.
- Cache `Harry Potter` in Redis (hot query).
- Then raise ES heap if the API is no longer the bottleneck.
