"""Coverage: synigoros_status bridge poll + notify + wrapper."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest


def _db(row=None):
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.first.return_value = row
    res.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=res)
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


def _client():
    c = MagicMock()
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    return c


@pytest.mark.asyncio
async def test_bridge_call_success(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    c = _client()
    resp = MagicMock(status_code=200)
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value={"ok": 1})
    c.post = AsyncMock(return_value=resp)
    assert await M._bridge_call(c, "/x", {}) == {"ok": 1}
    c.post.assert_awaited_once()


@pytest.mark.asyncio
async def test_bridge_call_retries_409(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    c = _client()
    busy = MagicMock(status_code=409, text="busy")
    ok = MagicMock(status_code=200)
    ok.raise_for_status = MagicMock()
    ok.json = MagicMock(return_value={"ok": 2})
    c.post = AsyncMock(side_effect=[busy, ok])
    assert await M._bridge_call(c, "/x", {}) == {"ok": 2}


@pytest.mark.asyncio
async def test_bridge_call_raises_after_retries(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    c = _client()
    c.post = AsyncMock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(httpx.ConnectError):
        await M._bridge_call(c, "/x", {}, tries=2)


@pytest.mark.asyncio
async def test_ev_returns_result(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(M, "_bridge_call", AsyncMock(return_value={"result": "v"}))
    assert await M._ev(MagicMock(), "1") == "v"


@pytest.mark.asyncio
async def test_poll_page_never_renders(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(M, "_bridge_call", AsyncMock(return_value={}))
    monkeypatch.setattr(M, "_ev", AsyncMock(return_value=False))
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client())
    with pytest.raises(RuntimeError, match="never rendered"):
        await M._poll()


@pytest.mark.asyncio
async def test_poll_status_already_visible(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(M, "_bridge_call", AsyncMock(return_value={}))
    payload = json.dumps({"status": "Αναμονή", "proto": "123", "kpa": "9"})

    async def ev(client, expr):
        if "webMail" in expr:
            return False
        if "Κατάσταση" in expr and "grab" not in expr:
            return True
        if "grab" in expr:
            return payload
        return None

    monkeypatch.setattr(M, "_ev", ev)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client())
    assert (await M._poll())["status"] == "Αναμονή"


@pytest.mark.asyncio
async def test_poll_identify_fails(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(M, "_bridge_call", AsyncMock(return_value={}))

    async def ev(client, expr):
        if "Δεν έγινε ταυτοποίηση" in expr:
            return True
        return "webMail" in expr

    monkeypatch.setattr(M, "_ev", ev)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client())
    with pytest.raises(RuntimeError, match="identify failed"):
        await M._poll()


@pytest.mark.asyncio
async def test_poll_identify_then_extract(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(M, "_bridge_call", AsyncMock(return_value={}))
    payload = json.dumps({"status": "Επεξεργασία", "proto": "55", "kpa": "1"})

    async def ev(client, expr):
        if "Δεν έγινε ταυτοποίηση" in expr:
            return False
        if "grab" in expr:
            return payload
        return "webMail" in expr

    monkeypatch.setattr(M, "_ev", ev)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client())
    assert (await M._poll())["proto"] == "55"


@pytest.mark.asyncio
async def test_poll_close_error_swallowed(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    calls = {"n": 0}

    async def bc(client, path, data):
        calls["n"] += 1
        if path == "/session/close":
            raise httpx.HTTPError("gone")
        return {}

    async def ev(client, expr):
        if "webMail" in expr:
            return False
        if "grab" in expr:
            return json.dumps({"status": "Ολοκληρώθηκε", "proto": "", "kpa": ""})
        return True

    monkeypatch.setattr(M, "_bridge_call", bc)
    monkeypatch.setattr(M, "_ev", ev)
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _client())
    assert (await M._poll())["status"] == "Ολοκληρώθηκε"


@pytest.mark.asyncio
async def test_run_no_status(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(M, "_poll", AsyncMock(return_value={}))
    assert await M._run() == "no-status-extracted"


@pytest.mark.asyncio
async def test_run_unchanged(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(M, "_poll", AsyncMock(return_value={"status": "S1"}))
    row = MagicMock(value="S1")
    monkeypatch.setattr(M, "task_session", lambda: _db(row))
    assert await M._run() == "unchanged"


@pytest.mark.asyncio
async def test_run_changed_existing_row(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(M, "_poll", AsyncMock(return_value={"status": "S2", "proto": "7"}))
    row = MagicMock(value="S1")
    db = _db(row)
    monkeypatch.setattr(M, "task_session", lambda: db)
    monkeypatch.setattr(M, "_notify", AsyncMock())
    assert await M._run() == "notified"
    assert row.value == "S2"


@pytest.mark.asyncio
async def test_run_changed_new_row(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(M, "_poll", AsyncMock(return_value={"status": "S3"}))
    db = _db(None)
    monkeypatch.setattr(M, "task_session", lambda: db)
    monkeypatch.setattr(M, "_notify", AsyncMock())
    assert await M._run() == "notified"
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_notify_no_account(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(M, "task_session", lambda: _db(None))
    await M._notify("s", "p")  # early return, no send


@pytest.mark.asyncio
async def test_notify_sends_via_bot_token(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    acc = MagicMock(meta_data={"bot_token_enc": "enc"}, access_token_enc=b"x")
    monkeypatch.setattr(M, "task_session", lambda: _db(acc))
    monkeypatch.setattr(M, "decrypt_token", lambda b: "tok")
    client = MagicMock()
    client.send_message = AsyncMock()
    monkeypatch.setattr(M, "TelegramAPIClient", lambda t: client)
    await M._notify("new", "42")
    client.send_message.assert_awaited_once()
    assert client.send_message.await_args.args[1].endswith("(πρωτ. 42)")


@pytest.mark.asyncio
async def test_notify_falls_back_to_access_token(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    acc = MagicMock(meta_data={}, access_token_enc=b"enc2")
    monkeypatch.setattr(M, "task_session", lambda: _db(acc))
    monkeypatch.setattr(M, "decrypt_token", lambda b: "tok2")
    client = MagicMock()
    client.send_message = AsyncMock()
    monkeypatch.setattr(M, "TelegramAPIClient", lambda t: client)
    await M._notify("s", "")
    client.send_message.assert_awaited_once()


def test_wrapper_ok(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    def _run(c):
        c.close()
        return "unchanged"

    monkeypatch.setattr(M, "run_async", _run)
    assert M.poll_synigoros() == "unchanged"


def test_wrapper_error(monkeypatch):
    from app.worker.tasks import synigoros_status as M
    monkeypatch.setattr(M, "run_async", MagicMock(side_effect=RuntimeError("boom")))
    assert M.poll_synigoros() == "error: boom"
