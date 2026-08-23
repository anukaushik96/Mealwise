"""Environment configuration, read once and validated loudly.

Every value here is required for the app to function, so a missing one raises at first
use with the name of the variable — not a `None` that surfaces three layers deeper.
"""

from __future__ import annotations

import os

__all__ = ["require", "app_origin", "redirect_uri", "DATABASE_URL", "SWIGGY_CLIENT_ID"]


def require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is not set")
    return value


def app_origin() -> str:
    """The public origin, used to build the OAuth redirect_uri.

    Falls back to Vercel's PRODUCTION host, never `VERCEL_URL`: the latter is the
    per-deployment preview host, which changes on every push and so can never be a
    stable registered redirect_uri.
    """
    explicit = os.environ.get("APP_URL")
    if explicit:
        return explicit.rstrip("/")
    production = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
    if production:
        return f"https://{production.rstrip('/')}"
    raise RuntimeError("APP_URL is not set (and no VERCEL_PROJECT_PRODUCTION_URL to fall back to)")


def redirect_uri() -> str:
    return f"{app_origin()}/auth/callback"


def DATABASE_URL() -> str:  # noqa: N802 - read as a constant at call sites
    return require("DATABASE_URL")


# Swiggy's DCR always returns this same id regardless of what you register, so it is a
# constant rather than something to persist per deployment.
SWIGGY_CLIENT_ID = "swiggy-mcp"
