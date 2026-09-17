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
import hashlib
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.d1_client import d1_client

logger = logging.getLogger(__name__)

# Redis keys for shared sync state. _last_sync used to be a per-process
# dict — Celery prefork workers never shared it and restarts wiped it, so
# every sync fell back to full-table upserts (~145 rows every ~30s across
# 4 workers = ~100K D1 writes/day, the entire free-tier budget).
_RS_LAST_SYNC = "d1sync:last:{table}"
_RS_DEBOUNCE = "d1sync:debounce:{table}"
_RS_HASHES = "d1sync:hashes:{table}"

# Minimum seconds between D1 writes for a given table. Queue tasks run
# every ~30s; a 2-minute lag on the D1 mirror is fine for read replicas.
_SYNC_MIN_INTERVAL = int(os.environ.get("D1_SYNC_MIN_INTERVAL", "120"))


async def _sync_redis() -> Any:
    """Shared Redis client for sync watermarks/debounce."""
    import redis.asyncio as aioredis

    from app.core.config import get_settings

    return aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)


# Tables to sync to D1, with their primary key column(s).
#
# Only LOW-VOLUME tables are synced to D1 to stay within the free tier
# limit of 100,000 rows written per day. High-volume append-heavy tables
# (analytics_events, post_analytics_snapshots, ai_usage_logs) use
# PostgreSQL as their primary and are NOT synced to D1.
#
# D1 free tier: 100K writes/day. With ~30 low-volume rows synced
# incrementally (only changed rows), daily writes stay well under 1K.
SYNC_TABLES: list[dict[str, str]] = [
    {"table": "users", "pk": "id"},
    {"table": "teams", "pk": "id"},
    {"table": "team_members", "pk": "team_id,user_id"},
    {"table": "social_accounts", "pk": "id"},
    {"table": "posts", "pk": "id"},
    {"table": "post_targets", "pk": "post_id,social_account_id"},
    {"table": "media_assets", "pk": "id"},
    {"table": "media_collections", "pk": "id"},
    {"table": "publish_queue", "pk": "id"},
    {"table": "ai_providers", "pk": "id"},
    {"table": "prompt_templates", "pk": "id"},
    {"table": "generated_workflows", "pk": "id"},
]

# High-volume tables that stay Postgres-primary (NOT synced to D1).
# These are append-only analytics/audit tables that would blow past the
# D1 free tier (100K writes/day) if synced every 5 minutes.
POSTGRES_ONLY_TABLES = frozenset({
    "analytics_events",
    "post_analytics_snapshots",
    "ai_usage_logs",
    "follower_snapshots",
})

# Whitelist of allowed table names — used to prevent SQL injection in
# sync_table_to_d1 / sync_table_to_postgres where the table name is
# interpolated into raw SQL.
_ALLOWED_TABLES = frozenset(t["table"] for t in SYNC_TABLES)


