"""Tail coverage — auth.py OIDC endpoints."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import app.api.auth as A

pytestmark = pytest.mark.asyncio


async def test_oidc_authorize_returns_url(monkeypatch):
    monkeypatch.setattr(A, "AUTHENTIK_CLIENT_ID", "cid")
    out = await A.oidc_authorize()
    assert "authorize" in out["url"] and "cid" in out["url"]


async def test_oidc_callback_new_user(monkeypatch):
    tok_resp = SimpleNamespace(status_code=200, json=lambda: {"access_token": "at"})
    ui_resp = SimpleNamespace(json=lambda: {"email": "New@X.com"})
    inst = SimpleNamespace(post=AsyncMock(return_value=tok_resp), get=AsyncMock(return_value=ui_resp))
    cls = MagicMock()
    cls.return_value.__aenter__.return_value = inst
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: cls(**kw))
    res = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(execute=AsyncMock(return_value=res), add=lambda u: None, commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(A, "hash_password", lambda p: "h")
    monkeypatch.setattr(A, "create_access_token", lambda p: "jwt")
    out = await A.oidc_callback("code", db)
    assert out["access_token"] == "jwt" and out["email"] == "new@x.com"


async def test_oidc_callback_no_email_400(monkeypatch):
    tok_resp = SimpleNamespace(status_code=200, json=lambda: {"access_token": "at"})
    ui_resp = SimpleNamespace(json=lambda: {"email": ""})
    inst = SimpleNamespace(post=AsyncMock(return_value=tok_resp), get=AsyncMock(return_value=ui_resp))
    cls = MagicMock()
    cls.return_value.__aenter__.return_value = inst
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: cls(**kw))
    db = SimpleNamespace(execute=AsyncMock())
    with pytest.raises(HTTPException) as e:
        await A.oidc_callback("code", db)
    assert e.value.status_code == 400
