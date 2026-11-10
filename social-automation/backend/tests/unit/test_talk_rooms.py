"""Coverage for the /api/v1/talk/consult-room endpoint."""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.api.talk_rooms as TR
import app.services.meeting_notes as MN


@pytest.mark.asyncio
async def test_consult_room_requires_name_or_email():
    with pytest.raises(HTTPException) as ei:
        await TR.consult_room(TR.ConsultRoomRequest(), _user=None)
    assert ei.value.status_code == 422


@pytest.mark.asyncio
async def test_consult_room_not_configured_returns_null(monkeypatch):
    monkeypatch.setattr(MN, "provision_consult_room", AsyncMock(return_value=None))
    out = await TR.consult_room(TR.ConsultRoomRequest(name="Jane"), _user=None)
    assert out == {"room_url": None, "token": None, "reused": False}


@pytest.mark.asyncio
async def test_consult_room_new_room(monkeypatch):
    seen = {}

    async def _prov(*, name, email, start):
        seen.update(name=name, email=email, start=start)
        return {"token": "abc12345", "url": "https://x/call/abc12345", "reused": False}

    monkeypatch.setattr(MN, "provision_consult_room", _prov)
    out = await TR.consult_room(
        TR.ConsultRoomRequest(name="  Jane Doe ", email="  JANE@ACME.COM ", start=" 2026-11-15 "),
        _user=None,
    )
    assert out == {"room_url": "https://x/call/abc12345", "token": "abc12345", "reused": False}
    assert seen == {"name": "Jane Doe", "email": "jane@acme.com", "start": "2026-11-15"}


@pytest.mark.asyncio
async def test_consult_room_reused_room(monkeypatch):
    monkeypatch.setattr(
        MN,
        "provision_consult_room",
        AsyncMock(return_value={"token": "abc12345", "url": "https://x/call/abc12345", "reused": True}),
    )
    out = await TR.consult_room(TR.ConsultRoomRequest(email="jane@acme.com"), _user=None)
    assert out["reused"] is True
    assert out["room_url"] == "https://x/call/abc12345"
