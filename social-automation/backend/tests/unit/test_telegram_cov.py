"""Coverage for telegram api helpers — token bytes, decrypt, dm-owner arms."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.telegram as TG

pytestmark = pytest.mark.asyncio


def test_token_bytes():
    assert TG._token_bytes(b"x") == b"x" and TG._token_bytes("x") == b"x"


def test_decrypt_bot_token_paths(monkeypatch):
    acc = SimpleNamespace(meta_data={"bot_token_enc": "enc"}, access_token_enc=b"at")
    monkeypatch.setattr(TG, "decrypt_token", lambda b: "tok")
    assert TG._decrypt_bot_token(acc) == "tok"
    acc2 = SimpleNamespace(meta_data={}, access_token_enc=b"at")
    assert TG._decrypt_bot_token(acc2) == "tok"
    acc3 = SimpleNamespace(meta_data=None, access_token_enc=None)
    with pytest.raises(HTTPException) as e:
        TG._decrypt_bot_token(acc3)
    assert e.value.status_code == 400


def test_decrypt_bot_token_fallback_to_access(monkeypatch):
    acc = SimpleNamespace(meta_data={"bot_token_enc": "bad"}, access_token_enc=b"at")
    seq = iter([Exception("x"), "tok2"])

    def dec(b):
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(TG, "decrypt_token", dec)
    assert TG._decrypt_bot_token(acc) == "tok2"


def test_owner_chat_id_default(monkeypatch):
    assert TG._owner_chat_id() == "7017840434"


async def test_dm_owner_empty_and_except(monkeypatch):
    acc = SimpleNamespace(meta_data={"bot_token_enc": "e"}, access_token_enc=None)
    monkeypatch.setattr(TG, "decrypt_token", lambda b: "tok")
    monkeypatch.setattr(TG, "_owner_chat_id", lambda: "")
    await TG._dm_owner(acc, "hi")  # empty chat_id → early return
    monkeypatch.setattr(TG, "_owner_chat_id", lambda: "123")
    client = SimpleNamespace(send_message=AsyncMock(side_effect=Exception("down")))
    monkeypatch.setattr(TG, "_client_for", lambda a: client)
    await TG._dm_owner(acc, "hi")  # send raises → swallowed
    client.send_message = AsyncMock()
    await TG._dm_owner(acc, "hi")  # success path
    client.send_message.assert_awaited_once()
