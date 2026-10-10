"""Bluesky (AT Protocol) account endpoints.

Accounts use ``platform="bluesky"`` with the **app password** stored
encrypted in ``access_token_enc`` (app passwords are the AT Protocol
credential for server-side automation — OAuth DPoP is unnecessary for a
single-user PDS flow). ``account_id`` holds the DID, ``username`` the
handle, and ``meta_data.pds_url`` the PDS (defaults to bsky.social).

- ``POST /connect`` — validate handle+app password via createSession,
  persist account
- ``GET /{account_id}/status`` — live session + profile check
- ``DELETE /{account_id}`` handled by the generic accounts router
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.api.auth import get_current_user
from app.api.deps import TeamId, check_quota
from app.core.security import decrypt_token, encrypt_token
from app.db.session import get_db
from app.models.social_account import SocialAccount
from app.models.user import User
from app.services.bluesky_api import BSKY_PDS_DEFAULT, BlueskyAPIError, BlueskyClient

router = APIRouter()


class BlueskyConnectRequest(BaseModel):
    handle: str = Field(..., description="Bluesky handle, e.g. cloudless.gr or user.bsky.social")
    app_password: str = Field(..., description="Bluesky app password (Settings → App Passwords)")
    pds_url: str = Field(BSKY_PDS_DEFAULT, description="PDS base URL (self-hosted PDS supported)")


async def _get_bluesky_account(
    account_id: uuid.UUID,
    team_id: uuid.UUID,
    db: AsyncSession,
) -> SocialAccount:
    result = await db.execute(
        select(SocialAccount).where(
            SocialAccount.id == account_id,
            SocialAccount.team_id == team_id,
            SocialAccount.platform == "bluesky",
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Bluesky account not found")
    return account


def _client_for(account: SocialAccount) -> BlueskyClient:
    enc = account.access_token_enc
    app_password = decrypt_token(enc if isinstance(enc, bytes) else enc.encode())
    pds = (account.meta_data or {}).get("pds_url") or BSKY_PDS_DEFAULT
    return BlueskyClient(account.username or "", app_password, pds_url=pds)


@router.post("/connect", response_model=dict)
async def connect_bluesky_account(
    body: BlueskyConnectRequest,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Create a Bluesky account from handle + app password."""
    await check_quota("social_accounts", team_id, db)
    try:
        client = BlueskyClient(body.handle.strip(), body.app_password.strip(), pds_url=body.pds_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        session = await client.create_session()
    except BlueskyAPIError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Bluesky login failed ({exc.status_code}): {exc.response_text[:200]}",
        ) from exc

    did = session["did"]
    handle = session.get("handle") or body.handle.strip()

    existing = await db.execute(
        select(SocialAccount).where(
            SocialAccount.team_id == team_id,
            SocialAccount.platform == "bluesky",
            SocialAccount.account_id == did,
        )
    )
    account = existing.scalar_one_or_none()
    enc = encrypt_token(body.app_password.strip())
    enc_b = enc if isinstance(enc, bytes) else enc.encode()
    meta: dict[str, Any] = {
        "pds_url": client.pds_url,
        "did": did,
        "credentials_configured": True,
        "auth_type": "app_password",
    }

    if account:
        account.access_token_enc = enc_b
        account.username = handle
        account.display_name = handle
        account.status = "active"
        account.meta_data = {**(account.meta_data or {}), **meta}
        flag_modified(account, "meta_data")
    else:
        account = SocialAccount(
            team_id=team_id,
            platform="bluesky",
            account_id=did,
            username=handle,
            display_name=handle,
            account_type="user",
            access_token_enc=enc_b,
            scopes=["atproto"],
            status="active",
            meta_data=meta,
        )
        db.add(account)

    await db.commit()
    await db.refresh(account)

    profile = await client.get_profile()
    return {
        "status": "ok",
        "account_id": str(account.id),
        "did": did,
        "handle": handle,
        "followers": profile.get("followersCount"),
        "posts": profile.get("postsCount"),
    }


@router.get("/{account_id}/status", response_model=dict)
async def bluesky_account_status(
    account_id: uuid.UUID,
    team_id: TeamId,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Live session + profile check for a connected Bluesky account."""
    account = await _get_bluesky_account(account_id, team_id, db)
    client = _client_for(account)
    try:
        await client.create_session()
        profile = await client.get_profile()
    except BlueskyAPIError as exc:
        return {
            "status": "error",
            "account_id": str(account.id),
            "handle": account.username,
            "error": f"{exc.status_code}: {exc.response_text[:200]}",
        }
    return {
        "status": "ok",
        "account_id": str(account.id),
        "handle": profile.get("handle"),
        "did": profile.get("did"),
        "followers": profile.get("followersCount"),
        "follows": profile.get("followsCount"),
        "posts": profile.get("postsCount"),
    }
