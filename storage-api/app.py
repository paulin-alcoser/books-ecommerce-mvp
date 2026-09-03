import json
import os
import uuid

import redis
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from sessions import require_editor

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
QUEUE_KEY = os.environ.get("QUEUE_KEY", "books:ingest")

app = FastAPI(title="Storage API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
rds = redis.Redis.from_url(REDIS_URL, decode_responses=True)


class BookIn(BaseModel):
    title: str = Field(min_length=1)
    genre: str = ""
    description: str = ""


@app.get("/health")
def health():
    try:
        rds.ping()
        return {"status": "ok", "redis": "ok", "queue_depth": rds.llen(QUEUE_KEY)}
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc


@app.get("/queue/depth")
def queue_depth():
    try:
        return {"queue": QUEUE_KEY, "depth": rds.llen(QUEUE_KEY)}
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"redis unavailable: {exc}") from exc


@app.post("/books", status_code=202)
def enqueue_book(book: BookIn, request: Request):
    require_editor(rds, request)
    book_id = str(uuid.uuid4())
    payload = {
        "id": book_id,
        "title": book.title.strip(),
        "genre": book.genre.strip(),
        "description": book.description.strip(),
    }
    try:
        rds.rpush(QUEUE_KEY, json.dumps(payload))
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc
    return {"id": book_id, "status": "queued"}
