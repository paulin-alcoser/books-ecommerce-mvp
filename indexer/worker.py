import json
import os
import time

import httpx
import redis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
QUEUE_KEY = os.environ.get("QUEUE_KEY", "books:ingest")
ES_URL = os.environ.get("ES_URL", "http://localhost:9200").rstrip("/")
ES_INDEX = os.environ.get("ES_INDEX", "books")

BOOKS_INDEX_BODY = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
        "refresh_interval": "1s",
    },
    "mappings": {
        "properties": {
            "title": {"type": "text"},
            "genre": {"type": "keyword"},
            "description": {"type": "text"},
        }
    },
}


def wait_for_es(client: httpx.Client) -> None:
    for _ in range(60):
        try:
            resp = client.get(f"{ES_URL}/_cluster/health")
            if resp.status_code == 200:
                print("elasticsearch is up", flush=True)
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise RuntimeError("elasticsearch did not become ready")


def ensure_index(client: httpx.Client) -> None:
    resp = client.head(f"{ES_URL}/{ES_INDEX}")
    if resp.status_code == 200:
        print(f"index {ES_INDEX} already exists", flush=True)
        return
    created = client.put(f"{ES_URL}/{ES_INDEX}", json=BOOKS_INDEX_BODY)
    created.raise_for_status()
    print(f"created index {ES_INDEX}", flush=True)


def index_book(client: httpx.Client, book: dict) -> None:
    book_id = book["id"]
    document = {
        "title": book["title"],
        "genre": book.get("genre", ""),
        "description": book.get("description", ""),
    }
    resp = client.put(
        f"{ES_URL}/{ES_INDEX}/_doc/{book_id}",
        params={"refresh": "wait_for"},
        json=document,
    )
    resp.raise_for_status()
    print(f"indexed book id={book_id} title={document['title']!r}", flush=True)


def main() -> None:
    rds = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    rds.ping()
    print(f"consuming {QUEUE_KEY}", flush=True)

    with httpx.Client(timeout=10.0) as client:
        wait_for_es(client)
        ensure_index(client)

        while True:
            item = rds.blpop(QUEUE_KEY, timeout=5)
            if item is None:
                continue
            _, raw = item
            try:
                book = json.loads(raw)
                index_book(client, book)
            except (json.JSONDecodeError, KeyError) as exc:
                print(f"dropping poison message: {exc} raw={raw!r}", flush=True)
            except httpx.HTTPError as exc:
                print(f"elasticsearch write failed, requeueing: {exc}", flush=True)
                rds.rpush(QUEUE_KEY, raw)
                time.sleep(1)


if __name__ == "__main__":
    main()
