"""Coverage tails: log_intel loki, mastersheet bridge, datalake_executor helpers."""
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _resp(status=200, body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = body if body is not None else {}
    r.raise_for_status = MagicMock()
    return r


# ---- log_intel._query_loki ----

@pytest.mark.asyncio
async def test_loki_streams(monkeypatch):
    from app.services import log_intel as M
    body = {"data": {"result": [
        {"stream": {"container_name": "api"}, "values": [[1, "err one"], [2, "err two"]]},
        {"stream": {"job": "j"}, "values": [[3, "warn"]]},
    ]}}
    c = MagicMock()
    c.get = AsyncMock(return_value=_resp(200, body))
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: c)
    out = await M._query_loki(30)
    assert "[api] err one" in out and "[j] warn" in out


@pytest.mark.asyncio
async def test_loki_non200(monkeypatch):
    from app.services import log_intel as M
    c = MagicMock()
    c.get = AsyncMock(return_value=_resp(500, text="down"))
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: c)
    assert await M._query_loki(5) == []


# ---- mastersheet_watch._bridge / _ev ----

def test_ms_bridge_ok(monkeypatch):
    from app.worker.tasks import mastersheet_watch as M
    monkeypatch.setattr(M.httpx, "post", MagicMock(return_value=_resp(200, {"result": 3})))
    assert M._bridge("/x", {}) == {"result": 3}


def test_ms_bridge_409_retries(monkeypatch):
    from app.worker.tasks import mastersheet_watch as M
    monkeypatch.setattr(M.httpx, "post",
                        MagicMock(side_effect=[_resp(409), _resp(200, {"ok": 1})]))
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    assert M._bridge("/x", {}) == {"ok": 1}


def test_ms_bridge_raises_last(monkeypatch):
    from app.worker.tasks import mastersheet_watch as M
    r = _resp(500, text="fatal")
    r.raise_for_status.side_effect = httpx.HTTPStatusError("e", request=None, response=r)
    monkeypatch.setattr(M.httpx, "post", MagicMock(return_value=r))
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    with pytest.raises(httpx.HTTPStatusError):
        M._bridge("/x", {}, tries=2)


def test_ms_ev(monkeypatch):
    from app.worker.tasks import mastersheet_watch as M
    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"result": "v"})
    assert M._ev("x") == "v"


# ---- datalake_executor helpers ----

def _db(res):
    db = MagicMock()
    r = MagicMock()
    r.scalar_one_or_none.return_value = res
    db.execute = AsyncMock(return_value=r)
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


@pytest.mark.asyncio
async def test_latest_signals(monkeypatch):
    from app.worker.tasks import datalake_executor as M
    ev = MagicMock(meta_data={"a": 1})
    assert (await M._latest_signals(_db(ev))) == {"a": 1}


@pytest.mark.asyncio
async def test_latest_signals_empty(monkeypatch):
    from app.worker.tasks import datalake_executor as M
    assert await M._latest_signals(_db(None)) == {}


@pytest.mark.asyncio
async def test_already_executed(monkeypatch):
    from app.worker.tasks import datalake_executor as M
    r = MagicMock()
    r.scalar_one_or_none.return_value = MagicMock()
    db = MagicMock()
    db.execute = AsyncMock(return_value=r)
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    assert await M._already_executed(db, "t", "k") is True
