"""Coach endpoint — Sofia-voice coaching persona on the doctrine corpus."""

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.api.deps import TeamId
from app.models.user import User
from app.services.kakkava import doctrine_context
from app.services.loop_harness import llm_call

logger = logging.getLogger(__name__)
router = APIRouter()

SYSTEM = (
    "You are the Cloudless coach — Sofia Kakkava's Visibility Era voice. "
    "Direct, one point per message, no fluff, real numbers only. "
    "Coach on offer positioning, content, and visibility strategy — "
    "answer from the doctrine context below, then one concrete action."
)


class CoachIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)


class CoachOut(BaseModel):
    reply: str
    doctrine_used: int


@router.post("/chat", response_model=CoachOut)
async def coach_chat(body: CoachIn, team_id: TeamId, current_user: User = Depends(get_current_user)):
    ctx = await doctrine_context(team_id, body.message, n_results=4)
    prompt = f"{ctx}\n\nMember asks: {body.message}" if ctx else body.message
    reply = await llm_call(prompt, system=SYSTEM, expect=lambda t: len(t) > 20, tag="coach")
    return CoachOut(reply=reply or "No answer — coach offline", doctrine_used=ctx.count("\n\n") if ctx else 0)
