# Cloudflare database failover & sync

This guide explains how the Cloudflare-first database architecture works, how to monitor it, and what to do during failover.

## Architecture

SocialAuto uses Cloudflare free-tier databases as the **primary** data layer, with local services as **failover**:

| Layer | Cloudflare (primary) | Local (failover) |
|-------|---------------------|------------------|
| SQL database | D1 (`social-automation`) | PostgreSQL (`social-postgres`) |
| Cache | KV (`social-cache`) | Redis |
| Vector search | Vectorize (`social-embeddings`) | ChromaDB |
| Object storage | R2 (`app-media-bucket`) | MinIO → local disk |

n8n and Metabase keep PostgreSQL as their primary database because they require native Postgres connections. Their D1 databases serve as periodic backup targets.

## How the dual-write router works

Every database write goes through the `db_router` (`app/services/db_router.py`):

1. **Write to D1** (primary).
2. **Replicate to Postgres** (failover replica).
3. If D1 fails, **write to Postgres only** and **queue the write for replay**.
4. When D1 recovers, **replay the queue** to sync missing writes.

### Circuit breaker

- After **3 consecutive D1 failures**, the circuit breaker opens.
- All traffic routes to Postgres for **60 seconds**.
- After 60 seconds, the router enters **half-open** state and retries D1.
- If the retry succeeds, the circuit closes and D1 becomes primary again.
- If the retry fails, the circuit reopens for another 60 seconds.

## Monitoring

### Check health

```bash
curl http://localhost:8083/api/v1/cf-db/health
```

Response:
```json
{
  "d1": true,
  "kv": true,
  "vectorize": true,
  "router": {
    "d1_primary": true,
    "circuit_open": false,
    "failure_count": 0,
    "replay_queue_size": 0,
    "mode": "dual"
  }
}
```

- `mode: "dual"` — both D1 and Postgres are active.
- `mode: "postgres_only"` — D1 is down, Postgres is serving all traffic.
- `circuit_open: true` — circuit breaker is open, routing to Postgres.
- `replay_queue_size > 0` — writes are queued for D1 replay.

### Check D1 table row counts

```bash
curl http://localhost:8083/api/v1/cf-db/tables
```

### Check which tables are synced

```bash
curl http://localhost:8083/api/v1/cf-db/sync-tables
```

## Operations

### Trigger a full bidirectional sync

Run this after restarting services, restoring from backup, or when you suspect data divergence:

```bash
curl -X POST http://localhost:8083/api/v1/cf-db/sync
```

This syncs all 15 application tables in both directions:
1. Postgres → D1 (push local changes to primary)
2. D1 → Postgres (pull any remote changes to failover)

### Replay queued writes to D1

After a D1 outage, the router automatically queues writes that went to Postgres only. To manually trigger replay:

```bash
curl -X POST http://localhost:8083/api/v1/cf-db/replay
```

Response:
```json
{
  "replayed": 5,
  "remaining": 0
}
```

## Failover scenarios

### D1 is down (Cloudflare outage)

1. The router detects D1 failures.
2. After 3 failures, the circuit breaker opens.
3. All reads and writes route to PostgreSQL.
4. Writes are queued for replay.
5. When D1 recovers, run `POST /api/v1/cf-db/replay` to sync queued writes.
6. Run `POST /api/v1/cf-db/sync` to ensure full consistency.

### PostgreSQL is down (local outage)

1. D1 continues as the sole database.
2. Reads and writes work normally through D1.
3. When Postgres recovers, run `POST /api/v1/cf-db/sync` to replicate D1 data back to Postgres.

### Both are down

1. The API will return errors for database operations.
2. Restore at least one database before continuing.
3. After restoration, run a full sync.

## Environment variables

Required in `.env` for the Cloudflare database system:

```bash
CLOUDFLARE_ACCOUNT_ID=your-account-id
CLOUDFLARE_API_TOKEN=your-token-with-d1-kv-vectorize-edit-permissions

D1_SOCIAL_AUTOMATION_ID=your-d1-database-uuid
D1_N8N_ID=your-n8n-d1-database-uuid
D1_METABASE_ID=your-metabase-d1-database-uuid

KV_CACHE_NAMESPACE=your-kv-namespace-id
KV_QUEUE_NAMESPACE=your-kv-queue-namespace-id

VECTORIZE_INDEX_NAME=social-embeddings
```

The `CLOUDFLARE_API_TOKEN` must have these permissions:
- Account → D1 → Edit
- Account → Workers KV Storage → Edit
- Account → Workers Vectorize → Edit
- Account → R2 → Edit (for storage)

## Free tier limits

| Service | Limit | What happens when exceeded |
|---------|-------|---------------------------|
| D1 | 5M reads/day, 100K writes/day, 5GB storage | Writes fail, router falls back to Postgres |
| KV | 100K reads/day, 1K writes/day, 1GB storage | Writes fail, Redis serves as failover |
| Vectorize | 30M queried dimensions/month, 10M stored | Queries fail, ChromaDB serves as failover |

## Files

| File | Purpose |
|------|---------|
| `app/services/d1_client.py` | Async D1 REST API client |
| `app/services/kv_client.py` | Async KV client |
| `app/services/vectorize_client.py` | Async Vectorize client |
| `app/services/db_sync.py` | Bidirectional sync service |
| `app/services/db_router.py` | Dual-write router with circuit breaker |
| `app/api/cf_db.py` | API endpoints for health, sync, replay |
| `scripts/pg_to_d1.py` | PostgreSQL → D1 schema converter |
