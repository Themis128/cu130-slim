"""Dual-write database router.

Routes database operations to Cloudflare D1 (primary) and local PostgreSQL (failover).
On D1 failure, automatically falls back to Postgres and queues writes for replay.

Usage in API routes:
    from app.services.db_router import db_router
    rows = await db_router.query("SELECT * FROM users WHERE id = ?", [user_id])
    await db_router.execute("INSERT INTO users ...", [params])
"""
from __future__ import annotations

import logging
import re
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

        # If D1 failed or was unavailable, queue for replay
        # Queue when D1 is enabled but failed, OR when circuit breaker is open
        should_queue = (not d1_success) and (
            d1_client.enabled or self._circuit_open
        )
        if should_queue:
            self._replay_queue.append({
                "sql": sql,
                "params": params,
                "table": table,
                "queued_at": datetime.now(UTC).isoformat(),
            })
            logger.info("Queued write for D1 replay (queue: %d)", len(self._replay_queue))

        return d1_result if d1_success else pg_result

    @staticmethod
    def _sqlite_to_pg_sql(sql: str) -> str:
        """Convert SQLite/D1 SQL syntax to PostgreSQL syntax."""
        # INSERT OR REPLACE → INSERT ... ON CONFLICT DO UPDATE
        # This is a simplified converter for common patterns
        pg_sql = sql

        # Convert INSERT OR REPLACE INTO table (cols) VALUES (?)
        # to INSERT INTO table (cols) VALUES (?) ON CONFLICT (id) DO UPDATE SET ...
        import re
        match = re.match(
            r"INSERT OR REPLACE INTO (\w+) \(([^)]+)\) VALUES \(([^)]+)\)",
            pg_sql,
        )
        if match:
            table = match.group(1)
            cols = [c.strip() for c in match.group(2).split(",")]
            placeholders = match.group(3)
            # Assume "id" is the conflict target
            conflict_col = "id" if "id" in cols else cols[0]
            update_cols = [c for c in cols if c != conflict_col]
            update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
            pg_sql = (
                f"INSERT INTO {table} ({', '.join(cols)}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT ({conflict_col}) DO UPDATE SET {update_str}"
            )

        return pg_sql

    async def _postgres_query(
        self,
        sql: str,
        params: list[Any] | None = None,
        table: str | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a read query against PostgreSQL."""
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.core.config import settings

        # Convert SQLite syntax to Postgres
        pg_sql = self._sqlite_to_pg_sql(sql)

        # Convert ? placeholders to :param_N for SQLAlchemy
        pg_params = {}
        if params:
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
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.core.config import settings

        # Convert SQLite syntax to Postgres
        pg_sql = self._sqlite_to_pg_sql(sql)

        # Convert boolean params (0/1 → False/True) for Postgres
        bool_params = self._convert_bool_params(sql, params)

        pg_params = {}
        if bool_params:
            for i, p in enumerate(bool_params):
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
        """Convert D1-serialized values back to PostgreSQL-compatible types.

        asyncpg expects:
        - JSONB values as JSON strings (not parsed dicts)
        - Timestamps as datetime objects (not ISO strings)
        - Booleans as Python bool (not int)
        """
        if isinstance(value, str):
            # Try to parse ISO datetime strings for Postgres timestamp columns
            # Pattern: YYYY-MM-DDTHH:MM:SS or with timezone
            if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value):
                try:
                    # Handle 'Z' suffix
                    v = value.replace("Z", "+00:00")
                    return datetime.fromisoformat(v)
                except (ValueError, TypeError):
                    pass
            return value
        return value

    @staticmethod
    def _convert_bool_params(sql: str, params: list[Any] | None) -> list[Any] | None:
        """Convert 0/1 to False/True for boolean columns by inspecting the SQL.

        Looks for columns named with 'is_' prefix or 'success' and converts
        their corresponding parameter values from int to bool.
        """
        if not params:
            return params
        # Find boolean column patterns in the SQL
        import re
        bool_patterns = [
            r"\bis_\w+\b",
            r"\bsuccess\b",
            r"\bis_active\b",
            r"\bis_default\b",
            r"\bis_enabled\b",
            r"\bis_superuser\b",
        ]
        # Extract column names from INSERT statement
        col_match = re.match(r"INSERT(?:\s+OR\s+REPLACE)?\s+INTO\s+\w+\s*\(([^)]+)\)", sql)
        if not col_match:
            return params
        col_names = [c.strip() for c in col_match.group(1).split(",")]
        result = list(params)
        for i, col in enumerate(col_names):
            if i < len(result):
                col_lower = col.lower()
                if any(re.search(p, col_lower) for p in bool_patterns):
                    if isinstance(result[i], int):
                        result[i] = bool(result[i])
        return result

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
