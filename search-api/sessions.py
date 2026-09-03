import json
import os
import secrets

import redis
from fastapi import HTTPException, Request

COOKIE_NAME = "books_session"
SESSION_PREFIX = "session:"
SESSION_TTL = int(os.environ.get("SESSION_TTL_SECONDS", "3600"))


def extract_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth.split(" ", 1)[1].strip()
        if token:
            return token
    return request.cookies.get(COOKIE_NAME)


def create_session(rds: redis.Redis, username: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    payload = json.dumps({"username": username, "role": role})
    rds.setex(f"{SESSION_PREFIX}{token}", SESSION_TTL, payload)
    return token


def load_session(rds: redis.Redis, token: str | None) -> dict | None:
    if not token:
        return None
    raw = rds.get(f"{SESSION_PREFIX}{token}")
    if not raw:
        return None
    return json.loads(raw)


def delete_session(rds: redis.Redis, token: str | None) -> None:
    if token:
        rds.delete(f"{SESSION_PREFIX}{token}")


def current_session(rds: redis.Redis, request: Request) -> dict | None:
    return load_session(rds, extract_token(request))


def require_session(rds: redis.Redis, request: Request) -> dict:
    session = current_session(rds, request)
    if not session:
        raise HTTPException(status_code=401, detail="login required")
    return session


def require_editor(rds: redis.Redis, request: Request) -> dict:
    session = require_session(rds, request)
    if session.get("role") != "editor":
        raise HTTPException(status_code=403, detail="editor role required")
    return session
