"""Unit tests for app/services/db_sync.py — D1 ↔ Postgres bidirectional sync.

Covers the free-tier protections: debounce, hash-diff incremental sync,
D1 write-limit circuit breaker, PK-less table repair refusal, the
Postgres-write-owned skip list, value conversion, and both sync loops.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.db_sync as S


def _svc() -> S.SyncService:
    return S.SyncService()


# ── value conversion ──────────────────────────────────────────────────


def test_d1_to_pg_value():
    conv = S.SyncService._d1_to_pg_value
    assert conv("is_active", 1) is True
    assert conv("is_active", 0) is False
    assert conv("is_active", True) is True  # bool isn't re-converted
    assert conv("tags", '["a","b"]') == ["a", "b"]
    assert conv("tags", "not json") == []
    assert conv("tags", None) is None
    dt = conv("created_at", "2026-10-01T12:30:00Z")
    assert isinstance(dt, datetime) and dt.year == 2026
    assert conv("created_at", "2026-13-99T99:99:99") == "2026-13-99T99:99:99"
    assert conv("name", "plain") == "plain"
    assert conv("count", 42) == 42


# ── D1 PK checks / repair ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_d1_table_has_pk_cache(monkeypatch):
    svc = _svc()
    q = AsyncMock(return_value=[{"pk": 1, "name": "id"}])
    monkeypatch.setattr(S.d1_client, "query_all", q)
    assert await svc._d1_table_has_pk("users") is True
    # cached — second call doesn't hit d1
    assert await svc._d1_table_has_pk("users") is True
    assert q.await_count == 1
    # pragma failure → False
    monkeypatch.setattr(S.d1_client, "query_all", AsyncMock(side_effect=RuntimeError("x")))
    assert await svc._d1_table_has_pk("other") is False


@pytest.mark.asyncio
async def test_repair_d1_pk(monkeypatch):
    svc = _svc()
    monkeypatch.setattr(
        S.d1_client, "query_all", AsyncMock(return_value=[{"name": "id", "type": "TEXT", "notnull": 1}, {"name": "v", "type": "TEXT", "notnull": 0}])
    )
    ex = AsyncMock()
    monkeypatch.setattr(S.d1_client, "execute", ex)
    assert await svc._repair_d1_pk("post_targets", ["post_id", "acct"]) is True
    sqls = [c.args[0] for c in ex.await_args_list]
    assert "CREATE TABLE IF NOT EXISTS post_targets_repaired" in sqls[0]
    assert "PRIMARY KEY (post_id, acct)" in sqls[0]
    assert sqls[2] == "DROP TABLE post_targets"
    assert "RENAME TO post_targets" in sqls[3]
    assert svc._d1_pk_cache["post_targets"] is True

    # empty pragma → False; execute blowup → False
    monkeypatch.setattr(S.d1_client, "query_all", AsyncMock(return_value=[]))
    assert await svc._repair_d1_pk("t", ["id"]) is False
    monkeypatch.setattr(S.d1_client, "query_all", AsyncMock(return_value=[{"name": "id", "type": "TEXT"}]))
    monkeypatch.setattr(S.d1_client, "execute", AsyncMock(side_effect=RuntimeError("ddl fail")))
    assert await svc._repair_d1_pk("t2", ["id"]) is False


# ── sync_table_to_d1 guards ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_to_d1_guards(monkeypatch):
    svc = _svc()
    # unallowed table
    out = await svc.sync_table_to_d1("DROP_ME")
    assert out["errors"] == 1 and out["synced"] == 0

    # D1 disabled
    _patch_d1(monkeypatch, enabled=False)
    out = await svc.sync_table_to_d1("users")
    assert out["skipped"] == 1

    # write-limit circuit breaker active
    svc._d1_write_limit_hit = True
    svc._d1_write_limit_reset_at = datetime.now(UTC) + timedelta(hours=1)
    monkeypatch.setattr(S.d1_client, "enabled", True)
    out = await svc.sync_table_to_d1("users")
    assert out["skipped"] == 1
    # reset window passed → flag cleared
    svc._d1_write_limit_reset_at = datetime.now(UTC) - timedelta(hours=1)
    monkeypatch.setattr(S, "_sync_redis", AsyncMock(side_effect=RuntimeError("no redis")))
    monkeypatch.setattr(S.SyncService, "_d1_table_has_pk", AsyncMock(return_value=True))
    _patch_engine(monkeypatch, rows=[])
    out = await svc.sync_table_to_d1("users")
    assert svc._d1_write_limit_hit is False


# ── fake engine / redis ───────────────────────────────────────────────


class _Result:
    def __init__(self, fetchone=None, keys=(), rows=()):
        self._one, self._keys, self._rows = fetchone, keys, rows

    def fetchone(self):
        return self._one

    def keys(self):
        return list(self._keys)

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self, handler):
        self.handler = handler

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, sql, params=None):
        return self.handler(str(sql), params)


class _Engine:
    def __init__(self, handler):
        self.handler = handler
        self.disposed = 0

    def connect(self):
        return _Conn(self.handler)

    def begin(self):
        return _Conn(lambda s, p: self.handler(s, p))

    async def dispose(self):
        self.disposed += 1


def _patch_engine(monkeypatch, *, has_updated_at=True, rows=None, keys=None, on_exec=None):
    """Patch create_async_engine with a fake conn handler.

    The handler receives (sql_str, params) and must return _Result for
    SELECTs; for DML returns _Result() (ignored).
    """
    keys = keys or ["id", "updated_at", "name"]
    rows = rows if rows is not None else []

    def handler(sql, params):
        if on_exec:
            on_exec(sql, params)
        if "information_schema.columns" in sql:
            return _Result(fetchone=("updated_at",) if has_updated_at else None)
        if sql.strip().lower().startswith("select"):
            return _Result(keys=keys, rows=rows)
        return _Result()

    import sqlalchemy.ext.asyncio as sa_async

    monkeypatch.setattr(sa_async, "create_async_engine", lambda url: _Engine(handler))


class _Redis:
    def __init__(self):
        self.kv = {}
        self.hashes = {}
        self.set_calls = []
        self.closed = 0

    async def set(self, k, v, nx=False, ex=None):
        self.set_calls.append((k, v, nx))
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def get(self, k):
        return self.kv.get(k)

    async def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    async def hset(self, k, mapping=None):
        self.hashes.setdefault(k, {}).update(mapping or {})

    async def hdel(self, k, *fields):
        for f in fields:
            self.hashes.get(k, {}).pop(f, None)

    async def aclose(self):
        self.closed += 1


def _patch_d1(monkeypatch, enabled=True):
    d1 = SimpleNamespace(
        enabled=enabled,
        execute=AsyncMock(),
        query_all=AsyncMock(return_value=[]),
        health=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(S, "d1_client", d1)
    return d1


@pytest.mark.asyncio
async def test_sync_to_d1_incremental_upsert(monkeypatch):
    svc = _svc()
    d1 = _patch_d1(monkeypatch)
    r = _Redis()
    monkeypatch.setattr(S, "_sync_redis", AsyncMock(return_value=r))
    monkeypatch.setattr(S.SyncService, "_d1_table_has_pk", AsyncMock(return_value=True))
    now = datetime.now(UTC)
    # watermark exists → incremental SELECT
    r.kv["d1sync:last:users"] = now.isoformat()
    _patch_engine(monkeypatch, rows=[(1, now, "alice")])
    out = await svc.sync_table_to_d1("users", debounce=False)
    assert out["synced"] == 1
    # INSERT OR REPLACE issued with serialized values
    assert "INSERT OR REPLACE INTO users" in d1.execute.await_args_list[0].args[0]
    assert d1.execute.await_args_list[0].args[1][0] == 1
    # watermark + hash map persisted
    assert "d1sync:last:users" in r.kv
    assert r.hashes["d1sync:hashes:users"]


@pytest.mark.asyncio
async def test_sync_to_d1_hash_diff_and_delete(monkeypatch):
    svc = _svc()
    d1 = _patch_d1(monkeypatch)
    r = _Redis()
    # D1 mirror has rows id=1 (stale hash) and id=9 (gone from PG)
    import hashlib
    import json

    stale_hash = hashlib.sha1(json.dumps({"id": 1, "name": "old", "updated_at": "x"}, sort_keys=True, default=str).encode()).hexdigest()
    r.hashes["d1sync:hashes:users"] = {
        json.dumps(["1"]): stale_hash,
        json.dumps(["9"]): "hash9",
    }
    monkeypatch.setattr(S, "_sync_redis", AsyncMock(return_value=r))
    monkeypatch.setattr(S.SyncService, "_d1_table_has_pk", AsyncMock(return_value=True))
    _patch_engine(monkeypatch, rows=[(1, None, "alice")], keys=["id", "updated_at", "name"])
    out = await svc.sync_table_to_d1("users", debounce=False)
    # id=1 content changed → upserted; id=9 gone → deleted
    assert out["synced"] == 1
    assert out["deleted"] == 1
    sqls = [c.args[0] for c in d1.execute.await_args_list]
    assert any("INSERT OR REPLACE" in s for s in sqls)
    assert any("DELETE FROM users WHERE id = ?" in s for s in sqls)
    # hash map updated: 9 removed
    assert json.dumps(["9"]) not in r.hashes["d1sync:hashes:users"]


@pytest.mark.asyncio
async def test_sync_to_d1_no_changes_and_debounce(monkeypatch):
    svc = _svc()
    _patch_d1(monkeypatch)
    r = _Redis()
    monkeypatch.setattr(S, "_sync_redis", AsyncMock(return_value=r))
    # empty table → early exit, watermark still persisted
    _patch_engine(monkeypatch, rows=[])
    out = await svc.sync_table_to_d1("users", debounce=True)
    assert out == {"synced": 0, "errors": 0, "skipped": 0}
    assert "d1sync:last:users" in r.kv

    # debounce: second run within interval → skipped
    out2 = await svc.sync_table_to_d1("users", debounce=True)
    assert out2["skipped"] == 1


@pytest.mark.asyncio
async def test_sync_to_d1_pk_less_refusal(monkeypatch):
    svc = _svc()
    _patch_d1(monkeypatch)
    monkeypatch.setattr(S, "_sync_redis", AsyncMock(return_value=_Redis()))
    monkeypatch.setattr(S.SyncService, "_d1_table_has_pk", AsyncMock(return_value=False))
    monkeypatch.setattr(S.SyncService, "_repair_d1_pk", AsyncMock(return_value=False))
    _patch_engine(monkeypatch, rows=[(1, None, "x")])
    out = await svc.sync_table_to_d1("users", debounce=False)
    assert out["errors"] == 1 and out["synced"] == 0


@pytest.mark.asyncio
async def test_sync_to_d1_row_errors_and_circuit(monkeypatch):
    svc = _svc()
    d1 = _patch_d1(monkeypatch)
    monkeypatch.setattr(S, "_sync_redis", AsyncMock(return_value=_Redis()))
    monkeypatch.setattr(S.SyncService, "_d1_table_has_pk", AsyncMock(return_value=True))
    # 4 rows; first 3 fail → abort after 3 consecutive errors
    d1.execute.side_effect = RuntimeError("boom")
    _patch_engine(monkeypatch, rows=[(i, None, f"n{i}") for i in range(4)])
    out = await svc.sync_table_to_d1("users", debounce=False)
    assert out["errors"] == 3 and out["synced"] == 0
    assert d1.execute.await_count == 3  # circuit stopped at 3

    # daily-limit error → breaker flag + immediate break
    d1.execute.reset_mock()
    d1.execute.side_effect = RuntimeError("exceeded daily limit")
    out = await svc.sync_table_to_d1("users", debounce=False)
    assert svc._d1_write_limit_hit is True
    assert d1.execute.await_count == 1


# ── sync_table_to_postgres ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_to_postgres_guards(monkeypatch):
    svc = _svc()
    out = await svc.sync_table_to_d1("nope")  # sanity for parity
    out = await svc.sync_table_to_postgres("DROP_ME")
    assert out["errors"] == 1
    # Postgres-write-owned must never pull from D1
    out = await svc.sync_table_to_postgres("publish_queue")
    assert out["skipped"] == 1
    _patch_d1(monkeypatch, enabled=False)
    out = await svc.sync_table_to_postgres("users")
    assert out["skipped"] == 1


@pytest.mark.asyncio
async def test_sync_to_postgres_upsert_and_dedupe(monkeypatch):
    svc = _svc()
    d1 = _patch_d1(monkeypatch)
    d1.query_all = AsyncMock(
        return_value=[
            {"id": "u1", "name": "alice", "is_active": 1, "tags": '["a"]', "created_at": "2026-10-01T00:00:00Z"},
            {"id": "u1", "name": "alice-new", "is_active": 0, "tags": '["b"]', "created_at": "2026-10-02T00:00:00Z"},
            {"id": "u2", "name": "bob", "is_active": 1, "tags": None, "created_at": "x"},
        ]
    )
    writes = []
    _patch_engine(monkeypatch, on_exec=lambda s, p: writes.append((s, p)))
    out = await svc.sync_table_to_postgres("users")
    # 3 D1 rows dedupe to 2 by PK — last wins
    assert out["synced"] == 2 and out["errors"] == 0
    upserts = [p for s, p in writes if "ON CONFLICT" in s]
    assert len(upserts) == 2
    assert upserts[0]["name"] == "alice-new"  # last write wins
    assert upserts[0]["is_active"] is False  # 0 → False conversion
    assert upserts[0]["tags"] == ["b"]  # JSON → list


@pytest.mark.asyncio
async def test_sync_to_postgres_batch_fallback(monkeypatch):
    svc = _svc()
    d1 = _patch_d1(monkeypatch)
    d1.query_all = AsyncMock(return_value=[{"id": f"u{i}", "v": i} for i in range(3)])
    calls = []

    batch_fail = {"done": False}

    def handler(sql, params):
        calls.append(sql)
        if "ON CONFLICT" in sql:
            if not batch_fail["done"]:
                batch_fail["done"] = True
                raise RuntimeError("batch fail")  # 1st = multi-row txn
            if params and params.get("id") == "u1":
                raise RuntimeError("fk violation")  # per-row fallback
        return _Result()

    # Make the batch (multi-row transaction) fail → per-row fallback
    class _BatchConn(_Conn):
        async def execute(self, sql, params=None):
            return handler(sql, params)

    class _Eng(_Engine):
        def begin(self):
            class _B:
                async def __aenter__(self):
                    return self

                async def __aexit__(self, *a):
                    return False

                async def execute(self, sql, params=None):
                    return handler(str(sql), params)

            return _B()

    import sqlalchemy.ext.asyncio as sa_async

    monkeypatch.setattr(sa_async, "create_async_engine", lambda url: _Eng(handler))
    out = await svc.sync_table_to_postgres("users")
    # batch of 3 fails → per-row: u0 ok, u1 fk fail, u2 ok
    assert out["synced"] == 2 and out["errors"] == 1


@pytest.mark.asyncio
async def test_sync_orchestrators(monkeypatch):
    svc = _svc()
    to_d1 = AsyncMock(return_value={"synced": 1, "errors": 0, "skipped": 0})
    to_pg = AsyncMock(return_value={"synced": 2, "errors": 0, "skipped": 0})
    monkeypatch.setattr(S.SyncService, "sync_table_to_d1", to_d1)
    monkeypatch.setattr(S.SyncService, "sync_table_to_postgres", to_pg)
    out = await svc.sync_all_to_d1()
    assert len(out) == len(S.SYNC_TABLES)
    out = await svc.sync_all_to_postgres()
    assert len(out) == len(S.SYNC_TABLES)

    full = await svc.full_bidirectional_sync()
    assert full["total_rows_synced"] == len(S.SYNC_TABLES) * 3

    monkeypatch.setattr(S.d1_client, "health", AsyncMock(return_value=True))
    h = await svc.health()
    assert h["d1"] is True and h["last_sync"] is False
    svc._last_sync["x"] = datetime.now(UTC)
    assert (await svc.health())["last_sync"] is True

    # sync_tables_to_d1 uses the pk map, unknown → "id"
    res = await svc.sync_tables_to_d1(["users", "not_in_map"])
    assert set(res) == {"users", "not_in_map"}


@pytest.mark.asyncio
async def test_sync_after_worker_task(monkeypatch):
    _patch_d1(monkeypatch, enabled=False)
    assert await S.sync_after_worker_task(["users"]) == {}
    _patch_d1(monkeypatch, enabled=True)
    spy = AsyncMock(return_value={"users": {"synced": 1}})
    monkeypatch.setattr(S.sync_service, "sync_tables_to_d1", spy)
    assert await S.sync_after_worker_task(["users"]) == {"users": {"synced": 1}}
    # errors swallowed
    spy.side_effect = RuntimeError("boom")
    assert await S.sync_after_worker_task(["users"]) == {}


# ── remaining branches ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_redis_client(monkeypatch):
    import redis.asyncio as aioredis

    urls = []
    monkeypatch.setattr(aioredis, "from_url", lambda u, **kw: urls.append((u, kw)) or "client")
    assert await S._sync_redis() == "client"
    assert urls[0][1]["decode_responses"] is True


@pytest.mark.asyncio
async def test_redis_error_paths(monkeypatch):
    svc = _svc()
    _patch_d1(monkeypatch)
    r = _Redis()
    monkeypatch.setattr(S, "_sync_redis", AsyncMock(return_value=r))
    monkeypatch.setattr(S.SyncService, "_d1_table_has_pk", AsyncMock(return_value=True))
    now = datetime.now(UTC)

    # r.get raises → in-memory/None watermark
    monkeypatch.setattr(r, "get", AsyncMock(side_effect=RuntimeError("x")))
    _patch_engine(monkeypatch, rows=[(1, now, "a")])
    out = await svc.sync_table_to_d1("users", debounce=False)
    assert out["synced"] == 1

    # r.hgetall raises → treated as empty diff map
    monkeypatch.setattr(r, "hgetall", AsyncMock(side_effect=RuntimeError("x")))
    out = await svc.sync_table_to_d1("users", debounce=False)
    assert out["synced"] == 1

    # debounce set raises → r closed (aclose also raising is swallowed)
    monkeypatch.setattr(r, "set", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(r, "aclose", AsyncMock(side_effect=RuntimeError("x")))
    out = await svc.sync_table_to_d1("users", debounce=True)
    assert out["synced"] == 1  # proceeds without redis


@pytest.mark.asyncio
async def test_sync_to_d1_value_serialization(monkeypatch):
    svc = _svc()
    d1 = _patch_d1(monkeypatch)
    r = _Redis()
    monkeypatch.setattr(S, "_sync_redis", AsyncMock(return_value=r))
    monkeypatch.setattr(S.SyncService, "_d1_table_has_pk", AsyncMock(return_value=True))
    now = datetime.now(UTC)
    # no updated_at col → hash-diff path; dict + bool + datetime values serialize
    _patch_engine(
        monkeypatch,
        has_updated_at=False,
        keys=["id", "meta", "is_active", "when"],
        rows=[(1, {"a": 1}, True, now)],
    )
    out = await svc.sync_table_to_d1("users", debounce=False)
    assert out["synced"] == 1
    vals = d1.execute.await_args_list[0].args[1]
    assert vals[1] == '{"a": 1}'
    assert vals[2] == 1
    assert vals[3] == now.isoformat()


@pytest.mark.asyncio
async def test_sync_to_d1_stale_delete_edges(monkeypatch):
    import json as _json

    svc = _svc()
    d1 = _patch_d1(monkeypatch)
    r = _Redis()
    # "gone" delete fails w/ daily-limit → circuit breaker
    r.hashes["d1sync:hashes:users"] = {_json.dumps(["gone"]): "h2"}
    monkeypatch.setattr(S, "_sync_redis", AsyncMock(return_value=r))
    monkeypatch.setattr(S.SyncService, "_d1_table_has_pk", AsyncMock(return_value=True))
    async def _exec(sql, *a, **kw):
        if "DELETE" in str(sql):
            raise RuntimeError("daily limit exceeded")
        return None

    d1.execute.side_effect = _exec
    _patch_engine(monkeypatch, has_updated_at=False, rows=[(1, None, "a")])
    out = await svc.sync_table_to_d1("users", debounce=False)
    assert svc._d1_write_limit_hit is True

    # hset/hdel raises → swallowed; non-JSON stale key → skipped by continue
    svc._d1_write_limit_hit = False
    r2 = _Redis()
    r2.hashes["d1sync:hashes:users"] = {"bad-key": "h", _json.dumps(["gone"]): "h2"}
    monkeypatch.setattr(S, "_sync_redis", AsyncMock(return_value=r2))
    monkeypatch.setattr(r2, "hset", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(r2, "hdel", AsyncMock(side_effect=RuntimeError("x")))
    monkeypatch.setattr(r2, "aclose", AsyncMock(side_effect=RuntimeError("x")))
    d1.execute.side_effect = None
    out = await svc.sync_table_to_d1("users", debounce=False)
    assert out["synced"] == 1


@pytest.mark.asyncio
async def test_set_last_sync_redis_down():
    svc = _svc()
    r = SimpleNamespace(set=AsyncMock(side_effect=RuntimeError("x")))
    await svc._set_last_sync("users", r)
    assert "users" in svc._last_sync


@pytest.mark.asyncio
async def test_sync_to_postgres_edges(monkeypatch):
    svc = _svc()
    d1 = _patch_d1(monkeypatch)
    _patch_engine(monkeypatch)
    # empty D1 → early return
    d1.query_all.return_value = []
    out = await svc.sync_table_to_postgres("users")
    assert out == {"synced": 0, "errors": 0, "skipped": 0}

    # all cols are PK → ON CONFLICT DO NOTHING
    sqls = []

    def handler(sql, params):
        sqls.append(sql)
        if sql.strip().lower().startswith("select"):
            return _Result(keys=["id"], rows=[])
        return _Result()

    import sqlalchemy.ext.asyncio as sa_async

    monkeypatch.setattr(sa_async, "create_async_engine", lambda url: _Engine(handler))
    d1.query_all.return_value = [{"id": "a"}, {"id": "b"}]
    out = await svc.sync_table_to_postgres("users", pk="id")
    assert out["synced"] == 2
    assert any("DO NOTHING" in s for s in sqls)
