"""Coverage gaps: bluesky_api refresh/session/app-password paths."""

from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.services import bluesky_api as api


def _jwt(exp_offset=3600):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + exp_offset}).encode()).decode().rstrip("=")
    return f"h.{payload}.s"


class _Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body
        self.text = str(body)

    def json(self):
        return self._body


class _Client:
    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.calls.append({"url": url, **kw})
        return self._responses.pop(0)


@pytest.fixture
def client():
    return api.BlueskyClient("test.bsky.social", "xxxx-yyyy-zzzz-wwww")


# ── _jwt_exp ─────────────────────────────────────────────────────────────


def test_jwt_exp_valid_and_invalid(client):
    exp = api.BlueskyClient._jwt_exp(_jwt(100))
    assert exp is not None and exp > time.time()
    assert api.BlueskyClient._jwt_exp("not-a-jwt") is None
    assert api.BlueskyClient._jwt_exp("a.!!!.c") is None
    assert api.BlueskyClient._jwt_exp("a." + base64.urlsafe_b64encode(b"{}").decode().rstrip("=") + ".c") is None


# ── refresh_session ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_session_no_refresh_jwt(client):
    with pytest.raises(api.BlueskyAPIError) as ei:
        await client.refresh_session()
    assert ei.value.status_code == 0


@pytest.mark.asyncio
async def test_refresh_session_non200_and_success(client):
    client._refresh_jwt = "ref"
    fake = _Client(_Resp(401, {"error": "ExpiredToken"}))
    with patch("app.services.bluesky_api.httpx.AsyncClient", return_value=fake):
        with pytest.raises(api.BlueskyAPIError) as ei:
            await client.refresh_session()
    assert ei.value.status_code == 401

    fake2 = _Client(_Resp(200, {"accessJwt": "new-acc", "refreshJwt": "new-ref", "did": "did:plc:new"}))
    with patch("app.services.bluesky_api.httpx.AsyncClient", return_value=fake2):
        out = await client.refresh_session()
    assert out["accessJwt"] == "new-acc"
    assert client._access_jwt == "new-acc"
    assert client._refresh_jwt == "new-ref"
    assert client.did == "did:plc:new"


# ── create_app_password error + revoke error ─────────────────────────────


@pytest.mark.asyncio
async def test_create_app_password_non200(client):
    client._access_jwt = "jwt"
    fake = _Client(_Resp(403, {"error": "no"}))
    with patch("httpx.AsyncClient", return_value=fake):
        with pytest.raises(api.BlueskyAPIError):
            await client.create_app_password()


@pytest.mark.asyncio
async def test_revoke_app_password_non200(client):
    client._access_jwt = "jwt"
    fake = _Client(_Resp(500, "err"))
    with patch("httpx.AsyncClient", return_value=fake):
        with pytest.raises(api.BlueskyAPIError):
            await client.revoke_app_password("x")


# ── ensure_session branches ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_session_reuses_unexpired_access(client):
    out = await api.ensure_session(client, {"access_jwt": _jwt(3600), "refresh_jwt": "r", "did": "d"})
    assert out == {"reused": True}
    assert client._access_jwt is not None


@pytest.mark.asyncio
async def test_ensure_session_expired_access_falls_to_refresh(client):
    async def fake_refresh():
        client._access_jwt = "fresh"
        return {"accessJwt": "fresh"}

    client.refresh_session = fake_refresh
    client.set_session = api.BlueskyClient.set_session.__get__(client)
    out = await api.ensure_session(client, {"access_jwt": _jwt(-10), "refresh_jwt": "r"})
    assert out == {"accessJwt": "fresh"}


@pytest.mark.asyncio
async def test_ensure_session_refresh_fails_falls_to_create(client):
    async def fake_refresh():
        raise api.BlueskyAPIError(401, "expired", "u")

    client.refresh_session = fake_refresh
    client.set_session = api.BlueskyClient.set_session.__get__(client)

    async def fake_create():
        return {"accessJwt": "brand-new"}

    client.create_session = fake_create
    out = await api.ensure_session(client, {"access_jwt": _jwt(-10), "refresh_jwt": "r"})
    assert out == {"accessJwt": "brand-new"}


@pytest.mark.asyncio
async def test_ensure_session_no_stored_goes_create(client):
    async def fake_create():
        return {"accessJwt": "x"}

    client.create_session = fake_create
    assert await api.ensure_session(client, None) == {"accessJwt": "x"}
    assert await api.ensure_session(client, {}) == {"accessJwt": "x"}


# ── persist_session ──────────────────────────────────────────────────────


def test_persist_session_no_session_state_and_normal(monkeypatch):
    import sqlalchemy.orm.attributes as _sa

    monkeypatch.setattr(_sa, "flag_modified", lambda *a, **k: None)

    class _NoState:
        pass

    acc = SimpleNamespace(meta_data={})
    api.persist_session(acc, _NoState())  # early return

    class _WithState:
        def session_state(self):
            return {"access_jwt": "a"}

    api.persist_session(acc, _WithState())
    assert acc.meta_data["bsky_session"] == {"access_jwt": "a"}


# ── remaining arcs ───────────────────────────────────────────────────────


def test_session_state_snapshot(client):
    client._access_jwt = "a"
    client._refresh_jwt = "r"
    client.did = "did:plc:x"
    st = client.session_state()
    assert st["access_jwt"] == "a" and st["refresh_jwt"] == "r" and st["did"] == "did:plc:x"
    assert "saved_at" in st


def test_backoff_active_future_and_now_param():
    from app.services.bluesky_api import backoff_active, set_429_backoff

    meta = {}
    set_429_backoff(meta, minutes=30)
    assert backoff_active(meta) is True
    assert backoff_active(meta, now=time.time() + 999999) is False


@pytest.mark.asyncio
async def test_rotate_app_password_no_password_raises(monkeypatch):
    from unittest.mock import AsyncMock

    account = SimpleNamespace(meta_data={"pds_url": "https://pds.example"}, username="h", access_token_enc=None)
    db = SimpleNamespace(commit=AsyncMock())

    class _Admin:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def create_session(self):
            return {}

        async def create_app_password(self, name="x"):
            return {"name": "n"}  # no password key

        async def revoke_app_password(self, name):
            pass

    monkeypatch.setattr(api, "BlueskyClient", lambda *a, **k: _Admin())

    with pytest.raises(api.BlueskyAPIError):
        await api.rotate_app_password(account, "mainpw", db)


def test_backoff_active_meta_without_key():
    from app.services.bluesky_api import backoff_active

    assert backoff_active({"unrelated": 1}) is False
