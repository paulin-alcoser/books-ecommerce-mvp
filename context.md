# Context: E-Books E-Commerce Infra Challenge

## Interview constraints

- Onsite interview.
- **90 minutes** to solve this infra challenge.
- Any language, environment, and tooling is allowed.
- Deliverable must be a **running system**. Services, databases, and telemetry of our choosing are allowed.

## Runtime constraint

All services must run on this MacBook. No cloud-managed cluster for the demo.

**Choice: Docker Compose, not Minikube.**

Reason: 90 minutes, need a running system, need to scale a service and find saturation. Compose is faster to start, easier to debug, and lighter on RAM. Minikube would better match production orchestration but burns time and memory without buying much for a single-node laptop demo.

Local topology in Compose:
- search-api
- storage-api
- indexer (scale replicas under load)
- queue (Redis preferred over Kafka on a laptop)
- inverted index (OpenSearch/Elasticsearch, or embedded Lucene if RAM is tight)
- observability (Prometheus/Grafana or equivalent)

Scaling demo: increase search-api and/or indexer replicas, load-test Search API, watch latency, error rate, CPU, and queue lag until the service saturates.

If we had more time: move the same topology to Kubernetes (Deployments, probes, HPA) — that is the production path, not the v1 path.

## Challenge expectations

Build a small but realistic distributed system that demonstrates how we **design, operate, and observe** a production environment.

At the end of the exercise, be able to:

1. Share the code (zip file or GitHub repo).
2. Share how it was designed.
3. Share how we observe the system.
4. Demonstrate scaling of the system and find the **saturation point** of the service.
5. Describe what breaks under load.
6. Describe what changes were made to improve it.
7. Describe what we would do with more time.

## Project choice

**E-books e-commerce.**

Two main functionalities:

1. **Feeding** — ingest e-books into the system.
2. **Retrieving** — lexical search over those books using a Lucene inverted index (OpenSearch / Elasticsearch / Lucene).

## First iteration

### Search API

Receives queries that simulate e-books commerce search, for example:

- `"Harry Potter"`
- `"J RR Tolkine"` (typos / messy user input are expected)

Behavior:

- Look up books that match the query in the inverted index (Lucene / OpenSearch / Elasticsearch).
- Return **n candidates**.

### Storage API

Clients use this API to add books to the inverted index.

Example payload:

```json
{
  "title": "harry potter",
  "genre": "fantasy",
  "description": "the boy who lives etc"
}
```

Flow:

1. Storage API accepts the book.
2. Storage API submits it to a queue (Kafka, Redis, or similar).
3. An **indexer** worker consumes the queued book.
4. The indexer writes the book into the inverted index database.

### Batch reindex

Once a day, run a **full batch reindex**.

## Target architecture (v1)

```
Client
  │
  ├── POST /books ──────────► Storage API ──► Queue (Kafka / Redis)
  │                                              │
  │                                              ▼
  │                                           Indexer ──► Inverted Index
  │                                                          (Lucene /
  │                                                      OpenSearch /
  │                                                      Elasticsearch)
  │
  └── GET /search?q=... ───► Search API ─────────────────► Inverted Index
                                                              │
                                                              ▼
                                                         n candidates

Daily cron / scheduler ──► Full batch reindex ──► Inverted Index
```

## Components to implement in v1

| Component | Responsibility |
|---|---|
| Search API | Accept query strings, search inverted index, return n candidates |
| Storage API | Accept book documents, enqueue them for indexing |
| Queue | Buffer ingested books (Kafka or Redis) |
| Indexer | Consume queue, write documents into the inverted index |
| Inverted index | Lexical search store (Lucene / OpenSearch / Elasticsearch) |
| Batch reindex | Daily full rebuild of the index |

## Book document (v1)

```json
{
  "title": "string",
  "genre": "string",
  "description": "string"
}
```

Search should match against title (and likely description / genre as the index grows).

## What we must be ready to talk about

- **Design:** APIs, async ingest vs sync search, queue + worker, why inverted index, daily full reindex.
- **Observe:** metrics, logs, traces; query latency; queue lag; index write rate; error rate.
- **Scale / saturation:** load-test Search API (and ingest if time), find the point where latency / errors / queue lag explode.
- **What breaks under load:** likely search latency, indexer lag, queue backlog, index refresh / heap, CPU on query nodes.
- **Improvements made during the 90 minutes:** whatever we actually change after the first load test.
- **If we had more time:** HA, replicas, relevance tuning, fuzzy search, idempotent ingest, dead-letter queue, incremental vs full reindex, auth, multi-tenant catalogs, etc.

## Out of scope for v1 (unless time remains)

- Payments, cart, recommendations, user accounts.
- Semantic / vector search.
- Perfect typo tolerance (worth mentioning as a follow-up; queries like `"J RR Tolkine"` make the gap obvious).
- Multi-region, full HA, or production-grade security.
