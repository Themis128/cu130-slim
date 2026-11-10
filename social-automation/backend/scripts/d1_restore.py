"""Emergency D1 -> Postgres restore after volume loss.

Run inside social-api:  python /app/scripts/d1_restore.py [--write-owned]

Restores every SYNC_TABLES table from the D1 mirror via
SyncService.sync_table_to_postgres. publish_queue/post_targets are
Postgres-write-owned and skipped by that path — pass --write-owned to
also restore them with a direct upsert (use only when Postgres was
rebuilt empty, e.g. Docker volume loss).
"""

from __future__ import annotations

import asyncio
import sys

from app.services.db_sync import POSTGRES_WRITE_OWNED, SYNC_TABLES, SyncService


async def restore_write_owned(svc: SyncService) -> None:
    """Direct D1->PG upsert for publish_queue/post_targets (bypasses the
    write-owned guard — only valid on a freshly rebuilt empty database)."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings
    from app.services import db_sync as _m

    d1 = _m.d1_client
    if not d1.enabled:
        print("D1 client disabled — cannot restore write-owned tables")
        return

    engine = create_async_engine(settings.DATABASE_URL)
    pk_map = {t["table"]: t["pk"] for t in SYNC_TABLES}
    try:
        async with engine.begin() as conn:
            for table in sorted(POSTGRES_WRITE_OWNED):
                rows = await d1.query_all(f"SELECT * FROM {table}")
                if not rows:
                    print(f"{table}: 0 rows in D1")
                    continue
                columns = list(rows[0].keys())
                pk_cols = [p.strip() for pk in [pk_map[table]] for p in pk.split(",")]
                col_str = ", ".join(columns)
                placeholders = ", ".join(f":{c}" for c in columns)
                pk_conflict = ", ".join(pk_cols)
                update_str = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in pk_cols)
                sql = (
                    f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) ON CONFLICT ({pk_conflict}) DO UPDATE SET {update_str}"
                    if update_str
                    else f"INSERT INTO {table} ({col_str}) VALUES ({placeholders}) ON CONFLICT ({pk_conflict}) DO NOTHING"
                )
                converted = [{c: svc._d1_to_pg_value(c, r[c]) for c in columns} for r in rows]
                seen: dict[str, dict] = {}
                for rd in converted:
                    seen[str([rd.get(c) for c in pk_cols])] = rd
                res = await conn.execute(text(sql), list(seen.values()))
                print(f"{table}: restored {res.rowcount} rows")
    finally:
        await engine.dispose()


async def main() -> None:
    write_owned = "--write-owned" in sys.argv
    svc = SyncService()
    for t in SYNC_TABLES:
        name = t["table"]
        stats = await svc.sync_table_to_postgres(name, pk=t["pk"])
        print(f"{name}: {stats}")
    if write_owned:
        await restore_write_owned(svc)


if __name__ == "__main__":
    asyncio.run(main())
