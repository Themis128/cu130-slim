"""Bidirectional sync between Cloudflare D1 (primary) and local PostgreSQL (failover).

Strategy:
- D1 is the primary database. All writes go to D1 first, then replicate to Postgres.
- If D1 is unavailable, writes fall back to Postgres and a pending sync queue
  replays to D1 when it recovers.
- Reads prefer D1, fall back to Postgres on error.
- A periodic background task syncs any divergent records in both directions.

Tables synced: users, teams, team_members, social_accounts, posts, post_targets,
media_assets, media_collections, publish_queue, ai_providers, ai_usage_logs,
analytics_events, prompt_templates, generated_workflows, post_analytics_snapshots.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

from app.services.d1_client import d1_client

logger = logging.getLogger(__name__)

# Tables to sync, with their primary key column
SYNC_TABLES: list[dict[str, str]] = [
    {"table": "users", "pk": "id"},
    {"table": "teams", "pk": "id"},
    {"table": "team_members", "pk": "id"},
    {"table": "social_accounts", "pk": "id"},
    {"table": "posts", "pk": "id"},
    {"table": "post_targets", "pk": "id"},
    {"table": "media_assets", "pk": "id"},
    {"table": "media_collections", "pk": "id"},
    {"table": "publish_queue", "pk": "id"},
    {"table": "ai_providers", "pk": "id"},
    {"table": "ai_usage_logs", "pk": "id"},
    {"table": "analytics_events", "pk": "id"},
    {"table": "prompt_templates", "pk": "id"},
    {"table": "generated_workflows", "pk": "id"},
    {"table": "post_analytics_snapshots", "pk": "id"},
]


class SyncService:
    """Bidirectional D1 ↔ PostgreSQL sync service."""

    def __init__(self) -> None:
        self._sync_lock = asyncio.Lock()
        self._last_sync: dict[str, datetime] = {}

    async def sync_table_to_d1(
        self,
        table: str,
        pk: str = "id",
        batch_size: int = 100,
    ) -> dict[str, int]:
        """Sync all rows from local PostgreSQL to D1.

        Reads from Postgres and upserts into D1.
        Uses INSERT OR REPLACE for SQLite/D1.
        """
        from sqlalchemy import text
        from app.core.config import settings
        from sqlalchemy.ext.asyncio import create_async_engine

        stats = {"synced": 0, "errors": 0, "skipped": 0}

        if not d1_client.enabled:
            logger.warning("D1 not configured, skipping sync for %s", table)
            stats["skipped"] = 1
            return stats

        engine = create_async_engine(settings.DATABASE_URL)
        try:
            # Get all rows from Postgres
            async with engine.connect() as conn:
                result = await conn.execute(text(f"SELECT * FROM {table}"))
                columns = list(result.keys())
                rows = result.fetchall()

            if not rows:
                logger.debug("No rows in %s to sync", table)
                return stats

            # Build INSERT OR REPLACE statement (SQLite/D1 syntax)
            col_str = ", ".join(columns)
            placeholders = ", ".join(["?"] * len(columns))
            # Use INSERT OR REPLACE which works without explicit PRIMARY KEY constraints
            sql = (
                f"INSERT OR REPLACE INTO {table} ({col_str}) "
                f"VALUES ({placeholders})"
            )

            # Batch upsert
            for row in rows:
                row_dict = dict(zip(columns, row, strict=False))
                # Serialize complex types for D1
                values = []
                for col in columns:
                    val = row_dict[col]
                    if isinstance(val, (dict, list)):
                        val = json.dumps(val)
                    elif isinstance(val, bool):
                        val = 1 if val else 0
                    elif isinstance(val, datetime):
                        val = val.isoformat()
                    elif val is None:
                        val = None
                    else:
                        val = str(val) if not isinstance(val, (int, float, str)) else val
                    values.append(val)

                try:
                    await d1_client.execute(sql, values)
                    stats["synced"] += 1
                except Exception as exc:
                    logger.error("D1 sync row failed for %s: %s", table, exc)
                    stats["errors"] += 1

        finally:
            await engine.dispose()

        self._last_sync[table] = datetime.now(UTC)
        logger.info("Synced %s: %d rows to D1", table, stats["synced"])
        return stats

    async def sync_table_to_postgres(
        self,
        table: str,
        pk: str = "id",
    ) -> dict[str, int]:
        """Sync all rows from D1 to local PostgreSQL.

        Reads from D1 and upserts into Postgres using INSERT ... ON CONFLICT.
        """
        from sqlalchemy import text
        from app.core.config import settings
        from sqlalchemy.ext.asyncio import create_async_engine

        stats = {"synced": 0, "errors": 0, "skipped": 0}

        if not d1_client.enabled:
            stats["skipped"] = 1
            return stats

        engine = create_async_engine(settings.DATABASE_URL)
        try:
            # Get all rows from D1
            rows = await d1_client.query_all(f"SELECT * FROM {table}")
            if not rows:
                return stats

            # Get column info from first row
            columns = list(rows[0].keys())

            # Build Postgres upsert
            col_str = ", ".join(columns)
            placeholders = ", ".join(f":{c}" for c in columns)
            update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c != pk)
            sql = (
                f"INSERT INTO {table} ({col_str}) "
                f"VALUES ({placeholders}) "
                f"ON CONFLICT ({pk}) DO UPDATE SET {update_str}"
            )

            async with engine.begin() as conn:
                for row in rows:
                    # Convert D1 values back to Postgres types
                    row_data = {}
                    for col in columns:
                        val = row[col]
                        row_data[col] = val
                    try:
                        await conn.execute(text(sql), row_data)
                        stats["synced"] += 1
                    except Exception as exc:
                        logger.error("Postgres sync row failed for %s: %s", table, exc)
                        stats["errors"] += 1

        finally:
            await engine.dispose()

        self._last_sync[table] = datetime.now(UTC)
        logger.info("Synced %s: %d rows to Postgres", table, stats["synced"])
        return stats

    async def sync_all_to_d1(self) -> dict[str, dict[str, int]]:
        """Sync all tables from Postgres to D1."""
        async with self._sync_lock:
            results = {}
            for t in SYNC_TABLES:
                results[t["table"]] = await self.sync_table_to_d1(t["table"], t["pk"])
            return results

    async def sync_all_to_postgres(self) -> dict[str, dict[str, int]]:
        """Sync all tables from D1 to Postgres."""
        async with self._sync_lock:
            results = {}
            for t in SYNC_TABLES:
                results[t["table"]] = await self.sync_table_to_postgres(t["table"], t["pk"])
            return results

    async def full_bidirectional_sync(self) -> dict[str, Any]:
        """Run a full bidirectional sync.

        1. Sync Postgres → D1 (push local changes to primary)
        2. Sync D1 → Postgres (pull any remote changes to failover)
        """
        start = datetime.now(UTC)
        logger.info("Starting full bidirectional sync...")

        to_d1 = await self.sync_all_to_d1()
        to_pg = await self.sync_all_to_postgres()

        elapsed = (datetime.now(UTC) - start).total_seconds()
        total_synced = sum(s.get("synced", 0) for s in to_d1.values()) + sum(
            s.get("synced", 0) for s in to_pg.values()
        )

        result = {
            "started_at": start.isoformat(),
            "elapsed_seconds": elapsed,
            "to_d1": to_d1,
            "to_postgres": to_pg,
            "total_rows_synced": total_synced,
        }
        logger.info("Bidirectional sync complete: %d rows in %.1fs", total_synced, elapsed)
        return result

    async def health(self) -> dict[str, bool]:
        """Check sync service health."""
        return {
            "d1": await d1_client.health(),
            "last_sync": bool(self._last_sync),
        }


# Singleton instance
sync_service = SyncService()
