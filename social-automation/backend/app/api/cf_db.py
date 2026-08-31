"""Cloudflare database sync and failover API endpoints.

Endpoints:
- GET  /api/v1/cf-db/health     — Check D1, KV, Vectorize health
- POST /api/v1/cf-db/sync       — Trigger bidirectional sync
- GET  /api/v1/cf-db/status     — Router state, replay queue, last sync
- POST /api/v1/cf-db/replay     — Replay queued writes to D1
- GET  /api/v1/cf-db/tables     — List D1 tables and row counts
"""
from __future__ import annotations

from fastapi import APIRouter

from app.services.d1_client import d1_client
from app.services.db_router import db_router
from app.services.db_sync import SYNC_TABLES, sync_service
from app.services.kv_client import kv_client
from app.services.vectorize_client import vectorize_client

router = APIRouter()


@router.get("/health")
async def cf_db_health() -> dict:
    """Check health of all Cloudflare database services."""
    return {
        "d1": await d1_client.health(),
        "kv": await kv_client.health(),
        "vectorize": await vectorize_client.health(),
        "router": await db_router.health(),
    }


@router.get("/status")
async def cf_db_status() -> dict:
    """Get the current state of the dual-write router."""
    return await db_router.health()


@router.post("/sync")
async def cf_db_sync() -> dict:
    """Trigger a full bidirectional sync between D1 and PostgreSQL."""
    return await sync_service.full_bidirectional_sync()


@router.post("/replay")
async def cf_db_replay() -> dict:
    """Replay queued writes to D1 after a D1 outage."""
    replayed = await db_router.replay_queue()
    return {
        "replayed": replayed,
        "remaining": len(db_router._replay_queue),
    }


@router.get("/tables")
async def cf_db_tables() -> dict:
    """List D1 tables and their row counts."""
    if not d1_client.enabled:
        return {"enabled": False, "tables": []}

    tables = await d1_client.list_tables()
    result = {}
    for table in tables:
        if table.startswith("_cf"):
            continue
        try:
            count = await d1_client.count(table)
            result[table] = count
        except Exception:
            result[table] = -1

    return {"enabled": True, "tables": result}


@router.get("/sync-tables")
async def cf_db_sync_tables() -> dict:
    """List the tables configured for bidirectional sync."""
    return {
        "tables": [t["table"] for t in SYNC_TABLES],
        "count": len(SYNC_TABLES),
    }
