"""Talk-bridge admin API — room map status + manual sync for the
Nextcloud Talk ↔ business messenger bridge (see
``app.services.talk_bridge`` and docs/talk-messenger-bridge.md)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_team_id, get_current_user
from app.db.session import get_db
from app.models.talk_room import TalkRoom
from app.models.user import User
from app.services.talk_bridge import bridge_enabled, inbound_sync, outbound, sync_rooms

router = APIRouter()


class RoomResponse(BaseModel):
    platform: str
    thread_id: str
    contact_name: str
    room_token: str
    enabled: bool


class SendRequest(BaseModel):
    text: str


@router.get("/status", response_model=dict)
async def talk_bridge_status(
    team_id: uuid.UUID = Depends(get_current_team_id),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    res = await db.execute(select(TalkRoom).where(TalkRoom.team_id == team_id))
    rooms = res.scalars().all()
    by_platform: dict[str, int] = {}
    for r in rooms:
        if r.enabled:
            by_platform[r.platform] = by_platform.get(r.platform, 0) + 1
    return {
        "enabled": bridge_enabled(),
        "rooms": len(rooms),
        "active_rooms": sum(1 for r in rooms if r.enabled),
        "by_platform": by_platform,
    }


@router.get("/rooms", response_model=list[RoomResponse])
async def list_rooms(
    team_id: uuid.UUID = Depends(get_current_team_id),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    res = await db.execute(select(TalkRoom).where(TalkRoom.team_id == team_id))
    return [
        RoomResponse(
            platform=r.platform,
            thread_id=r.thread_id,
            contact_name=r.contact_name,
            room_token=r.room_token,
            enabled=r.enabled,
        )
        for r in res.scalars().all()
    ]


@router.post("/sync", response_model=dict)
async def sync_now(
    team_id: uuid.UUID = Depends(get_current_team_id),
    _user: User = Depends(get_current_user),
):
    """Create rooms for current conversations + post new inbound messages."""
    if not bridge_enabled():
        raise HTTPException(status_code=409, detail="Talk bridge is not enabled (TALK_BRIDGE_ENABLED/NEXTCLOUD_TALK_NOTIFY/BOT_SECRET)")
    rooms = await sync_rooms(team_id)
    inbound = await inbound_sync(team_id)
    return {**rooms, **inbound}


@router.post("/rooms/{room_token}/send", response_model=dict)
async def send_to_thread(
    room_token: str,
    body: SendRequest,
    team_id: uuid.UUID = Depends(get_current_team_id),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Send a message through the bridge (same path as a Talk-room reply).

    Scoped to the caller's team — a room token only routes outbound when a
    TalkRoom row for this team maps it.
    """
    res = await db.execute(select(TalkRoom).where(TalkRoom.team_id == team_id, TalkRoom.room_token == room_token))
    if res.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Room not found")
    ok, detail = await outbound(room_token, body.text)
    if not ok:
        raise HTTPException(status_code=502, detail=detail)
    return {"ok": True}


@router.post("/rooms/{room_token}/toggle", response_model=dict)
async def toggle_room(
    room_token: str,
    team_id: uuid.UUID = Depends(get_current_team_id),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    res = await db.execute(select(TalkRoom).where(TalkRoom.team_id == team_id, TalkRoom.room_token == room_token))
    room = res.scalar_one_or_none()
    if room is None:
        raise HTTPException(status_code=404, detail="Room not found")
    room.enabled = not room.enabled
    await db.commit()
    return {"room_token": room_token, "enabled": room.enabled}
