"""Coverage for app/services/db_router.py — D1/Postgres dual-write router."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.db_router as DBR
from app.services.db_router import DualWriteRouter


def _d1(enabled=True, **kw):
    d = SimpleNamespace(
        enabled=enabled,
        query_all=AsyncMock(return_value=[{"id": 1}]),
        execute=AsyncMock(return_value=7),
        health=AsyncMock(return_value=True),
    )
    for k, v in kw.items():
        setattr(d, k, v)
    return d


class _CM:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *a):
        pass


class _Conn:
    def __init__(self, result):
        self._result = result
        self.executed = []

    async def execute(self, stmt, params=None):
        self.executed.append((str(stmt), params))
        return self._result


class _Engine:
    def __init__(self, conn):
        self._conn = conn
        self.disposed = False

    def connect(self):
        return _CM(self._conn)

    def begin(self):
        return _CM(self._conn)

    async def dispose(self):
        self.disposed = True


def _wire_pg(monkeypatch, result):
    conn = _Conn(result)
    engine = _Engine(conn)
    import sqlalchemy.ext.asyncio as sa_async
    monkeypatch.setattr(sa_async, "create_async_engine", lambda url: engine)
    import app.core.config as cfg
    monkeypatch.setattr(cfg, "settings",
                        SimpleNamespace(DATABASE_URL="postgresql://x"))
    return conn, engine


class TestCircuit:
    def test_closed_allows(self):
        r = DualWriteRouter()
        assert r._check_circuit() is True

    def test_open_young_blocks(self):
        r = DualWriteRouter()
        r._circuit_open = True
        r._circuit_opened_at = datetime.now(UTC)
        assert r._check_circuit() is False

    def test_open_old_resets(self):
        r = DualWriteRouter()
        r._circuit_open = True
        r._circuit_opened_at = datetime.now(UTC) - timedelta(seconds=120)
        r._failure_count = 5
        assert r._check_circuit() is True
        assert r._circuit_open is False
        assert r._failure_count == 0

    def test_record_failure_opens_at_threshold(self):
        r = DualWriteRouter()
        for _ in range(2):
            r._record_failure()
        assert r._circuit_open is False
        r._record_failure()
        assert r._circuit_open is True
        assert r._circuit_opened_at is not None

    def test_record_success_closes(self):
        r = DualWriteRouter()
        r._circuit_open = True
        r._failure_count = 3
        r._record_success()
        assert r._failure_count == 0
        assert r._circuit_open is False


class TestQuery:
    @pytest.mark.asyncio
    async def test_d1_primary(self, monkeypatch):
        d1 = _d1()
        monkeypatch.setattr(DBR, "d1_client", d1)
        r = DualWriteRouter()
        out = await r.query("SELECT 1")
        assert out == [{"id": 1}]
        d1.query_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_d1_failover_to_pg(self, monkeypatch):
        d1 = _d1(query_all=AsyncMock(side_effect=RuntimeError("d1 down")))
        monkeypatch.setattr(DBR, "d1_client", d1)
        result = SimpleNamespace(
            keys=lambda: ["a"], fetchall=lambda: [(1,), (2,)])
        conn, engine = _wire_pg(monkeypatch, result)
        r = DualWriteRouter()
        out = await r.query("SELECT a FROM t WHERE id = ?", [5])
        assert out == [{"a": 1}, {"a": 2}]
        assert r._failure_count == 1
        assert engine.disposed
        assert ":param_0" in conn.executed[0][0]
        assert conn.executed[0][1] == {"param_0": 5}

    @pytest.mark.asyncio
    async def test_circuit_open_skips_d1(self, monkeypatch):
        d1 = _d1()
        monkeypatch.setattr(DBR, "d1_client", d1)
        result = SimpleNamespace(keys=lambda: ["x"], fetchall=lambda: [])
        _wire_pg(monkeypatch, result)
        r = DualWriteRouter()
        r._circuit_open = True
        r._circuit_opened_at = datetime.now(UTC)
        await r.query("SELECT 1")
        d1.query_all.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_d1_disabled_goes_pg(self, monkeypatch):
        monkeypatch.setattr(DBR, "d1_client", _d1(enabled=False))
        result = SimpleNamespace(keys=lambda: ["x"], fetchall=lambda: [])
        _wire_pg(monkeypatch, result)
        assert await DualWriteRouter().query("SELECT 1") == []

    @pytest.mark.asyncio
    async def test_query_one(self, monkeypatch):
        d1 = _d1(query_all=AsyncMock(return_value=[{"a": 1}, {"a": 2}]))
        monkeypatch.setattr(DBR, "d1_client", d1)
        r = DualWriteRouter()
        assert await r.query_one("SELECT") == {"a": 1}
        d1.query_all = AsyncMock(return_value=[])
        assert await r.query_one("SELECT") is None


class TestExecute:
    @pytest.mark.asyncio
    async def test_dual_write_success(self, monkeypatch):
        d1 = _d1(execute=AsyncMock(return_value=42))
        monkeypatch.setattr(DBR, "d1_client", d1)
        _wire_pg(monkeypatch, SimpleNamespace(rowcount=1))
        r = DualWriteRouter()
        out = await r.execute("INSERT INTO t (a) VALUES (?)", [1])
        assert out == 42  # d1 result wins
        assert r._replay_queue == []

    @pytest.mark.asyncio
    async def test_d1_fail_queues_replay(self, monkeypatch):
        d1 = _d1(execute=AsyncMock(side_effect=RuntimeError("x")))
        monkeypatch.setattr(DBR, "d1_client", d1)
        _wire_pg(monkeypatch, SimpleNamespace(rowcount=3))
        r = DualWriteRouter()
        out = await r.execute("INSERT INTO t (a) VALUES (?)", [9], table="t")
        assert out == 3  # pg result
        assert len(r._replay_queue) == 1
        assert r._replay_queue[0]["table"] == "t"
        assert r._replay_queue[0]["params"] == [9]

    @pytest.mark.asyncio
    async def test_disabled_no_circuit_no_queue(self, monkeypatch):
        monkeypatch.setattr(DBR, "d1_client", _d1(enabled=False))
        _wire_pg(monkeypatch, SimpleNamespace(rowcount=1))
        r = DualWriteRouter()
        await r.execute("INSERT INTO t (a) VALUES (1)")
        assert r._replay_queue == []

    @pytest.mark.asyncio
    async def test_circuit_open_still_queues(self, monkeypatch):
        monkeypatch.setattr(DBR, "d1_client", _d1(enabled=False))
        _wire_pg(monkeypatch, SimpleNamespace(rowcount=1))
        r = DualWriteRouter()
        r._circuit_open = True
        r._circuit_opened_at = datetime.now(UTC)
        await r.execute("INSERT INTO t (a) VALUES (1)")
        assert len(r._replay_queue) == 1


class TestSqlConversion:
    def test_insert_or_replace_id_conflict(self):
        sql = "INSERT OR REPLACE INTO users (id, name, email) VALUES (?, ?, ?)"
        out = DualWriteRouter._sqlite_to_pg_sql(sql)
        assert "ON CONFLICT (id) DO UPDATE" in out
        assert "name = EXCLUDED.name" in out
        assert "email = EXCLUDED.email" in out
        assert out.startswith("INSERT INTO users")

    def test_insert_or_replace_no_id_col(self):
        sql = "INSERT OR REPLACE INTO kv (k, v) VALUES (?, ?)"
        out = DualWriteRouter._sqlite_to_pg_sql(sql)
        assert "ON CONFLICT (k) DO UPDATE SET v = EXCLUDED.v" in out

    def test_passthrough(self):
        sql = "UPDATE t SET a = ? WHERE b = ?"
        assert DualWriteRouter._sqlite_to_pg_sql(sql) == sql


class TestDeserialize:
    def test_iso_datetime(self):
        out = DualWriteRouter._deserialize_for_pg("2026-01-02T03:04:05")
        assert out == datetime(2026, 1, 2, 3, 4, 5)

    def test_z_suffix(self):
        out = DualWriteRouter._deserialize_for_pg("2026-01-02T03:04:05Z")
        assert out.tzinfo is not None

    def test_invalid_iso_passthrough(self):
        # matches regex but fails fromisoformat
        out = DualWriteRouter._deserialize_for_pg("2026-13-99T99:99:99")
        assert out == "2026-13-99T99:99:99"

    def test_non_iso_passthrough(self):
        assert DualWriteRouter._deserialize_for_pg("plain") == "plain"
        assert DualWriteRouter._deserialize_for_pg(42) == 42
        assert DualWriteRouter._deserialize_for_pg(None) is None


class TestBoolParams:
    def test_insert_bool_cols(self):
        sql = "INSERT INTO t (name, is_enabled, success) VALUES (?, ?, ?)"
        out = DualWriteRouter._convert_bool_params(sql, ["x", 1, 0])
        assert out == ["x", True, False]

    def test_non_insert_passthrough(self):
        sql = "UPDATE t SET is_active = ?"
        assert DualWriteRouter._convert_bool_params(sql, [1]) == [1]

    def test_none_params(self):
        assert DualWriteRouter._convert_bool_params("INSERT INTO t (a) VALUES (?)", None) is None

    def test_non_int_untouched(self):
        sql = "INSERT INTO t (is_live) VALUES (?)"
        assert DualWriteRouter._convert_bool_params(sql, ["yes"]) == ["yes"]


class TestPostgresExecute:
    @pytest.mark.asyncio
    async def test_execute_rowcount(self, monkeypatch):
        conn, engine = _wire_pg(monkeypatch, SimpleNamespace(rowcount=3))
        r = DualWriteRouter()
        out = await r._postgres_execute(
            "INSERT INTO t (is_enabled) VALUES (?)", [1])
        assert out == 3
        assert conn.executed[0][1] == {"param_0": True}
        assert engine.disposed

    @pytest.mark.asyncio
    async def test_query_no_params(self, monkeypatch):
        result = SimpleNamespace(keys=lambda: ["c"], fetchall=lambda: [(9,)])
        conn, _ = _wire_pg(monkeypatch, result)
        out = await DualWriteRouter()._postgres_query("SELECT c")
        assert out == [{"c": 9}]
        assert conn.executed[0][1] is None


class TestReplayQueue:
    @pytest.mark.asyncio
    async def test_empty_or_disabled(self, monkeypatch):
        r = DualWriteRouter()
        monkeypatch.setattr(DBR, "d1_client", _d1())
        assert await r.replay_queue() == 0
        r._replay_queue.append({"sql": "x", "params": []})
        monkeypatch.setattr(DBR, "d1_client", _d1(enabled=False))
        assert await r.replay_queue() == 0

    @pytest.mark.asyncio
    async def test_circuit_open_blocks(self, monkeypatch):
        r = DualWriteRouter()
        r._replay_queue.append({"sql": "x", "params": []})
        r._circuit_open = True
        r._circuit_opened_at = datetime.now(UTC)
        monkeypatch.setattr(DBR, "d1_client", _d1())
        assert await r.replay_queue() == 0

    @pytest.mark.asyncio
    async def test_replays_success_keeps_failures(self, monkeypatch):
        d1 = _d1(execute=AsyncMock(side_effect=[None, RuntimeError("boom")]))
        monkeypatch.setattr(DBR, "d1_client", d1)
        r = DualWriteRouter()
        r._replay_queue = [
            {"sql": "good", "params": []},
            {"sql": "bad", "params": []},
        ]
        assert await r.replay_queue() == 1
        assert len(r._replay_queue) == 1
        assert r._replay_queue[0]["sql"] == "bad"


class TestHealth:
    @pytest.mark.asyncio
    async def test_dual_mode(self, monkeypatch):
        monkeypatch.setattr(DBR, "d1_client", _d1())
        r = DualWriteRouter()
        r._replay_queue.append({})
        out = await r.health()
        assert out == {"d1_primary": True, "circuit_open": False,
                       "failure_count": 0, "replay_queue_size": 1,
                       "mode": "dual"}

    @pytest.mark.asyncio
    async def test_postgres_only(self, monkeypatch):
        monkeypatch.setattr(DBR, "d1_client", _d1(enabled=False))
        out = await DualWriteRouter().health()
        assert out["d1_primary"] is False
        assert out["mode"] == "postgres_only"