class SyncService:
    """Bidirectional D1 ↔ PostgreSQL sync service."""

    # Columns that are boolean in Postgres but stored as 0/1 in D1/SQLite
    _BOOL_COLUMNS = frozenset({
        "success",
        "is_active",
        "is_default",
        "is_enabled",
        "is_superuser",
        "is_published",
        "is_approved",
        "is_favorite",
        "is_archived",
        "is_processed",
        "is_verified",
    })

    # Columns that are PostgreSQL arrays (varchar[]) stored as JSON strings in D1
    _ARRAY_COLUMNS = frozenset({
        "tags",
        "ai_tags",
        "platforms",
        "hashtags",
    })

    def __init__(self) -> None:
        self._sync_lock = asyncio.Lock()
        self._last_sync: dict[str, datetime] = {}
        self._d1_write_limit_hit: bool = False
        self._d1_write_limit_reset_at: datetime | None = None
        self._d1_pk_cache: dict[str, bool] = {}

    async def _d1_table_has_pk(self, table: str) -> bool:
        """Return True if the D1 table has a PRIMARY KEY (cached per process)."""
        if table in self._d1_pk_cache:
            return self._d1_pk_cache[table]
        try:
            cols = await d1_client.query_all(f"PRAGMA table_info({table})")
            has_pk = any(c.get("pk") for c in cols)
        except Exception:
            has_pk = False
        self._d1_pk_cache[table] = has_pk
        return has_pk

    async def _repair_d1_pk(self, table: str, pk_cols: list[str]) -> bool:
        """Rebuild a PK-less D1 table with the expected composite PK.

        Recreates the table as <table>_repaired with PRIMARY KEY over pk_cols,
        copies deduplicated rows (last write wins per PK), then swaps it in.
        Self-heals the duplicate-row damage caused by INSERT OR REPLACE on a
        PK-less table. Returns True when the repaired table is live.
        """
        try:
            info = await d1_client.query_all(f"PRAGMA table_info({table})")
            if not info:
                return False
            col_defs = ", ".join(
                f"{c['name']} {c['type'] or 'TEXT'}{' NOT NULL' if c.get('notnull') else ''}"
                for c in info
            )
            col_names = ", ".join(c["name"] for c in info)
            pk_str = ", ".join(pk_cols)
            new_table = f"{table}_repaired"

            await d1_client.execute(
                f"CREATE TABLE IF NOT EXISTS {new_table} "
                f"({col_defs}, PRIMARY KEY ({pk_str}))"
            )
            # DISTINCT + ORDER BY rowid keeps one copy per PK (dedupe)
            await d1_client.execute(
                f"INSERT OR REPLACE INTO {new_table} SELECT {col_names} "
                f"FROM {table} GROUP BY {pk_str}"
            )
            await d1_client.execute(f"DROP TABLE {table}")
            await d1_client.execute(f"ALTER TABLE {new_table} RENAME TO {table}")
            self._d1_pk_cache[table] = True
            logger.info("Repaired D1 table %s: added PK (%s) and deduplicated", table, pk_str)
            return True
        except Exception as exc:
            logger.error("D1 PK repair failed for %s: %s", table, exc)
            return False

    @staticmethod
    def _d1_to_pg_value(column: str, value: Any) -> Any:
        """Convert a D1 (SQLite) value to a PostgreSQL-compatible value.

        - Boolean columns: 0/1 int → Python bool
        - Timestamp strings: ISO 8601 → datetime
        - JSON strings: kept as-is (asyncpg JSONB encoder expects strings)
        - Array columns: JSON string → Python list
        """
        # Boolean conversion
        if column in SyncService._BOOL_COLUMNS and isinstance(value, int):
            return bool(value)

        # Array conversion: D1 stores as JSON string, Postgres expects a list
        if column in SyncService._ARRAY_COLUMNS and isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return []

        # Timestamp conversion (ISO 8601 string → datetime)
        if isinstance(value, str):
            import re
            if re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", value):
                try:
                    return datetime.fromisoformat(value.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    pass

        return value

    async def sync_table_to_d1(
        self,
        table: str,
        pk: str = "id",
        batch_size: int = 100,
        *,
        debounce: bool = True,
    ) -> dict[str, int]:
        """Sync changed rows from local PostgreSQL to D1.

        Reads from Postgres and upserts into D1.
        Uses INSERT OR REPLACE for SQLite/D1.

        Incremental: only syncs rows with updated_at > last_sync_at.
        Tables without updated_at use a Redis-cached row-hash diff so only
        rows whose content actually changed are written — D1 bills per
        affected row, so a no-op upsert costs the same as a real write.
        """
        if table not in _ALLOWED_TABLES:
            logger.error("Refusing to sync unrecognised table %r (not in SYNC_TABLES)", table)
            return {"synced": 0, "errors": 1, "skipped": 0}

        # Circuit breaker: skip if D1 daily write limit was hit and hasn't reset
        if self._d1_write_limit_hit:
            now = datetime.now(UTC)
            if self._d1_write_limit_reset_at and now < self._d1_write_limit_reset_at:
                logger.debug("D1 daily write limit active, skipping sync for %s", table)
                return {"synced": 0, "errors": 0, "skipped": 1}
            else:
                # Reset time passed — clear the flag and try again
                self._d1_write_limit_hit = False
                self._d1_write_limit_reset_at = None
                logger.info("D1 daily write limit reset window passed, retrying sync")

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.core.config import settings

        stats = {"synced": 0, "errors": 0, "skipped": 0}

        if not d1_client.enabled:
            logger.warning("D1 not configured, skipping sync for %s", table)
            stats["skipped"] = 1
            return stats

        # Shared sync state lives in Redis — Celery prefork processes and
        # restarts must not each keep their own watermark (that was what
        # turned every worker task into a full-table D1 upsert).
        r = None
        try:
            r = await _sync_redis()
            if debounce and not await r.set(
                _RS_DEBOUNCE.format(table=table), "1", nx=True, ex=_SYNC_MIN_INTERVAL
            ):
                logger.debug("D1 sync debounced for %s (interval %ds)", table, _SYNC_MIN_INTERVAL)
                stats["skipped"] = 1
                return stats
        except Exception:
            if r is not None:
                try:
                    await r.aclose()
                except Exception:
                    pass
                r = None

        engine = create_async_engine(settings.DATABASE_URL)
        try:
            async with engine.connect() as col_check:
                col_result = await col_check.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :tbl AND column_name = 'updated_at'"
                    ),
                    {"tbl": table},
                )
                has_updated_at = col_result.fetchone() is not None

            # Incremental watermark — Redis-shared with in-memory fallback
            last_sync: datetime | None = None
            if has_updated_at:
                if r is not None:
                    try:
                        ts = await r.get(_RS_LAST_SYNC.format(table=table))
                        last_sync = datetime.fromisoformat(ts) if ts else None
                    except Exception:
                        last_sync = None
                else:
                    last_sync = self._last_sync.get(table)

            select_sql = f"SELECT * FROM {table}"
            if last_sync:
                select_sql += f" WHERE updated_at > '{last_sync.isoformat()}'"
                logger.debug("Incremental sync for %s (since %s)", table, last_sync.isoformat())

            async with engine.connect() as conn:
                result = await conn.execute(text(select_sql))
                columns = list(result.keys())
                rows = result.fetchall()

            pk_cols = [p.strip() for p in pk.split(",")]

            def _pk_key(row_dict: dict[str, Any]) -> str:
                return json.dumps([str(row_dict.get(c)) for c in pk_cols])

            def _row_hash(row_dict: dict[str, Any]) -> str:
                return hashlib.sha1(
                    json.dumps(row_dict, sort_keys=True, default=str).encode()
                ).hexdigest()

            # Tables without updated_at (or first sync) can't select by
            # watermark — diff row content against the Redis hash map so we
            # only write rows that actually changed. Entries present in the
            # map but gone from Postgres get deleted in D1.
            hash_key = _RS_HASHES.format(table=table)
            use_hash_diff = not last_sync and r is not None
            new_hashes: dict[str, str] = {}
            old_hashes: dict[str, str] = {}
            if use_hash_diff:
                try:
                    old_hashes = await r.hgetall(hash_key)
                except Exception:
                    old_hashes = {}
                changed_rows = []
                for row in rows:
                    row_dict = dict(zip(columns, row, strict=False))
                    key = _pk_key(row_dict)
                    h = _row_hash(row_dict)
                    new_hashes[key] = h
                    if old_hashes.get(key) != h:
                        changed_rows.append(row)
                rows = changed_rows

            if not rows and not (use_hash_diff and set(old_hashes) - set(new_hashes)):
                logger.debug("No changed rows in %s to sync", table)
                await self._set_last_sync(table, r)
                return stats

            # INSERT OR REPLACE only deduplicates on a PRIMARY KEY/UNIQUE
            # violation. A D1 table created without a PK turns every
            # "upsert" into a plain INSERT — duplicating rows each cycle
            # (this is what ballooned post_targets to 150k+ rows).
            if not await self._d1_table_has_pk(table):
                logger.warning(
                    "D1 table %r has no PRIMARY KEY — attempting repair",
                    table,
                )
                if not await self._repair_d1_pk(table, pk_cols):
                    logger.error(
                        "D1 table %r still has no PRIMARY KEY — refusing to "
                        "upsert (would silently duplicate rows)",
                        table,
                    )
                    stats["errors"] += 1
                    return stats

            # Build INSERT OR REPLACE statement (SQLite/D1 syntax)
            col_str = ", ".join(columns)
            placeholders = ", ".join(["?"] * len(columns))
            sql = (
                f"INSERT OR REPLACE INTO {table} ({col_str}) "
                f"VALUES ({placeholders})"
            )
            delete_sql = (
                f"DELETE FROM {table} WHERE "
                + " AND ".join(f"{c} = ?" for c in pk_cols)
            )

            def _ser(row_dict: dict[str, Any]) -> list[Any]:
                values = []
                for col in columns:
                    val = row_dict[col]
                    if isinstance(val, dict | list):
                        val = json.dumps(val, default=str)
                    elif isinstance(val, bool):
                        val = 1 if val else 0
                    elif isinstance(val, datetime):
                        val = val.isoformat()
                    elif val is None:
                        val = None
                    else:
                        val = str(val) if not isinstance(val, int | float | str) else val
                    values.append(val)
                return values

            def _limit_hit() -> None:
                now = datetime.now(UTC)
                reset_at = now.replace(hour=0, minute=0, second=0, microsecond=0)
                if reset_at <= now:
                    reset_at = reset_at + timedelta(days=1)
                self._d1_write_limit_hit = True
                self._d1_write_limit_reset_at = reset_at
                logger.info("D1 write circuit breaker active until %s UTC", reset_at.isoformat())

            # Upsert changed rows only
            _consecutive_errors = 0
            synced_keys: list[str] = []
            for row in rows:
                row_dict = dict(zip(columns, row, strict=False))
                try:
                    await d1_client.execute(sql, _ser(row_dict))
                    stats["synced"] += 1
                    key = _pk_key(row_dict)
                    synced_keys.append(key)
                    # Record the hash for every row written, in every mode —
                    # keeps the map accurate so a restart diffs instead of
                    # re-upserting the whole table.
                    new_hashes[key] = _row_hash(row_dict)
                    _consecutive_errors = 0
                except Exception as exc:
                    exc_str = str(exc)
                    logger.error("D1 sync row failed for %s: %s", table, exc)
                    stats["errors"] += 1
                    _consecutive_errors += 1
                    if "exceeded" in exc_str.lower() and "daily" in exc_str.lower():
                        logger.warning(
                            "D1 daily write limit reached — skipping remaining tables for this sync cycle"
                        )
                        _limit_hit()
                        break
                    if _consecutive_errors >= 3:
                        logger.error(
                            "D1 sync for %s: aborting after %d consecutive errors",
                            table, _consecutive_errors,
                        )
                        break

            # Delete D1 rows whose PK vanished from Postgres
            if use_hash_diff and not self._d1_write_limit_hit:
                deleted_keys: list[str] = []
                for key in set(old_hashes) - set(new_hashes):
                    try:
                        pk_vals = json.loads(key)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    try:
                        await d1_client.execute(delete_sql, pk_vals)
                        deleted_keys.append(key)
                    except Exception as exc:
                        logger.error("D1 stale-row delete failed for %s: %s", table, exc)
                        if "exceeded" in str(exc).lower() and "daily" in str(exc).lower():
                            _limit_hit()
                            break
                if deleted_keys:
                    stats["deleted"] = len(deleted_keys)
            else:
                deleted_keys = []

            # Persist the hash map of what D1 now mirrors
            if r is not None and (synced_keys or deleted_keys):
                try:
                    if synced_keys:
                        await r.hset(hash_key, mapping={k: new_hashes[k] for k in synced_keys})
                    if deleted_keys:
                        await r.hdel(hash_key, *deleted_keys)
                except Exception:
                    pass

            await self._set_last_sync(table, r)

        finally:
            await engine.dispose()
            if r is not None:
                try:
                    await r.aclose()
                except Exception:
                    pass

        logger.info(
            "Synced %s: %d rows to D1 (%d deleted, %d errors)",
            table, stats["synced"], stats.get("deleted", 0), stats["errors"],
        )
        return stats

    async def _set_last_sync(self, table: str, r: Any = None) -> None:
        """Persist the sync watermark — Redis when available, else memory."""
        now = datetime.now(UTC)
        if r is not None:
            try:
                await r.set(_RS_LAST_SYNC.format(table=table), now.isoformat())
                return
            except Exception:
                pass
        self._last_sync[table] = now

    async def sync_table_to_postgres(
        self,
        table: str,
        pk: str = "id",
    ) -> dict[str, int]:
        """Sync all rows from D1 to local PostgreSQL.

        Reads from D1 and upserts into Postgres using INSERT ... ON CONFLICT.
        """
        if table not in _ALLOWED_TABLES:
            logger.error("Refusing to sync unrecognised table %r (not in SYNC_TABLES)", table)
            return {"synced": 0, "errors": 1, "skipped": 0}

        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from app.core.config import settings

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

            # Parse PK (may be composite, e.g. "team_id,user_id")
            pk_cols = [p.strip() for p in pk.split(",")]

            # Build Postgres upsert
            col_str = ", ".join(columns)
            placeholders = ", ".join(f":{c}" for c in columns)
            pk_conflict = ", ".join(pk_cols)
            update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in pk_cols)
            if update_str:
                sql = (
                    f"INSERT INTO {table} ({col_str}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT ({pk_conflict}) DO UPDATE SET {update_str}"
                )
            else:
                # All columns are PK columns — use DO NOTHING
                sql = (
                    f"INSERT INTO {table} ({col_str}) "
                    f"VALUES ({placeholders}) "
                    f"ON CONFLICT ({pk_conflict}) DO NOTHING"
                )

            converted = [
                {col: self._d1_to_pg_value(col, row[col]) for col in columns}
                for row in rows
            ]

            # D1 rows may contain PK duplicates (from the PK-less-table bug).
            # Dedupe by PK — last occurrence wins — before upserting.
            seen: dict[str, dict[str, Any]] = {}
            for row_data in converted:
                seen[json.dumps([str(row_data.get(c)) for c in pk_cols])] = row_data
            if len(seen) < len(converted):
                logger.info(
                    "Deduped %s: %d D1 rows -> %d by PK",
                    table, len(converted), len(seen),
                )
            converted = list(seen.values())

            async def _row_upsert(row_data: dict[str, Any]) -> bool:
                try:
                    async with engine.begin() as conn:
                        await conn.execute(text(sql), row_data)
                    return True
                except Exception as exc:
                    logger.warning(
                        "Skipping unsyncable %s row %s: %s",
                        table,
                        {c: row_data.get(c) for c in pk_cols},
                        exc,
                    )
                    return False

            # Batch upserts (500/transaction) for speed; on batch failure
            # fall back to per-row transactions so one bad row (e.g. an FK
            # violation from a D1 orphan) can't poison the whole batch.
            batch_size = 500
            for i in range(0, len(converted), batch_size):
                batch = converted[i : i + batch_size]
                try:
                    async with engine.begin() as conn:
                        for row_data in batch:
                            await conn.execute(text(sql), row_data)
                    stats["synced"] += len(batch)
                except Exception:
                    for row_data in batch:
                        if await _row_upsert(row_data):
                            stats["synced"] += 1
                        else:
                            stats["errors"] += 1

        finally:
            await engine.dispose()

        self._last_sync[table] = datetime.now(UTC)
        logger.info("Synced %s: %d rows to Postgres", table, stats["synced"])
        return stats

    async def sync_all_to_d1(self) -> dict[str, dict[str, int]]:
        """Sync all tables from Postgres to D1 (operator-triggered, no debounce)."""
        async with self._sync_lock:
            results = {}
            for t in SYNC_TABLES:
                results[t["table"]] = await self.sync_table_to_d1(
                    t["table"], t["pk"], debounce=False
                )
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

    async def sync_tables_to_d1(self, tables: list[str]) -> dict[str, dict[str, int]]:
        """Sync specific tables from Postgres → D1.

        Called by Celery worker tasks after writing to Postgres, so that
        D1 (the primary) stays in sync with worker-originated changes.

        Args:
            tables: List of table names to sync (must be in SYNC_TABLES).

        Returns:
            Per-table sync stats: {table: {synced, errors, skipped}}.
        """
        # Build a lookup for PKs
        pk_map = {t["table"]: t["pk"] for t in SYNC_TABLES}
        results: dict[str, dict[str, int]] = {}
        for table in tables:
            pk = pk_map.get(table, "id")
            results[table] = await self.sync_table_to_d1(table, pk)
        return results


# Singleton instance
sync_service = SyncService()


async def sync_after_worker_task(tables: list[str]) -> dict[str, dict[str, int]]:
    """Fire-and-forget Postgres → D1 sync for Celery worker tasks.

    Called after a worker task writes to Postgres. Pushes the affected
    tables to D1 so the primary stays consistent. Errors are logged but
    never raised — the worker's Postgres write already succeeded.

    Args:
        tables: Tables touched by the worker task (e.g. ["posts", "publish_queue"]).

    Returns:
        Per-table sync stats.
    """
    if not d1_client.enabled:
        logger.debug("sync_after_worker_task skipped (D1 not enabled)")
        return {}
    try:
        return await sync_service.sync_tables_to_d1(tables)
    except Exception as exc:
        logger.warning("sync_after_worker_task failed for %s: %s", tables, exc)
        return {}
