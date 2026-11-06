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
        await A._get_linkedin_org_account(uuid.uuid4(), SimpleNamespace(id=uuid.uuid4()), db)
    assert e.value.status_code == 404


async def test_get_linkedin_client_no_token():
    acc = SimpleNamespace(access_token_enc=None)
    with pytest.raises(HTTPException) as e:
        A._get_linkedin_client(acc)
    assert e.value.status_code == 400


async def test_test_account_expired_token(monkeypatch):
    from app.services.linkedin_api import LinkedInAPIError

    acc = SimpleNamespace(
        id=uuid.uuid4(),
        status="active",
        platform="linkedin",
        access_token_enc=b"x",
        account_id="org1",
        meta_data={},
    )
    res = SimpleNamespace(scalar_one_or_none=lambda: acc)
    db = SimpleNamespace(execute=AsyncMock(return_value=res), commit=AsyncMock())
    monkeypatch.setattr(A, "decrypt_token", lambda t: "tok")

    class _Client:
        def __init__(self, access_token=None, **kw):
            pass

        async def validate_token(self):
            raise LinkedInAPIError(401, "unauthorized", "https://api.linkedin.com/x")

    import app.services.linkedin_api as LA

    monkeypatch.setattr(LA, "LinkedInAPIClient", _Client)
    out = await A.test_account(acc.id, SimpleNamespace(id=uuid.uuid4()), db)
    assert out["status"] == "expired" and acc.status == "expired"
