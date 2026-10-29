"""Coverage: synigoros_status — bridge retry, poll parse, _run notify, wrapper."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest


def _resp(status=200, body=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = body if body is not None else {}
    r.raise_for_status = MagicMock()
    if status >= 400 and status != 409:
        import httpx

        r.raise_for_status.side_effect = httpx.HTTPStatusError("err", request=None, response=r)
    return r


def test_bridge_success(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M.httpx, "post", MagicMock(return_value=_resp(200, {"result": 5})))
    assert M._bridge("/x", {}) == {"result": 5}


def test_bridge_409_retries_then_ok(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    seq = [_resp(409), _resp(200, {"ok": 1})]
    monkeypatch.setattr(M.httpx, "post", MagicMock(side_effect=seq))
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    assert M._bridge("/x", {}) == {"ok": 1}


def test_bridge_exhausts(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M.httpx, "post", MagicMock(return_value=_resp(409)))
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    assert M._bridge("/x", {}, tries=2) == {}


def test_bridge_context_destroyed_retries(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    seq = [_resp(500, text="context was destroyed"), _resp(200, {"ok": 1})]
    monkeypatch.setattr(M.httpx, "post", MagicMock(side_effect=seq))
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    assert M._bridge("/x", {}) == {"ok": 1}


def test_ev_delegates(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"result": "v"})
    assert M._ev("1") == "v"


def test_poll_parses_status(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    calls = {"n": 0}

    def _ev(expr):
        calls["n"] += 1
        if "webMail" in expr:
            return False
        if "getElementById('webMail')" in expr or "innerText.includes('Κατάσταση')" in expr:
            return True
        if "Δεν έγινε" in expr:
            return False
        return json.dumps({"status": "Αναμονή", "proto": "11", "kpa": M.KPA})

    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"ok": 1})
    monkeypatch.setattr(M, "_ev", _ev)
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    out = M._poll()
    assert out["status"] == "Αναμονή"


def test_poll_never_renders(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"ok": 1})
    monkeypatch.setattr(M, "_ev", lambda e: False)
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="never rendered"):
        M._poll()


def test_poll_identify_fails(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    def _ev(expr):
        if "Κατάσταση" in expr:
            return False  # status never renders — failure path must win
        return True

    monkeypatch.setattr(M, "_bridge", lambda p, d, **k: {"ok": 1})
    monkeypatch.setattr(M, "_ev", _ev)
    monkeypatch.setattr(M.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="identify failed"):
        M._poll()


def _db(last=None, acc=None):
    db = MagicMock()
    res1 = MagicMock()
    res1.scalar_one_or_none.return_value = last
    res2 = MagicMock()
    res2.scalar_one.return_value = acc
    db.execute = AsyncMock(side_effect=[res1, res2])
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    return db


@pytest.mark.asyncio
async def test_run_unchanged_no_telegram(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    last = MagicMock(meta_data={"status": "Αναμονή"}, team_id="t")
    acc = MagicMock(team_id="t", meta_data={}, access_token_enc=b"e")
    monkeypatch.setattr(M, "_poll", lambda: {"status": "Αναμονή", "proto": "11"})
    monkeypatch.setattr(M, "task_session", lambda: _db(last, acc))
    send = AsyncMock()
    monkeypatch.setattr(M.TelegramAPIClient, "send_message", send, raising=False)
    out = await M._run()
    assert not out.get("telegram_notified")


@pytest.mark.asyncio
async def test_run_changed_notifies(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    last = MagicMock(meta_data={"status": "Αναμονή"}, team_id="t")
    acc = MagicMock(team_id="t", meta_data={"bot_token_enc": "enc"}, access_token_enc=b"e")
    monkeypatch.setattr(M, "MAIL", "m"); monkeypatch.setattr(M, "KPA", "k")
    monkeypatch.setattr(M, "_poll", lambda: {"status": "Ολοκληρώθηκε", "proto": "11"})
    monkeypatch.setattr(M, "task_session", lambda: _db(last, acc))
    monkeypatch.setattr(M, "decrypt_token", lambda b: "tok")
    tg = MagicMock()
    tg.send_message = AsyncMock()
    monkeypatch.setattr(M, "TelegramAPIClient", MagicMock(return_value=tg))
    out = await M._run()
    assert out["telegram_notified"] is True
    tg.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_new_row_no_last(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    acc = MagicMock(team_id="t", meta_data={}, access_token_enc=b"e")
    monkeypatch.setattr(M, "MAIL", "m"); monkeypatch.setattr(M, "KPA", "k")
    monkeypatch.setattr(M, "_poll", lambda: {"status": "Αναμονή"})
    monkeypatch.setattr(M, "task_session", lambda: _db(None, acc))
    monkeypatch.setattr(M, "decrypt_token", lambda b: "tok")
    tg = MagicMock()
    tg.send_message = AsyncMock()
    monkeypatch.setattr(M, "TelegramAPIClient", MagicMock(return_value=tg))
    out = await M._run()
    assert out["telegram_notified"] is True


def test_wrapper_calls_run_async(monkeypatch):
    from app.worker.tasks import synigoros_status as M

    monkeypatch.setattr(M, "run_async", lambda c: (c.close(), {"ok": 1})[1])
    assert M.poll_synigoros() == {"ok": 1}
