"""Unit tests for the OAuth authorize fail-fast guard (client_id presence).

Directly exercises app.api.auth.oauth_authorize to assert that a platform
with an empty client_id returns a clear 400 instead of building a provider
authorization URL like ".../authorize?client_id=&state=..." (LinkedIn refuses
those with "You need to pass the 'client_id' parameter").
"""

import uuid

import pytest
from fastapi import HTTPException

import app.api.auth as auth


@pytest.mark.asyncio
async def test_oauth_authorize_refuses_empty_linkedin_client_id(monkeypatch):
    """Empty LINKEDIN_CLIENT_ID -> 400 with actionable detail, no broken URL."""
    monkeypatch.setattr(auth.linkedin_client, "client_id", "")

    with pytest.raises(HTTPException) as exc_info:
        await auth.oauth_authorize("linkedin", team_id=uuid.uuid4(), current_user=None)

    assert exc_info.value.status_code == 400
    assert "LINKEDIN_CLIENT_ID" in exc_info.value.detail


@pytest.mark.asyncio
async def test_oauth_authorize_refuses_empty_tiktok_client_key(monkeypatch):
    """TikTok uses TIKTOK_CLIENT_KEY as the client_id -> error names that var."""
    monkeypatch.setattr(auth.tiktok_client, "client_id", "")

    with pytest.raises(HTTPException) as exc_info:
        await auth.oauth_authorize("tiktok", team_id=uuid.uuid4(), current_user=None)

    assert exc_info.value.status_code == 400
    assert "TIKTOK_CLIENT_KEY" in exc_info.value.detail
