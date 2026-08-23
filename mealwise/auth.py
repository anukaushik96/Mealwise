"""Swiggy OAuth 2.1 + PKCE for a HOSTED app.

Verified against the live authorization server:

  - `/auth/register` (DCR) accepts an https redirect_uri, so the loopback flow the CLI
    uses is not required. It always returns the SAME `client_id` ("swiggy-mcp"), so
    registration is effectively an echo — treat the id as a constant.
  - `grant_types_supported` includes `refresh_token`, so one may be issued; capture it,
    because access tokens expire in ~5 days (432000s).
  - `code_challenge_methods_supported` is ["S256"] only.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import secrets
import urllib.parse
from dataclasses import dataclass

import httpx

from .config import SWIGGY_CLIENT_ID, redirect_uri

__all__ = [
    "SWIGGY_AUTH_BASE",
    "SWIGGY_SCOPE",
    "TokenSet",
    "create_pkce_pair",
    "create_state",
    "authorize_url",
    "exchange_code",
    "refresh_access_token",
    "swiggy_subject",
]

SWIGGY_AUTH_BASE = "https://mcp.swiggy.com"
SWIGGY_SCOPE = "mcp:tools mcp:resources mcp:prompts"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def create_pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def create_state() -> str:
    return _b64url(secrets.token_bytes(16))


def authorize_url(*, challenge: str, state: str) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": SWIGGY_CLIENT_ID,
            "redirect_uri": redirect_uri(),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": SWIGGY_SCOPE,
        }
    )
    return f"{SWIGGY_AUTH_BASE}/auth/authorize?{query}"


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str | None
    expires_at: dt.datetime


def _to_token_set(body: dict) -> TokenSet:
    access = body.get("access_token")
    if not isinstance(access, str):
        raise RuntimeError(f"token response had no access_token: {json.dumps(body)[:200]}")
    expires_in = body.get("expires_in")
    seconds = expires_in if isinstance(expires_in, int) else 432000
    refresh = body.get("refresh_token")
    return TokenSet(
        access_token=access,
        refresh_token=refresh if isinstance(refresh, str) else None,
        expires_at=dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds),
    )


async def _post(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(f"{SWIGGY_AUTH_BASE}{path}", json=payload)
    if response.status_code >= 400:
        raise RuntimeError(f"{path} failed ({response.status_code}): {response.text[:300]}")
    return response.json()


async def exchange_code(*, code: str, verifier: str) -> TokenSet:
    return _to_token_set(
        await _post(
            "/auth/token",
            {
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": verifier,
                "redirect_uri": redirect_uri(),
                "client_id": SWIGGY_CLIENT_ID,
            },
        )
    )


async def refresh_access_token(refresh_token: str) -> TokenSet:
    return _to_token_set(
        await _post(
            "/auth/token",
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": SWIGGY_CLIENT_ID,
            },
        )
    )


def swiggy_subject(access_token: str) -> str:
    """The stable Swiggy user id, from the token's `sub` claim.

    The JWT is only DECODED, never verified — we are not the audience and hold no key.
    That is safe because the token came straight from a TLS token-endpoint response, not
    from the user.
    """
    parts = access_token.split(".")
    if len(parts) < 2:
        raise ValueError("access token is not a JWT")
    padded = parts[1] + "=" * (-len(parts[1]) % 4)
    claims = json.loads(base64.urlsafe_b64decode(padded))
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise ValueError("access token has no `sub` claim to identify the user")
    return sub
