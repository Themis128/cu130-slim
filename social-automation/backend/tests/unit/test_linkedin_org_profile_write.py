"""Coverage for LinkedIn org-profile write path (PR #1339)."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi import HTTPException

import app.api.accounts as A
from app.services.linkedin_api import LinkedInAPIClient, LinkedInAPIError


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
        out = await client.update_organization_profile(
            "108614163", description="d", website="w", tagline="t"
        )
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
