"""Fixed-window rate limiting for expensive endpoints.

Inputs: a bucket name and the caller identity (authenticated user id, else client IP).
Outputs: nothing on success; ``HTTPException(429)`` with ``Retry-After`` when the window is full.
Side effects: counter rows in ``rag_rate_limits`` (PostgreSQL) or a process-local dict.
"""

import time
from functools import lru_cache
from typing import Any

from fastapi import Depends, HTTPException, Request
from sqlalchemy import Column, Integer, String, Table, delete, insert, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import get_settings
from app.persistence.metadata import metadata

rate_limits_table = Table(
    "rag_rate_limits",
    metadata,
    Column("key", String(160), primary_key=True),
    Column("count", Integer, nullable=False),
    Column("expires", Integer, nullable=False),
)


class RateLimiter:
    """Shared fixed-window counter, backed by PostgreSQL or an in-process dict."""

    def __init__(self, engine: AsyncEngine | None) -> None:
        self._engine = engine
        self._memory: dict[str, tuple[int, int]] = {}

    async def check(self, key: str, *, limit: int, window_seconds: int = 60, cost: int = 1) -> None:
        now = int(time.time())
        window = now // window_seconds
        reset_at = (window + 1) * window_seconds
        bucket_key = f"{key}:{window}"
        count = (
            self._check_memory(bucket_key, reset_at, now, cost)
            if self._engine is None
            else await self._check_engine(bucket_key, reset_at, now, cost)
        )
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please slow down.",
                headers={"Retry-After": str(max(reset_at - now, 1))},
            )

    def _check_memory(self, bucket_key: str, reset_at: int, now: int, cost: int) -> int:
        current, _ = self._memory.get(bucket_key, (0, reset_at))
        current += cost
        self._memory[bucket_key] = (current, reset_at)
        self._memory = {k: v for k, v in self._memory.items() if v[1] > now}
        return current

    async def _check_engine(self, bucket_key: str, reset_at: int, now: int, cost: int) -> int:
        async with self._engine.begin() as conn:  # type: ignore[union-attr]
            try:
                async with conn.begin_nested():
                    await conn.execute(
                        insert(rate_limits_table).values(key=bucket_key, count=0, expires=reset_at)
                    )
            except IntegrityError:
                pass
            count = (
                await conn.execute(
                    update(rate_limits_table)
                    .where(rate_limits_table.c.key == bucket_key)
                    .values(count=rate_limits_table.c.count + cost)
                    .returning(rate_limits_table.c.count)
                )
            ).scalar_one()
            await conn.execute(delete(rate_limits_table).where(rate_limits_table.c.expires < now))
        return int(count)


@lru_cache
def _limiter(engine: AsyncEngine | None) -> RateLimiter:
    return RateLimiter(engine)


def _identity(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is not None:
        return f"user:{user['id']}"
    return f"ip:{request.client.host if request.client else 'anon'}"


def rate_limit(bucket: str, per_minute_setting: str) -> Any:
    """Dependency: cap ``bucket`` requests per caller per minute from a Settings field."""

    async def dependency(request: Request) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        limit = int(getattr(settings, per_minute_setting))
        if settings.metadata_store_backend == "postgresql":
            from app.api.dependencies import get_metadata_engine

            engine: AsyncEngine | None = get_metadata_engine()
        else:
            engine = None
        await _limiter(engine).check(f"{bucket}:{_identity(request)}", limit=limit)

    return Depends(dependency)
