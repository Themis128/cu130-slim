"""Coverage for LinkedIn org-profile write path (PR #1339)."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

import app.api.accounts as A
from app.services.linkedin_api import LinkedInAPIClient


@pytest.mark.asyncio
async def test_update_organization_profile_builds_patch():
    client = LinkedInAPIClient(access_token="tok")
    resp = Mock(status_code=200, json=lambda: {})

    class _C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def patch(self, url, headers=None, json=None):
            self.url, self.body = url, json
            return resp

    c = _C()
    with patch("app.services.linkedin_api.httpx.AsyncClient", return_value=c):
        out = await client.update_organization_profile("108614163", description="d", website="w", tagline="t")
    assert out is True
    assert c.body["patch"]["$set"] == {
        "localizedDescription": "d",
        "localizedWebsiteUrl": "w",
        "tagline": "t",
    }


@pytest.mark.asyncio
async def test_update_organization_profile_no_fields_raises():
    client = LinkedInAPIClient(access_token="tok")
    with pytest.raises(ValueError):
        await client.update_organization_profile("1")


@pytest.mark.asyncio
async def test_get_linkedin_org_account_not_org_404():
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)))
    with pytest.raises(HTTPException) as e:
        await A._get_linkedin_org_account(uuid.uuid4(), SimpleNamespace(id="u"), db)
    assert e.value.status_code == 404


def test_get_linkedin_client_missing_token_400():
    acc = SimpleNamespace(access_token_enc=None)
    with pytest.raises(HTTPException) as e:
        A._get_linkedin_client(acc)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_page_profile_linkedin_branch(monkeypatch):
    res = SimpleNamespace(scalar_one_or_none=lambda: "linkedin")
    db = SimpleNamespace(execute=AsyncMock(return_value=res))
    monkeypatch.setattr(A, "_get_linkedin_org_account", AsyncMock(return_value=SimpleNamespace(account_id="1")))
    monkeypatch.setattr(A, "_get_linkedin_client", lambda a: SimpleNamespace(get_organization=AsyncMock(return_value={"id": "1"})))
    out = await A.get_facebook_page_profile(uuid.uuid4(), current_user=SimpleNamespace(id="u"), db=db)
    assert out["profile"] == {"id": "1"}


@pytest.mark.asyncio
async def test_page_profile_linkedin_502(monkeypatch):
    res = SimpleNamespace(scalar_one_or_none=lambda: "linkedin")
    db = SimpleNamespace(execute=AsyncMock(return_value=res))
    monkeypatch.setattr(A, "_get_linkedin_org_account", AsyncMock(return_value=SimpleNamespace(account_id="1")))
    client = SimpleNamespace(get_organization=AsyncMock(side_effect=Exception("boom")))
    monkeypatch.setattr(A, "_get_linkedin_client", lambda a: client)
    with pytest.raises(HTTPException) as e:
        await A.get_facebook_page_profile(uuid.uuid4(), current_user=SimpleNamespace(id="u"), db=db)
    assert e.value.status_code == 502


@pytest.mark.asyncio
async def test_update_profile_linkedin_branch(monkeypatch):
    res = SimpleNamespace(scalar_one_or_none=lambda: "linkedin")
    db = SimpleNamespace(execute=AsyncMock(return_value=res))
    monkeypatch.setattr(A, "_get_linkedin_org_account", AsyncMock(return_value=SimpleNamespace(account_id="1")))
    client = SimpleNamespace(update_organization_profile=AsyncMock(return_value=True))
    monkeypatch.setattr(A, "_get_linkedin_client", lambda a: client)
    upd = SimpleNamespace(description="d", website="w", tagline="t", about=None, phone=None)
    out = await A.update_facebook_page_profile(uuid.uuid4(), updates=upd, current_user=SimpleNamespace(id="u"), db=db)
    assert out == {"success": True}


@pytest.mark.asyncio
async def test_update_profile_linkedin_value_error_400(monkeypatch):
    res = SimpleNamespace(scalar_one_or_none=lambda: "linkedin")
    db = SimpleNamespace(execute=AsyncMock(return_value=res))
    monkeypatch.setattr(A, "_get_linkedin_org_account", AsyncMock(return_value=SimpleNamespace(account_id="1")))
    client = SimpleNamespace(update_organization_profile=AsyncMock(side_effect=ValueError("none")))
    monkeypatch.setattr(A, "_get_linkedin_client", lambda a: client)
    upd = SimpleNamespace(description=None, website=None, tagline=None, about=None, phone=None)
    with pytest.raises(HTTPException) as e:
        await A.update_facebook_page_profile(uuid.uuid4(), updates=upd, current_user=SimpleNamespace(id="u"), db=db)
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_route_linkedin_branch_success(monkeypatch):
    """accounts.py 1099-1112: linkedin platform → org profile write → success."""
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: "linkedin")))
    acc = SimpleNamespace(account_id="108614163")
    client = SimpleNamespace(update_organization_profile=AsyncMock(return_value=True))
    monkeypatch.setattr(A, "_get_linkedin_org_account", AsyncMock(return_value=acc))
    monkeypatch.setattr(A, "_get_linkedin_client", Mock(return_value=client))
    upd = SimpleNamespace(description="d", website="w", tagline="t")
    out = await A.update_facebook_page_profile(uuid.uuid4(), upd, SimpleNamespace(id="u"), db)
    assert out == {"success": True}
    client.update_organization_profile.assert_awaited_once_with("108614163", description="d", website="w", tagline="t")


@pytest.mark.asyncio
async def test_route_linkedin_branch_errors(monkeypatch):
    """ValueError→400, generic→502 on the org write."""
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: "linkedin")))
    acc = SimpleNamespace(account_id="1")
    monkeypatch.setattr(A, "_get_linkedin_org_account", AsyncMock(return_value=acc))
    upd = SimpleNamespace(description=None, website=None, tagline=None)
    for exc, code in ((ValueError("bad"), 400), (RuntimeError("boom"), 502)):
        client = SimpleNamespace(update_organization_profile=AsyncMock(side_effect=exc))
        monkeypatch.setattr(A, "_get_linkedin_client", Mock(return_value=client))
        with pytest.raises(HTTPException) as e:
            await A.update_facebook_page_profile(uuid.uuid4(), upd, SimpleNamespace(id="u"), db)
        assert e.value.status_code == code
