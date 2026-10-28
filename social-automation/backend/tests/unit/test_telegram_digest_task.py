"""Unit tests for app/worker/tasks/telegram_digest.py."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.telegram_digest as TD


def _account(**kw):
    base = dict(id=uuid.uuid4(), platform="telegram", status="active",
                access_token_enc="enc", meta_data={})
    base.update(kw)
    return SimpleNamespace(**base)


def _db(accounts):
    class _DB:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: accounts))

    return _DB


def _meta(**over):
    return {"telegram_group_watch": {
        "enabled": True, "digest_enabled": True,
        "owner_chat_id": "999", **over}}


@pytest.mark.asyncio
async def test_run_digests_skips(monkeypatch):
    # no accounts
    monkeypatch.setattr(TD, "_worker_db", lambda: _db([])())
    out = await TD._run_digests()
    assert out == {"accounts": 0, "sent": 0, "results": []}

    accts = [
        _account(meta_data={"telegram_group_watch": {"enabled": False}}),
        _account(meta_data=_meta(digest_enabled=False)),
        _account(meta_data=_meta(owner_chat_id=None)),
    ]
    monkeypatch.setattr(TD, "_worker_db", lambda: _db(accts)())
    out = await TD._run_digests()
    assert out["accounts"] == 0 and out["results"] == []


@pytest.mark.asyncio
async def test_run_digests_paths(monkeypatch):
    monkeypatch.setattr(TD, "decrypt_token", lambda t: f"dec-{t}")
    send = AsyncMock(return_value={"sent": 2, "reason": ""})
    monkeypatch.setattr(TD, "send_digest_for_account", send)
    monkeypatch.setattr(TD, "TelegramAPIClient",
                        lambda t: SimpleNamespace(t=t))

    # no token → error result (bytes enc still decrypts empty)
    acct = _account(meta_data=_meta(), access_token_enc=b"enc")
    monkeypatch.setattr(TD, "decrypt_token", lambda t: "")
    monkeypatch.setattr(TD, "_worker_db", lambda: _db([acct])())
    out = await TD._run_digests()
    assert out["accounts"] == 1
    assert out["results"][0]["error"] == "no_token"

    # bytes token path — happy
    monkeypatch.setattr(TD, "decrypt_token", lambda t: f"dec-{t}")
    acct = _account(meta_data=_meta(), access_token_enc=b"enc")
    monkeypatch.setattr(TD, "_worker_db", lambda: _db([acct])())
    out = await TD._run_digests()
    assert out["sent"] == 2 and out["results"][0]["sent"] == 2
    send.assert_awaited_once()

    # meta bot_token_enc fallback when access_token_enc not bytes
    send.reset_mock()
    acct = _account(meta_data={**_meta(), "bot_token_enc": "menc"},
                    access_token_enc="plain")
    monkeypatch.setattr(TD, "_worker_db", lambda: _db([acct])())
    out = await TD._run_digests()
    assert out["sent"] == 2

    # per-account exception → error captured, loop continues
    send.side_effect = [RuntimeError("down"),
                        {"sent": 1, "reason": ""}]
    accts = [_account(meta_data=_meta(), access_token_enc=b"enc"),
             _account(meta_data=_meta(), access_token_enc=b"enc")]
    monkeypatch.setattr(TD, "_worker_db", lambda: _db(accts)())
    out = await TD._run_digests()
    assert out["accounts"] == 2 and out["sent"] == 1
    assert "down" in out["results"][0]["error"]


def test_task_wrapper(monkeypatch):
    monkeypatch.setattr(TD, "run_async",
                        lambda c: (c.close(), {"ok": 1})[1])
    assert TD.send_telegram_group_digests() == {"ok": 1}
