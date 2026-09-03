# System design — Folio e-books

Laptop topology. Everything runs in Docker Compose on one MacBook (16GB). This is the v1 we demo, not a multi-region production cluster.

---

## Container view

```mermaid
flowchart LR
  subgraph clients [Clients]
    Browser["Browser / Folio UI"]
    Curl["curl / load-search.py"]
  end

  subgraph compose [Docker Compose]
    SearchAPI["Search API :8001<br/>FastAPI · Folio · login · /metrics"]
    StorageAPI["Storage API :8000<br/>FastAPI · POST /books"]
    Indexer["Indexer<br/>BLPOP worker"]
    Redis["Redis :6379<br/>LIST books:ingest<br/>session:token"]
    ES["Elasticsearch :9200<br/>index books<br/>Lucene inverted index"]
    Prom["Prometheus :9090"]
  end

  Browser -->|"GET /  GET /search  POST /login"| SearchAPI
  Curl -->|"GET /search"| SearchAPI
  SearchAPI -->|"multi_match title^2, description"| ES
  SearchAPI -->|"session SET/GET"| Redis
  SearchAPI -->|"POST /books Bearer editor"| StorageAPI
  StorageAPI -->|"require editor"| Redis
  StorageAPI -->|"RPUSH books:ingest"| Redis
  Indexer -->|"BLPOP"| Redis
  Indexer -->|"PUT /books/_doc/id"| ES
  Prom -->|"scrape /metrics every 5s"| SearchAPI
```

**Read path is sync. Write path is async.** Clients never talk to Elasticsearch or Redis.

| Service | Port | Role |
|---|---|---|
| Folio + Search API | 8001 | Public search, login, editor proxy to Storage API, `/metrics` |
| Storage API | 8000 | Authenticated ingest only (`editor` role) |
| Redis | 6379 | Ingest queue (`books:ingest`) + sessions |
| Indexer | — | Competing consumer; writes Lucene/ES |
| Elasticsearch | 9200 | Single-node inverted index (`books`) |
| Prometheus | 9090 | Scrapes Search API |

Users are seeded in Search API memory (`editor`, `shopper`). Sessions live in Redis so they survive API restarts.

---

## Ingest sequence

Editor adds a book. Search API is a BFF for the browser; Storage API is the source of write truth.

```mermaid
sequenceDiagram
  actor Editor
  participant UI as Folio / Search API
  participant Store as Storage API
  participant Q as Redis LIST
  participant W as Indexer
  participant ES as Elasticsearch

  Editor->>UI: POST /login editor
  UI->>Q: SET session:token TTL 1h
  UI-->>Editor: cookie + token

  Editor->>UI: POST /books
  UI->>Q: load session, require role=editor
  UI->>Store: POST /books Authorization Bearer
  Store->>Q: require role=editor
  Store->>Q: RPUSH books:ingest {id,title,genre,description}
  Store-->>UI: 202 {id, status: queued}
  UI-->>Editor: 202

  W->>Q: BLPOP books:ingest
  W->>ES: PUT /books/_doc/{id}?refresh=wait_for
  Note over ES: Lucene inverted index<br/>now searchable
```

Shopper sessions get **403**. No token gets **401**.

---

## Search sequence

Anyone can search. Default `n=10` (max 50). Results are the top-n BM25 scores.

```mermaid
sequenceDiagram
  actor Shopper
  participant UI as Folio / Search API
  participant ES as Elasticsearch

  Shopper->>UI: GET /search?q=Harry Potter&n=10
  UI->>ES: multi_match title^2, description
  ES-->>UI: hits + took_ms + _score
  UI-->>Shopper: {total, took_ms, candidates[]}
```

Seed catalog (`data/books.json`) is 7 Harry Potter novels plus 15 others, fed through the ingest path (`./scripts/seed-books.sh`).

---

## Observe and load

```mermaid
flowchart LR
  Load["load-search.py<br/>60s · 20 workers"] --> SearchAPI["Search API"]
  SearchAPI --> ES["Elasticsearch"]
  SearchAPI -->|"http_requests_total<br/>http_request_duration_seconds<br/>search_es_took_seconds"| Metrics["/metrics"]
  Prom["Prometheus"] --> Metrics
```

Baseline we measured: **~169 rps**, p95 **296 ms**, **0 errors**. Not saturated yet. Raise `CONCURRENCY` until p95 or 5xxs break — that is the saturation point.

Likely bottleneck on this laptop: ES heap (512MB inside a 1GB container), not Search API RAM.

---

## What this is not (yet)

- Kubernetes / HPA (Compose is the 90-minute choice)
- Postgres users (in-memory users + Redis sessions)
- Kafka (Redis LIST competing consumers instead)
- Fuzzy typo search, daily full reindex job, Grafana, traces

Those are the “if we had more time” items. Daily full reindex would rebuild `books` from a catalog extract like `data/books.json`.
