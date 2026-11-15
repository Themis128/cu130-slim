"""LinkedIn feed ops API — Community Management feed reads + gated writes.

Read endpoints need only a valid LinkedIn token; write endpoints additionally
require ``LINKEDIN_FEED_WRITES=1`` in the service layer (they return
``dry_run`` payloads otherwise) — the API itself stays callable so operators
can preview exactly what the engagement worker would do.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.services import linkedin_feed

router = APIRouter(prefix="/linkedin/feed", tags=["linkedin-feed"])


@router.get("/posts")
async def feed_posts(author: str, count: int = 10, _user=Depends(get_current_user)) -> dict[str, Any]:
    """Recent posts by an author URN (``urn:li:organization:…`` / ``urn:li:person:…``)."""
    if not author.startswith("urn:li:"):
        raise HTTPException(status_code=400, detail="author must be a urn:li: URN")
    return await linkedin_feed.list_posts(author, count=min(count, 50))


@router.get("/metadata")
async def feed_metadata(urns: str, _user=Depends(get_current_user)) -> dict[str, Any]:
    """Social-metadata counts for comma-separated post URNs."""
    items = [u.strip() for u in urns.split(",") if u.strip().startswith("urn:li:")][:50]
    return await linkedin_feed.social_metadata(items)


@router.get("/comments")
async def feed_comments(urn: str, count: int = 10, _user=Depends(get_current_user)) -> dict[str, Any]:
    """Comments on a share/post URN."""
    if not urn.startswith("urn:li:"):
        raise HTTPException(status_code=400, detail="urn must be a urn:li: URN")
    return await linkedin_feed.list_comments(urn, count=min(count, 50))


class ReactionIn(BaseModel):
    actor: str = Field(pattern="^(member|organization)$")
    root: str = Field(min_length=10)
    reaction_type: str = "LIKE"


@router.post("/react")
async def feed_react(body: ReactionIn, _user=Depends(get_current_user)) -> dict[str, Any]:
    """React to a post — dry-run unless LINKEDIN_FEED_WRITES=1."""
    return await linkedin_feed.react(body.actor, body.root, body.reaction_type)


class CommentIn(BaseModel):
    actor: str = Field(pattern="^(member|organization)$")
    object: str = Field(min_length=10)
    text: str = Field(min_length=1, max_length=1250)
    parent_comment: str | None = None


@router.post("/comment")
async def feed_comment(body: CommentIn, _user=Depends(get_current_user)) -> dict[str, Any]:
    """Comment on a post — dry-run unless LINKEDIN_FEED_WRITES=1."""
    return await linkedin_feed.comment(body.actor, body.object, body.text, body.parent_comment)
