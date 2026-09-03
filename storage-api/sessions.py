import json

import redis
from fastapi import HTTPException, Request

COOKIE_NAME = "books_session"
SESSION_PREFIX = "session:"


def extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        if token:
            return token
    return request.cookies.get(COOKIE_NAME)


def load_session(rds: redis.Redis, token: str | None) -> dict | None:
    if not token:
        return None
    raw = rds.get(f"{SESSION_PREFIX}{token}")
    if not raw:
        return None
    return json.loads(raw)


def require_session(rds: redis.Redis, request: Request) -> dict:
    session = load_session(rds, extract_token(request))
    if not session:
        raise HTTPException(status_code=401, detail="login required")
    return session


def require_editor(rds: redis.Redis, request: Request) -> dict:
    session = require_session(rds, request)
    if session.get("role") != "editor":
        raise HTTPException(status_code=403, detail="editor role required")
    return session
