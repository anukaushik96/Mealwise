"""App session: a signed cookie holding our own user id.

Deliberately NOT the Swiggy token — that stays encrypted server-side. If this cookie
leaks, the attacker gets a session; they do not get a credential that can order on
Swiggy directly.
"""

from __future__ import annotations

import datetime as dt

import jwt
from fastapi import Request, Response

from .config import require

__all__ = ["COOKIE", "MAX_AGE_SECONDS", "set_session", "current_user_id", "clear_session"]

COOKIE = "mealwise_session"
MAX_AGE_SECONDS = 60 * 60 * 24 * 30


def set_session(response: Response, user_id: str, *, secure: bool) -> None:
    now = dt.datetime.now(dt.timezone.utc)
    token = jwt.encode(
        {"sub": user_id, "iat": now, "exp": now + dt.timedelta(seconds=MAX_AGE_SECONDS)},
        require("SESSION_SECRET"),
        algorithm="HS256",
    )
    response.set_cookie(
        COOKIE,
        token,
        max_age=MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def current_user_id(request: Request) -> str | None:
    """The signed-in user's id, or None. A bad cookie is treated as absent, not an error."""
    raw = request.cookies.get(COOKIE)
    if not raw:
        return None
    try:
        claims = jwt.decode(raw, require("SESSION_SECRET"), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    sub = claims.get("sub")
    return sub if isinstance(sub, str) else None


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")
