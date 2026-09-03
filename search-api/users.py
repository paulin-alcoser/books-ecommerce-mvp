import hashlib
import hmac
import os

PASSWORD_SALT = os.environ.get("PASSWORD_SALT", "books-ecom-demo-salt")
# username:password:role, comma-separated
DEMO_USERS = os.environ.get(
    "DEMO_USERS",
    "editor:editorpass:editor,shopper:shopperpass:shopper",
)


def hash_password(password: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode(),
        PASSWORD_SALT.encode(),
        120_000,
    ).hex()


def load_users() -> dict[str, dict]:
    users: dict[str, dict] = {}
    for entry in DEMO_USERS.split(","):
        parts = entry.strip().split(":")
        if len(parts) != 3:
            continue
        username, password, role = parts
        users[username] = {"password_hash": hash_password(password), "role": role}
    return users


USERS = load_users()


def authenticate(username: str, password: str) -> dict | None:
    user = USERS.get(username)
    if not user:
        return None
    candidate = hash_password(password)
    if not hmac.compare_digest(candidate, user["password_hash"]):
        return None
    return {"username": username, "role": user["role"]}
