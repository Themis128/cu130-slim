"""Unit tests for app/services/d1_client.py — Cloudflare D1 REST client."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.d1_client as D1


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(D1.settings, "CLOUDFLARE_ACCOUNT_ID", "acct",
                        raising=False)
    monkeypatch.setattr(D1.settings, "D1_SOCIAL_AUTOMATION_ID", "dbid",
                        raising=False)
    monkeypatch.setattr(D1.settings, "CLOUDFLARE_API_TOKEN", "tok1",
                        raising=False)
    monkeypatch.setattr(D1.settings, "CLOUDFLARE_AI_API_TOKEN", "tok2",
                        raising=False)
    monkeypatch.setattr(D1.settings, "CLOUDFLARE_EMAIL_API_TOKEN", "",
                        raising=False)
    monkeypatch.setattr(D1.settings, "D1_ENABLED", True, raising=False)
    c = D1.D1Client()
    return c


def _resp(status=200, body=None, text=""):
    return SimpleNamespace(status_code=status, text=text,
                           json=lambda: body if body is not None else {})


def _ok(rows=None, rows_written=0):
    return _resp(200, {"success": True, "result": [
        {"results": rows or [],
         "meta": {"rows_written": rows_written}}]})


class _Http:
    def __init__(self, on_post=None):
        self.on_post = on_post
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *e):
        return False

    async def post(self, url, **kw):
        self.calls.append(kw.get("json"))
        return await self.on_post(url, **kw)


def _wire(monkeypatch, c, on_post):
    http = _Http(on_post)
    monkeypatch.setattr(D1.httpx, "AsyncClient", lambda *a, **k: http)
    return http


def _no_redis(monkeypatch):
    """Redis unavailable → local counter fallback."""
    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url",
                        Mock(side_effect=ConnectionError("down")))


# ── config surface ────────────────────────────────────────────────────


def test_config_surface(client, monkeypatch):
    assert client.enabled is True
    assert "acct" in client.base_url and "dbid" in client.base_url
    assert client._headers()["Authorization"] == "Bearer tok1"

    # D1_ENABLED=False → disabled
    monkeypatch.setattr(D1.settings, "D1_ENABLED", False, raising=False)
    assert client.enabled is False
    monkeypatch.setattr(D1.settings, "D1_ENABLED", True, raising=False)

    # missing db_id → disabled
    monkeypatch.setattr(D1.settings, "D1_SOCIAL_AUTOMATION_ID", "",
                        raising=False)
    c2 = D1.D1Client()
    assert c2.enabled is False

    # token chain — all three present → primary is first
    monkeypatch.setattr(D1.settings, "CLOUDFLARE_EMAIL_API_TOKEN", "tok3",
                        raising=False)
    c3 = D1.D1Client()
    assert c3._tokens == ["tok1", "tok2", "tok3"]
    assert c3.api_token == "tok1"


def test_is_write_and_serialize():
    for sql in ("INSERT INTO t", " update t", "DELETE", "replace into"):
        assert D1.D1Client._is_write(sql)
    for sql in ("SELECT 1", " create table", "PRAGMA"):
        assert not D1.D1Client._is_write(sql)

    # _serialize_param — uuid/datetime/enum/dict/primitives
    u = uuid.uuid4()
    assert D1.D1Client._serialize_param(u) == str(u)
    dt = datetime(2026, 1, 1, tzinfo=UTC)
    assert D1.D1Client._serialize_param(dt) == dt.isoformat()
    assert D1.D1Client._serialize_param({"a": 1}) == '{"a": 1}'
    assert D1.D1Client._serialize_param(True) == 1  # bool → int? or stays
    assert D1.D1Client._serialize_param(5) == 5
    assert D1.D1Client._serialize_param(None) is None


# ── token fallback ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_token_fallback(client, monkeypatch):
    calls = []

    async def req(token):
        calls.append(token)
        return _resp(401) if token == "tok1" else _resp(200, {"ok": 1})
    out = await client._try_with_token_fallback(req)
    assert out.status_code == 200 and calls == ["tok1", "tok2"]
    assert client._active_token == "tok2"

    # all tokens 403 → every token tried, _auth_dead set, last 403 returned
    async def req403(token):
        calls.append(token)
        return _resp(403)
    calls.clear()
    out = await client._try_with_token_fallback(req403)
    assert out.status_code == 403
    assert calls == ["tok1", "tok2"] and client._auth_dead is True

    # auth_dead guard — once set, raises before any request
    with pytest.raises(RuntimeError, match="D1 disabled"):
        await client._try_with_token_fallback(req403)

    # all tokens 401 → last 401 response returned (line ~119)
    client._auth_dead = False

    async def req401(token):
        return _resp(401)
    out = await client._try_with_token_fallback(req401)
    assert out.status_code == 401

    # request raises → try next; all fail → RuntimeError
    client._auth_dead = False

    async def boom(token):
        raise ConnectionError("x")
    with pytest.raises(RuntimeError):
        await client._try_with_token_fallback(boom)

    # mixed: first raises, second succeeds
    calls2 = []
    async def mix(token):
        calls2.append(token)
        if token == "tok1":
            raise ConnectionError("x")
        return _resp(200)
    out = await client._try_with_token_fallback(mix)
    assert out.status_code == 200 and calls2 == ["tok1", "tok2"]


# ── execute paths ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_paths(client, monkeypatch):
    _no_redis(monkeypatch)

    # not configured → RuntimeError before HTTP
    client._enabled = False
    with pytest.raises(RuntimeError, match="not configured"):
        await client.execute("SELECT 1")
    client._enabled = True

    # happy SELECT → rows unwrapped, no params key when empty
    http = _wire(monkeypatch, client,
                 AsyncMock(return_value=_ok([{"a": 1}])))
    out = await client.execute("SELECT * FROM t")
    assert out == [{"a": 1}]
    assert "params" not in http.calls[0]

    # params serialized
    http = _wire(monkeypatch, client,
                 AsyncMock(return_value=_ok()))
    u = uuid.uuid4()
    await client.execute("SELECT ? WHERE x", [u])
    assert http.calls[0]["params"] == [str(u)]

    # HTTP >=400 → RuntimeError; "write limit" msg → throttled + push date
    client._write_count_date = None
    _wire(monkeypatch, client, AsyncMock(return_value=_resp(
        400, {"errors": [{"message": "write limit exceeded"}]})))
    with pytest.raises(RuntimeError, match="D1 HTTP 400"):
        await client.execute("SELECT 1")
    assert client._write_throttled is True

    # success=False → error message
    _wire(monkeypatch, client, AsyncMock(return_value=_resp(
        200, {"success": False, "errors": [{"message": "syntax"}]})))
    with pytest.raises(RuntimeError, match="syntax"):
        await client.execute("SELECT bad")

    # write → rows_written tracked on local counter
    client._write_count = 0
    client._write_count_date = datetime.now(UTC).date()
    _wire(monkeypatch, client, AsyncMock(return_value=_ok(
        rows_written=3)))
    await client.execute("INSERT INTO t VALUES (1)")
    assert client._write_count == 3

    # budget exhausted → throttled RuntimeError before HTTP
    client._write_count = D1.D1Client.D1_DAILY_WRITE_LIMIT
    client._write_count_date = datetime.now(UTC).date()
    with pytest.raises(RuntimeError, match="budget exhausted"):
        await client.execute("INSERT INTO t VALUES (1)")
    # SELECT still allowed while write-throttled
    _wire(monkeypatch, client, AsyncMock(return_value=_ok()))
    await client.execute("SELECT 1")


@pytest.mark.asyncio
async def test_redis_shared_counter(client, monkeypatch):
    # shared counter present → authoritative (overrides local)
    r = SimpleNamespace(
        get=AsyncMock(return_value="42"), incrby=AsyncMock(),
        expire=AsyncMock(), aclose=AsyncMock())
    import redis.asyncio as aioredis
    monkeypatch.setattr(aioredis, "from_url", lambda *a, **k: r)
    client._write_count = 0
    assert await client._shared_writes_today() == 42
    await client._maybe_reset_daily_counter()
    assert client._write_count == 42
    await client._record_writes(5)
    r.incrby.assert_awaited_once()
    assert client._write_count == 47

    # redis down → None, local fallback
    monkeypatch.setattr(aioredis, "from_url",
                        Mock(side_effect=ConnectionError("x")))
    assert await client._shared_writes_today() is None
    client._write_count_date = datetime.now(UTC).date() - timedelta(days=1)
    client._write_count = 9
    await client._maybe_reset_daily_counter()  # stale date → reset
    assert client._write_count == 0

    # write_budget status shape
    client._write_count = 10
    out = await client.write_budget()
    assert out["writes_today"] == 10
    assert out["remaining"] == 100_000 - 10


# ── CRUD helpers ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_crud_helpers(client, monkeypatch):
    _no_redis(monkeypatch)
    executed = []

    async def fake_exec(sql, params=None):
        executed.append((sql, params))
        if "count" in sql:
            return [{"cnt": 7}]
        if "sqlite_master" in sql:
            return [{"name": "posts"}, {"name": "_cf_internal"}]
        return []

    monkeypatch.setattr(client, "execute", fake_exec)

    assert await client.query_one("SELECT 1") is None
    monkeypatch.setattr(client, "execute",
                        AsyncMock(return_value=[{"r": 1}]))
    assert await client.query_one("SELECT 1") == {"r": 1}
    assert await client.query_all("SELECT 1") == [{"r": 1}]

    monkeypatch.setattr(client, "execute", fake_exec)
    out = await client.insert("t", {"a": 1, "b": "x"})
    assert out == {"a": 1, "b": "x"}
    assert executed[-1][0] == "INSERT INTO t (a, b) VALUES (?, ?)"
    assert executed[-1][1] == [1, "x"]

    n = await client.update("t", {"a": 2}, "id = ?", [9])
    assert n == 1
    assert executed[-1][0] == "UPDATE t SET a = ? WHERE id = ?"
    assert executed[-1][1] == [2, 9]

    n = await client.delete("t", "id = ?", [9])
    assert n == 1

    assert await client.count("t", "x > ?", [1]) == 7
    monkeypatch.setattr(client, "execute", AsyncMock(return_value=[]))
    assert await client.count("empty") == 0
    monkeypatch.setattr(client, "execute", fake_exec)
    assert await client.table_exists("posts") is True
    assert await client.list_tables() == ["posts"]  # _cf filtered

    # execute_many — per-row failures tolerated
    async def fail_second(sql, params=None):
        if params == [2]:
            raise RuntimeError("row fail")
        return []
    monkeypatch.setattr(client, "execute", fail_second)
    assert await client.execute_many("INSERT", [[1], [2], [3]]) == 2

    # disabled → execute_many raises
    client._enabled = False
    with pytest.raises(RuntimeError):
        await client.execute_many("INSERT", [[1]])
    client._enabled = True

    # health — disabled → False; execute ok → True; throws → False
    client._enabled = False
    assert await client.health() is False
    client._enabled = True
    monkeypatch.setattr(client, "execute", AsyncMock(return_value=[]))
    assert await client.health() is True
    monkeypatch.setattr(client, "execute", AsyncMock(
        side_effect=RuntimeError("x")))
    assert await client.health() is False


@pytest.mark.asyncio
async def test_execute_error_json_and_empty_results(client, monkeypatch):
    _no_redis(monkeypatch)

    # error body is not JSON → err_msg falls back to resp.text
    bad = SimpleNamespace(status_code=500, text="not-json-body")
    def _raise():
        raise ValueError("no json")
    bad.json = _raise
    _wire(monkeypatch, client, AsyncMock(return_value=bad))
    with pytest.raises(RuntimeError, match="D1 HTTP 500: not-json-body"):
        await client.execute("SELECT 1")

    # success but empty result list → []
    _wire(monkeypatch, client, AsyncMock(
        return_value=_resp(200, {"success": True, "result": []})))
    assert await client.execute("SELECT * FROM t") == []
