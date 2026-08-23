"""Postgres access via asyncpg.

Two things matter for serverless:

  - `statement_cache_size=0` is REQUIRED. Neon's pooled endpoint is pgbouncer in
    transaction mode, which does not keep a session for prepared statements; leaving the
    cache on produces intermittent "prepared statement does not exist" errors under load.
  - A single connection per invocation, not a pool. A Vercel function may be frozen
    between requests, and a pool held across that boundary leaks server-side sessions.
"""

from __future__ import annotations

import contextlib
from typing import Any, AsyncIterator

import asyncpg

from .config import DATABASE_URL

__all__ = ["connection", "fetch", "fetchrow", "execute"]


@contextlib.asynccontextmanager
async def connection() -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(DATABASE_URL(), statement_cache_size=0)
    try:
        yield conn
    finally:
        await conn.close()


async def fetch(sql: str, *args: Any) -> list[asyncpg.Record]:
    async with connection() as conn:
        return await conn.fetch(sql, *args)


async def fetchrow(sql: str, *args: Any) -> asyncpg.Record | None:
    async with connection() as conn:
        return await conn.fetchrow(sql, *args)


async def execute(sql: str, *args: Any) -> str:
    async with connection() as conn:
        return await conn.execute(sql, *args)
