"""Dual-write database router.

Routes database operations to Cloudflare D1 (primary) and local PostgreSQL (failover).
On D1 failure, automatically falls back to Postgres and queues writes for replay.

Usage in API routes:
    from app.services.db_router import db_router
    rows = await db_router.query("SELECT * FROM users WHERE id = ?", [user_id])
    await db_router.execute("INSERT INTO users ...", [params])
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from app.services.d1_client import d1_client

logger = logging.getLogger(__name__)


class DualWriteRouter:
    """Routes reads/writes between D1 (primary) and PostgreSQL (failover).

    Write strategy:
    1. Write to D1 (primary).
    2. If D1 succeeds, replicate to Postgres (async, best-effort).
    3. If D1 fails, write to Postgres only and queue for replay.

    Read strategy:
    1. Read from D1 (primary).
    2. If D1 fails, read from Postgres (failover).
    """

    def __init__(self) -> None:
        self._d1_available: bool = True
        self._d1_check_at: datetime | None = None
        self._replay_queue: list[dict[str, Any]] = []
        self._circuit_open: bool = False
        self._circuit_opened_at: datetime | None = None
        self._failure_count: int = 0
        self._CIRCUIT_THRESHOLD = 3
        self._CIRCUIT_RESET_SECONDS = 60

    def _check_circuit(self) -> bool:
        """Check if the D1 circuit breaker should allow requests."""
        if not self._circuit_open:
            return True
        if self._circuit_opened_at:
            elapsed = (datetime.now(UTC) - self._circuit_opened_at).total_seconds()
            if elapsed > self._CIRCUIT_RESET_SECONDS:
                logger.info("D1 circuit breaker reset after %ds, retrying", int(elapsed))
                self._circuit_open = False
                self._failure_count = 0
                return True
        return False

    def _record_failure(self) -> None:
        """Record a D1 failure and potentially open the circuit breaker."""
        self._failure_count += 1
        if self._failure_count >= self._CIRCUIT_THRESHOLD:
            self._circuit_open = True
            self._circuit_opened_at = datetime.now(UTC)
            logger.warning(
                "D1 circuit breaker opened after %d failures", self._failure_count
            )

    def _record_success(self) -> None:
        """Record a D1 success and reset failure count."""
        self._failure_count = 0
        if self._circuit_open:
            self._circuit_open = False
            logger.info("D1 circuit breaker closed (recovered)")

    async def query(
        self,
        sql: str,
        params: list[Any] | None = None,
        table: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a read query. Tries D1 first, falls back to Postgres."""
        if self._check_circuit() and d1_client.enabled:
            try:
                rows = await d1_client.query_all(sql, params)
                self._record_success()
                return rows
            except Exception as exc:
                logger.warning("D1 query failed, falling back to Postgres: %s", exc)
                self._record_failure()

        # Fall back to Postgres
        return await self._postgres_query(sql, params, table)

    async def query_one(
        self,
        sql: str,
        params: list[Any] | None = None,
        table: str | None = None,
    ) -> dict[str, Any] | None:
        """Execute a read query and return one row."""
        rows = await self.query(sql, params, table)
        return rows[0] if rows else None

    async def execute(
        self,
        sql: str,
        params: list[Any] | None = None,
        table: str | None = None,
    ) -> Any:
        """Execute a write statement with dual-write.

        1. Write to D1 (primary).
        2. Replicate to Postgres (failover).
        3. If D1 fails, write to Postgres and queue for replay.
        """
        d1_success = False
        d1_result = None

        if self._check_circuit() and d1_client.enabled:
            try:
                d1_result = await d1_client.execute(sql, params)
                d1_success = True
                self._record_success()
            except Exception as exc:
                logger.warning("D1 write failed: %s, writing to Postgres only", exc)
                self._record_failure()

        # Always write to Postgres (it's the failover)
        pg_result = await self._postgres_execute(sql, params, table)

        # If D1 failed, queue for replay
        if not d1_success and d1_client.enabled:
            self._replay_queue.append({
                "sql": sql,
                "params": params,
                "table": table,
                "queued_at": datetime.now(UTC).isoformat(),
            })
            logger.info("Queued write for D1 replay (queue: %d)", len(self._replay_queue))

        return d1_result if d1_success else pg_result

    async def _postgres_query(
        self,
        sql: str,
        params: list[Any] | None = None,
        table: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a read query against PostgreSQL."""
        from sqlalchemy import text
        from app.core.config import settings
        from sqlalchemy.ext.asyncio import create_async_engine

        # Convert ? placeholders to :param_N for SQLAlchemy
        pg_sql = sql
        pg_params = {}
        if params:
            pg_sql = sql.replace("?", ":param_") + ""  # Will fix below
            # Actually, replace each ? with :param_N
            for i, p in enumerate(params):
                pg_sql = pg_sql.replace("?", f":param_{i}", 1)
                pg_params[f"param_{i}"] = self._deserialize_for_pg(p)

        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(pg_sql), pg_params if pg_params else None)
                columns = list(result.keys())
                rows = result.fetchall()
                return [dict(zip(columns, row, strict=False)) for row in rows]
        finally:
            await engine.dispose()

    async def _postgres_execute(
        self,
        sql: str,
        params: list[Any] | None = None,
        table: str | None = None,
    ) -> Any:
        """Execute a write against PostgreSQL."""
        from sqlalchemy import text
        from app.core.config import settings
        from sqlalchemy.ext.asyncio import create_async_engine

        pg_sql = sql
        pg_params = {}
        if params:
            for i, p in enumerate(params):
                pg_sql = pg_sql.replace("?", f":param_{i}", 1)
                pg_params[f"param_{i}"] = self._deserialize_for_pg(p)

        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(pg_sql), pg_params if pg_params else None)
                return result.rowcount
        finally:
            await engine.dispose()

    @staticmethod
    def _deserialize_for_pg(value: Any) -> Any:
        """Convert D1-serialized values back to PostgreSQL-compatible types."""
        if isinstance(value, str):
            # Try to detect JSON strings
            if value.startswith(("{", "[")):
                try:
                    return json.loads(value)
                except json.JSONDecodeError:
                    pass
        return value

    async def replay_queue(self) -> int:
        """Replay queued writes to D1 after recovery."""
        if not self._replay_queue or not d1_client.enabled:
            return 0

        if not self._check_circuit():
            return 0

        replayed = 0
        remaining = []
        for item in self._replay_queue:
            try:
                await d1_client.execute(item["sql"], item["params"])
                replayed += 1
                self._record_success()
            except Exception as exc:
                logger.warning("Replay failed, keeping in queue: %s", exc)
                self._record_failure()
                remaining.append(item)

        self._replay_queue = remaining
        if replayed:
            logger.info("Replayed %d writes to D1, %d remaining", replayed, len(remaining))
        return replayed

    async def health(self) -> dict[str, Any]:
        """Check the health of both databases and the router state."""
        d1_ok = await d1_client.health() if d1_client.enabled else False
        return {
            "d1_primary": d1_ok,
            "circuit_open": self._circuit_open,
            "failure_count": self._failure_count,
            "replay_queue_size": len(self._replay_queue),
            "mode": "dual" if d1_ok else "postgres_only",
        }


# Singleton instance
db_router = DualWriteRouter()
