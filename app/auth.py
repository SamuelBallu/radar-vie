"""Two kinds of callers.

- The owner, signed in with a password. Their browser holds a signed session cookie.
- The scheduled agents (morning search, cover letters). They send a bearer token.

The page is for one person, so there are no user accounts: one password, one token.
"""

import hashlib
import hmac
import secrets
import time
from typing import Literal

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request, status

from .config import get_settings

COOKIE = "radar_session"
MIN_SECRET_LENGTH = 32

Caller = Literal["owner", "agent"]

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored_hash: str) -> bool:
    if not stored_hash:
        return False
    try:
        return _hasher.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def sign_in_configured() -> bool:
    s = get_settings()
    return bool(s.app_password_hash) and len(s.session_secret) >= MIN_SECRET_LENGTH


def _signature(payload: str) -> str:
    # The password hash is part of the key: changing the password signs every device out.
    s = get_settings()
    key = (s.session_secret + s.app_password_hash).encode()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def new_session() -> str:
    expires = int(time.time()) + get_settings().session_days * 86400
    payload = f"{expires}.{secrets.token_hex(8)}"
    return f"{payload}.{_signature(payload)}"


def session_is_valid(token: str | None) -> bool:
    if not token or not sign_in_configured():
        return False
    parts = token.split(".")
    if len(parts) != 3 or not parts[0].isdigit():
        return False
    expires, nonce, signature = parts
    return hmac.compare_digest(signature, _signature(f"{expires}.{nonce}")) and int(expires) > time.time()


def is_agent(request: Request) -> bool:
    token = get_settings().api_token
    header = request.headers.get("authorization", "")
    if len(token) < MIN_SECRET_LENGTH or not header.startswith("Bearer "):
        return False
    return hmac.compare_digest(header.removeprefix("Bearer "), token)


def require_caller(request: Request) -> Caller:
    """Owner or agent: anyone allowed to read."""
    if is_agent(request):
        return "agent"
    if session_is_valid(request.cookies.get(COOKIE)):
        return "owner"
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in first.")


def require_agent(request: Request) -> None:
    if not is_agent(request):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Agent token required.")


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
