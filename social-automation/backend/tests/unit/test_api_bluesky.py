"""Unit tests for app/api/bluesky.py — connect + status endpoints."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

import app.api.bluesky as B


class _DB:
    def __init__(self, scalar=None):
        self._scalar = scalar
        self.added = []
        self.commits = 0

    async def execute(self, *a, **kw):
        r = Mock()
        r.scalar_one_or_none = Mock(return_value=self._scalar)
        return r

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        obj.id = obj.id or uuid.uuid4()


def _acct(**kw):
    base = dict(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        platform="bluesky",
        account_id="did:plc:abc",
        username="cloudless.bsky.social",
        display_name="cloudless.bsky.social",
        access_token_enc=b"enc",
        meta_data={"pds_url": "https://bsky.social"},
        status="active",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ── _get_bluesky_account / _client_for ────────────────────────────────


@pytest.mark.asyncio
async def test_get_account_404():
    with pytest.raises(HTTPException) as ei:
        await B._get_bluesky_account(uuid.uuid4(), uuid.uuid4(), _DB(scalar=None))
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_get_account_and_client_for(monkeypatch):
    acct = _acct(access_token_enc="plain-str-enc")
    assert await B._get_bluesky_account(acct.id, acct.team_id, _DB(scalar=acct)) is acct

    monkeypatch.setattr(B, "decrypt_token", lambda b: "app-pass")
    captured = {}

    class _C:
        def __init__(self, handle, pw, pds_url=None):
            captured.update(handle=handle, pw=pw, pds_url=pds_url)

    monkeypatch.setattr(B, "BlueskyClient", _C)
    B._client_for(acct)
    assert captured == {
        "handle": "cloudless.bsky.social",
        "pw": "app-pass",
        "pds_url": "https://bsky.social",
    }
    # no pds in meta → default
    acct.meta_data = {}
    B._client_for(acct)
    assert captured["pds_url"] == B.BSKY_PDS_DEFAULT


# ── connect ───────────────────────────────────────────────────────────


def _connect_body(**kw):
    base = dict(handle=" @cloudless.bsky.social ", app_password=" abcd-1234 ", pds_url=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_connect_new_account(monkeypatch):
    monkeypatch.setattr(B, "check_quota", AsyncMock())
    monkeypatch.setattr(B, "encrypt_token", lambda s: b"ENC")

    class _C:
        pds_url = "https://bsky.social"

        def __init__(self, h, p, pds_url=None):
            self.h, self.p = h, p

        async def create_session(self):
            return {"did": "did:plc:xyz", "handle": "cloudless.bsky.social"}

        async def get_profile(self):
            return {"followersCount": 5, "postsCount": 3}

    monkeypatch.setattr(B, "BlueskyClient", _C)
    db = _DB(scalar=None)
    out = await B.connect_bluesky_account(_connect_body(), uuid.uuid4(), db, Mock())
    assert out["status"] == "ok" and out["did"] == "did:plc:xyz"
    assert out["followers"] == 5
    assert db.added and db.commits == 1
    assert db.added[0].platform == "bluesky"


@pytest.mark.asyncio
async def test_connect_existing_account_updates(monkeypatch):
    monkeypatch.setattr(B, "check_quota", AsyncMock())
    monkeypatch.setattr(B, "encrypt_token", lambda s: b"ENC")
    flag = Mock()
    monkeypatch.setattr(B, "flag_modified", flag)

    class _C:
        pds_url = "https://bsky.social"

        def __init__(self, *a, **kw):
            pass

        async def create_session(self):
            return {"did": "did:plc:abc"}

        async def get_profile(self):
            return {}

    monkeypatch.setattr(B, "BlueskyClient", _C)
    existing = _acct(meta_data={"old": True})
    db = _DB(scalar=existing)
    out = await B.connect_bluesky_account(_connect_body(), existing.team_id, db, Mock())
    assert out["did"] == "did:plc:abc"
    assert existing.access_token_enc == b"ENC"
    assert existing.meta_data["old"] is True and existing.meta_data["did"] == "did:plc:abc"
    flag.assert_called_once()


@pytest.mark.asyncio
async def test_connect_errors(monkeypatch):
    monkeypatch.setattr(B, "check_quota", AsyncMock())

    class _Bad:
        def __init__(self, *a, **kw):
            raise ValueError("bad handle")

    monkeypatch.setattr(B, "BlueskyClient", _Bad)
    with pytest.raises(HTTPException) as ei:
        await B.connect_bluesky_account(_connect_body(), uuid.uuid4(), _DB(), Mock())
    assert ei.value.status_code == 400 and "bad handle" in ei.value.detail

    class _LoginFail:
        def __init__(self, *a, **kw):
            pass

        async def create_session(self):
            raise B.BlueskyAPIError(401, "Unauthorized", "u")

    monkeypatch.setattr(B, "BlueskyClient", _LoginFail)
    with pytest.raises(HTTPException) as ei:
        await B.connect_bluesky_account(_connect_body(), uuid.uuid4(), _DB(), Mock())
    assert ei.value.status_code == 400 and "401" in ei.value.detail


# ── status ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_ok_and_error(monkeypatch):
    acct = _acct()
    db = _DB(scalar=acct)
    monkeypatch.setattr(B, "decrypt_token", lambda b: "pw")

    class _C:
        def __init__(self, *a, **kw):
            pass

        async def create_session(self):
            return {}

        async def get_profile(self):
            return {"handle": "h", "did": "d", "followersCount": 1, "followsCount": 2, "postsCount": 3}

    monkeypatch.setattr(B, "BlueskyClient", _C)
    out = await B.bluesky_account_status(acct.id, acct.team_id, db, Mock())
    assert out["status"] == "ok" and out["followers"] == 1

    class _Fail(_C):
        async def create_session(self):
            raise B.BlueskyAPIError(403, "Forbidden", "u")

    monkeypatch.setattr(B, "BlueskyClient", _Fail)
    out = await B.bluesky_account_status(acct.id, acct.team_id, db, Mock())
    assert out["status"] == "error" and "403" in out["error"]
