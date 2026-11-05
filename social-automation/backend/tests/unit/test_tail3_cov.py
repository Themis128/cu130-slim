"""Tail coverage — accounts.py: _encode_state branches, linkedin org helpers, expired-token arms."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.accounts as A

pytestmark = pytest.mark.asyncio


def test_encode_state_branches(monkeypatch):
    monkeypatch.setattr(A, "sign_oauth_state", lambda p: "signed:" + str(sorted(p.items())))
    tid = uuid.uuid4()
    # all branches: cv + platform set
    out = A._encode_state(tid, code_verifier="cv", platform="li")
    assert "cv" in out and "li" in out
    # platform None → no "p" key
    out2 = A._encode_state(tid)
    assert "cv" not in out2


async def test_get_linkedin_org_account_404(monkeypatch):
    res = SimpleNamespace(scalar_one_or_none=lambda: None)
    db = SimpleNamespace(execute=AsyncMock(return_value=res))
    with pytest.raises(HTTPException) as e:
        await A._get_linkedin_org_account(db, SimpleNamespace(id=uuid.uuid4()), "t1")
    assert e.value.status_code == 404


async def test_get_linkedin_client_no_token():
    acc = SimpleNamespace(access_token_enc=None)
    with pytest.raises(HTTPException) as e:
        A._get_linkedin_client(acc)
    assert e.value.status_code == 400


async def test_test_account_expired_token(monkeypatch):
    acc = SimpleNamespace(status="active", platform="linkedin")
    monkeypatch.setattr(A, "_get_account_or_404", AsyncMock(return_value=acc))
    exc = type("E", (Exception,), {"status_code": 401})()
    monkeypatch.setattr(A, "_platform_client", AsyncMock(side_effect=exc))
    monkeypatch.setattr(A, "log_action", AsyncMock())
    db = SimpleNamespace(commit=AsyncMock())
    out = await A.test_account("a1", "t1", SimpleNamespace(), db)
    assert out["status"] == "expired" and acc.status == "expired"
