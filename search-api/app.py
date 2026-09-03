import os
from pathlib import Path

import httpx
import redis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from prometheus_client import Histogram
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel, Field

from sessions import (
    COOKIE_NAME,
    create_session,
    current_session,
    delete_session,
    extract_token,
    require_editor,
)
from users import authenticate

ES_URL = os.environ.get("ES_URL", "http://localhost:9200").rstrip("/")
ES_INDEX = os.environ.get("ES_INDEX", "books")
DEFAULT_N = int(os.environ.get("DEFAULT_N", "10"))
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
STORAGE_URL = os.environ.get("STORAGE_URL", "http://localhost:8000").rstrip("/")
STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Search API")
es = httpx.Client(timeout=5.0)
storage = httpx.Client(timeout=5.0)
rds = redis.Redis.from_url(REDIS_URL, decode_responses=True)

Instrumentator(
    should_group_status_codes=True,
    excluded_handlers=["/metrics"],
).instrument(app).expose(app, include_in_schema=False)

ES_TOOK = Histogram(
    "search_es_took_seconds",
    "Elasticsearch query time reported in took_ms",
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)


class LoginIn(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class BookIn(BaseModel):
    title: str = Field(min_length=1)
    genre: str = ""
    description: str = ""


def _session_cookie(response: JSONResponse, token: str) -> JSONResponse:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        path="/",
        max_age=int(os.environ.get("SESSION_TTL_SECONDS", "3600")),
    )
    return response


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
def health():
    try:
        resp = es.get(f"{ES_URL}/_cluster/health")
        resp.raise_for_status()
        body = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"elasticsearch unavailable: {exc}") from exc
    try:
        rds.ping()
        redis_status = "ok"
    except redis.RedisError:
        redis_status = "down"
    return {
        "status": "ok" if redis_status == "ok" else "degraded",
        "elasticsearch": body.get("status"),
        "redis": redis_status,
    }


@app.post("/login")
def login(body: LoginIn):
    user = authenticate(body.username.strip(), body.password)
    if not user:
        raise HTTPException(status_code=401, detail="invalid credentials")
    try:
        token = create_session(rds, user["username"], user["role"])
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail=f"session store unavailable: {exc}") from exc
    payload = {"token": token, "username": user["username"], "role": user["role"]}
    return _session_cookie(JSONResponse(payload), token)


@app.post("/logout")
def logout(request: Request):
    delete_session(rds, extract_token(request))
    response = JSONResponse({"status": "logged_out"})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@app.get("/me")
def me(request: Request):
    session = current_session(rds, request)
    if not session:
        return {"authenticated": False}
    return {"authenticated": True, **session}


@app.get("/search")
def search(
    q: str = Query(min_length=1),
    n: int = Query(default=DEFAULT_N, ge=1, le=50),
):
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="q must not be blank")

    try:
        resp = es.post(
            f"{ES_URL}/{ES_INDEX}/_search",
            json={
                "size": n,
                "query": {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^2", "description"],
                    }
                },
            },
        )
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"search failed: {exc}") from exc

    body = resp.json()
    hits = body.get("hits", {})
    candidates = []
    for hit in hits.get("hits", []):
        source = hit.get("_source", {})
        candidates.append(
            {
                "id": hit.get("_id"),
                "title": source.get("title", ""),
                "genre": source.get("genre", ""),
                "description": source.get("description", ""),
                "score": hit.get("_score"),
            }
        )

    total = hits.get("total", {})
    ES_TOOK.observe((body.get("took") or 0) / 1000.0)
    return {
        "query": query,
        "n": n,
        "total": total.get("value", len(candidates)),
        "took_ms": body.get("took"),
        "candidates": candidates,
    }


@app.post("/books", status_code=202)
def add_book(book: BookIn, request: Request):
    require_editor(rds, request)
    token = extract_token(request)
    try:
        resp = storage.post(
            f"{STORAGE_URL}/books",
            json=book.model_dump(),
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail=f"storage unavailable: {exc}") from exc
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text)
    return resp.json()
