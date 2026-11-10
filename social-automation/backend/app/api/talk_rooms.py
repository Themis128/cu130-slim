"""Consultation Talk-room provisioning — booking-time endpoint.

cloudless.gr calls this BEFORE creating the Cal.com booking so the guest
join link can go into the booking notes/response and the confirmation UI.
The Cal.com webhook calls the same idempotent service path afterwards —
the ``talk_room:`` Lead marker dedupes, so no second room is created.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.models.user import User
from app.services import meeting_notes

logger = logging.getLogger(__name__)
router = APIRouter()


class ConsultRoomRequest(BaseModel):
    name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=320)
    start: str = Field(default="", max_length=40)


@router.post("/consult-room")
async def consult_room(body: ConsultRoomRequest, _user: User = Depends(get_current_user)) -> dict:
    """Provision (or reuse) the Talk room for a consultation booking.

    Best-effort by design: returns ``{room_url: null}`` when Talk/EspoCRM
    isn't configured so the booking flow can proceed with the calendar
    link only.
    """
    if not (body.name or body.email):
        raise HTTPException(status_code=422, detail="name or email required")
    out = await meeting_notes.provision_consult_room(
        name=body.name.strip(),
        email=body.email.strip().lower(),
        start=body.start.strip(),
    )
    if not out:
        return {"room_url": None, "token": None, "reused": False}
    return {"room_url": out.get("url"), "token": out.get("token"), "reused": bool(out.get("reused"))}
