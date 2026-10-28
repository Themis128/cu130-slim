"""Unit tests for the Messenger Platform API router (app/api/messenger_api.py).

Covers account resolution/authorization, client construction from page
credentials, and the send paths — previously 0% covered.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api import messenger_api
from app.api.messenger_api import (
    SendMessageRequest,
    SendQuickRepliesRequest,
    _get_facebook_page_account,
    _get_messenger_client,
    send_message,
    send_quick_replies,
)
from app.core.config import settings


class _Res:
    def __init__(self, v):
        self._v = v

    def scalar_one_or_none(self):
        return self._v


class _DB:
    def __init__(self, results=()):
        self._q = list(results)

    async def execute(self, stmt):
        return _Res(self._q.pop(0) if self._q else None)


def _page_account(**kw):
    return SimpleNamespace(
        id=kw.pop("id", uuid.uuid4()),
        team_id=kw.pop("team_id", uuid.uuid4()),
        platform="facebook",
        account_type="page",
        account_id=kw.pop("account_id", "116436681562585"),
        meta_data=kw.pop("meta_data", {"page_token": "EAApagetoken"}),
        **kw,
    )


def _user(email=None):
    return SimpleNamespace(id=uuid.uuid4(), email=email or settings.SOCIAL_ADMIN_EMAIL)


# ── _get_facebook_page_account ───────────────────────────────────────


@pytest.mark.asyncio
async def test_page_account_404_when_missing():
    db = _DB([None])
    with pytest.raises(HTTPException) as e:
        await _get_facebook_page_account(db, uuid.uuid4(), _user())
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_page_account_400_wrong_platform():
    acc = _page_account()
    acc.platform = "instagram"
    db = _DB([acc])
    with pytest.raises(HTTPException) as e:
        await _get_facebook_page_account(db, uuid.uuid4(), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_page_account_400_personal_profile():
    acc = _page_account()
    acc.account_type = "personal"
    db = _DB([acc])
    with pytest.raises(HTTPException) as e:
        await _get_facebook_page_account(db, uuid.uuid4(), _user())
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_page_account_admin_bypasses_membership_check():
    acc = _page_account()
    db = _DB([acc])  # only one result queued — admin path must not query TeamMember
    out = await _get_facebook_page_account(db, acc.id, _user())
    assert out is acc


@pytest.mark.asyncio
async def test_page_account_non_admin_requires_membership():
    acc = _page_account()
    # account found, then TeamMember lookup -> None -> 403
    db = _DB([acc, None])
    with pytest.raises(HTTPException) as e:
        await _get_facebook_page_account(db, acc.id, _user("member@example.com"))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_page_account_non_admin_member_ok():
    acc = _page_account()
    db = _DB([acc, SimpleNamespace(id=uuid.uuid4())])
    out = await _get_facebook_page_account(db, acc.id, _user("member@example.com"))
    assert out is acc


# ── _get_messenger_client ────────────────────────────────────────────


def test_client_400_without_page_token():
    acc = _page_account(meta_data={})
    with pytest.raises(HTTPException) as e:
        _get_messenger_client(acc)
    assert e.value.status_code == 400


def test_client_plaintext_token_used_directly():
    acc = _page_account(meta_data={"page_token": "EAAplain"})
    client = _get_messenger_client(acc)
    assert client.page_id == acc.account_id


def test_client_encrypted_token_decrypted(monkeypatch):
    seen = {}

    def _dec(raw):
        seen["raw"] = raw
        return "EAAdec"

    monkeypatch.setattr(messenger_api, "decrypt_token", _dec)
    acc = _page_account(meta_data={"page_token": "enc-blob-not-ea"})
    client = _get_messenger_client(acc)
    assert seen["raw"] == "enc-blob-not-ea"
    assert client.access_token == "EAAdec"


def test_client_decrypt_failure_falls_back_to_plaintext(monkeypatch):
    monkeypatch.setattr(
        messenger_api, "decrypt_token",
        lambda raw: (_ for _ in ()).throw(ValueError("bad key")),
    )
    acc = _page_account(meta_data={"page_token": "weird-token"})
    client = _get_messenger_client(acc)  # must not raise
    assert client.page_id == acc.account_id


# ── send_message / send_quick_replies ────────────────────────────────


@pytest.fixture
def _patched(monkeypatch):
    acc = _page_account()
    client = SimpleNamespace(
        send_text=AsyncMock(return_value={"message_id": "m1"}),
        send_image_url=AsyncMock(return_value={"message_id": "m2"}),
        send_quick_replies=AsyncMock(return_value={"message_id": "m3"}),
    )
    monkeypatch.setattr(messenger_api, "_get_facebook_page_account", AsyncMock(return_value=acc))
    monkeypatch.setattr(messenger_api, "_get_messenger_client", lambda a: client)
    return client


@pytest.mark.asyncio
async def test_send_message_text(_patched):
    out = await send_message(
        uuid.uuid4(),
        SendMessageRequest(recipient_psid="psid1", text="hello"),
        db=None, user=None,
    )
    _patched.send_text.assert_awaited_once_with("psid1", "hello", messaging_type="RESPONSE")
    assert out["message_id"] == "m1"


@pytest.mark.asyncio
async def test_send_message_image_only(_patched):
    out = await send_message(
        uuid.uuid4(),
        SendMessageRequest(recipient_psid="psid1", image_url="https://x/img.png"),
        db=None, user=None,
    )
    _patched.send_image_url.assert_awaited_once_with("psid1", "https://x/img.png")
    _patched.send_text.assert_not_awaited()
    assert out["message_id"] == "m2"


@pytest.mark.asyncio
async def test_send_message_requires_text_or_image(_patched):
    with pytest.raises(HTTPException) as e:
        await send_message(
            uuid.uuid4(),
            SendMessageRequest(recipient_psid="psid1"),
            db=None, user=None,
        )
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_send_message_client_error_502(_patched):
    _patched.send_text.side_effect = RuntimeError("graph down")
    with pytest.raises(HTTPException) as e:
        await send_message(
            uuid.uuid4(),
            SendMessageRequest(recipient_psid="psid1", text="hi"),
            db=None, user=None,
        )
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_send_quick_replies(_patched):
    qr = [{"content_type": "text", "title": "Yes", "payload": "Y"}]
    out = await send_quick_replies(
        uuid.uuid4(),
        SendQuickRepliesRequest(recipient_psid="psid9", text="pick", quick_replies=qr),
        db=None, user=None,
    )
    _patched.send_quick_replies.assert_awaited_once_with("psid9", "pick", qr)
    assert out["message_id"] == "m3"
