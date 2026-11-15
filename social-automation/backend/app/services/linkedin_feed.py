"""LinkedIn Community Management feed — read posts + social actions via API.

Uses the ``*_social_feed`` scopes granted on app 264925843 (verified live
2026-11-15): ``r_organization_social_feed`` / ``w_organization_social_feed``
for page-actor actions and ``w_member_social_feed`` for member-actor actions.

This replaces the fragile browser-sidecar engagement lane for anything the
Community Management API covers: listing an author's recent posts, reading
social-metadata counts, reacting, and commenting — all with real API tokens
instead of DOM scraping.

Writes are gated: ``LINKEDIN_FEED_WRITES=1`` enables real reactions/comments;
anything else runs dry (returns ``{"dry_run": True, ...}`` without calling
LinkedIn). Reads need only a valid token.
"""

from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.worker._async import task_session

logger = logging.getLogger(__name__)

REST = "https://api.linkedin.com/rest"
HDRS = {"Linkedin-Version": "202610", "X-Restli-Protocol-Version": "2.0.0"}

WRITES_ENABLED = os.getenv("LINKEDIN_FEED_WRITES", "0") == "1"

REACTION_TYPES = {"LIKE", "PRAISE", "EMPATHY", "INTEREST", "APPRECIATION", "ENTERTAINMENT"}


def _enc(urn: str) -> str:
    return quote(urn, safe="")


async def _account_ctx(account_type: str) -> tuple[str, str] | None:
    """(access_token, actor_urn) for a linkedin social_accounts row."""
    async with task_session() as db:
        acc = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.account_type == account_type,
                    )
                )
            )
            .scalars()
            .first()
        )
        if not acc or not acc.access_token_enc:
            return None
        token = decrypt_token(bytes(acc.access_token_enc))
        kind = "organization" if account_type == "organization" else "person"
        return token, f"urn:li:{kind}:{acc.account_id}"


async def member_ctx() -> tuple[str, str] | None:
    return await _account_ctx("person")


async def org_ctx() -> tuple[str, str] | None:
    return await _account_ctx("organization")


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30)


# ── Reads ────────────────────────────────────────────────────────────────


async def list_posts(author_urn: str, count: int = 10) -> dict[str, Any]:
    """Recent posts by an author URN (org page or person).

    ``GET /rest/posts?author={urn}&count=N`` — requires
    ``r_organization_social``/``r_organization_social_feed`` for org authors
    and ``r_member_social``/``r_member_social_feed`` for person authors.
    The token must match the author kind — LinkedIn 400s a member-author
    query made with an org token ("Member permissions must be used").
    """
    if author_urn.startswith("urn:li:organization:"):
        ctx = await org_ctx()
    else:
        ctx = await member_ctx()
    if not ctx:
        return {"posts": [], "error": "no-linkedin-account"}
    token, _ = ctx
    async with await _client() as c:
        r = await c.get(
            f"{REST}/posts",
            params={"author": author_urn, "count": min(count, 50)},
            headers={"Authorization": f"Bearer {token}", **HDRS},
        )
    if r.status_code != 200:
        return {"posts": [], "status": r.status_code, "error": r.text[:200]}
    return {"posts": r.json().get("elements") or [], "status": 200}


async def social_metadata(urns: list[str]) -> dict[str, Any]:
    """Comment/reaction counts for post URNs — ``GET /rest/socialMetadata``."""
    if not urns:
        return {"metadata": {}}
    ctx = await org_ctx()
    if not ctx:
        return {"metadata": {}, "error": "no-linkedin-account"}
    token, _ = ctx
    ids = "List(" + ",".join(urns[:50]) + ")"
    async with await _client() as c:
        r = await c.get(
            f"{REST}/socialMetadata",
            params={"ids": ids},
            headers={"Authorization": f"Bearer {token}", **HDRS},
        )
    if r.status_code != 200:
        return {"metadata": {}, "status": r.status_code, "error": r.text[:200]}
    results = (r.json() or {}).get("results") or {}
    return {"metadata": results, "status": 200}


async def list_comments(object_urn: str, count: int = 10) -> dict[str, Any]:
    """Comments on a share/post URN — ``GET /rest/socialActions/{urn}/comments``."""
    ctx = await org_ctx()
    if not ctx:
        return {"comments": [], "error": "no-linkedin-account"}
    token, _ = ctx
    async with await _client() as c:
        r = await c.get(
            f"{REST}/socialActions/{_enc(object_urn)}/comments",
            params={"count": min(count, 50)},
            headers={"Authorization": f"Bearer {token}", **HDRS},
        )
    if r.status_code != 200:
        return {"comments": [], "status": r.status_code, "error": r.text[:200]}
    return {"comments": r.json().get("elements") or [], "status": 200}


# ── Writes (gated by LINKEDIN_FEED_WRITES) ───────────────────────────────


async def _write_ctx(actor: str) -> tuple[str, str] | None:
    """Resolve (token, actor_urn) for 'member' or 'organization'."""
    if actor == "organization":
        return await org_ctx()
    return await member_ctx()


async def has_reaction(actor_urn: str, entity_urn: str) -> bool | None:
    """True if actor already reacted to entity; None when the check fails."""
    ctx = await org_ctx()
    if not ctx:
        return None
    token, _ = ctx
    key = f"(actor:{_enc(actor_urn)},entity:{_enc(entity_urn)})"
    async with await _client() as c:
        r = await c.get(
            f"{REST}/reactions/{key}",
            headers={"Authorization": f"Bearer {token}", **HDRS},
        )
    if r.status_code == 200:
        return True
    if r.status_code == 404:
        return False
    logger.warning("linkedin has_reaction %s → %s", key, r.status_code)
    return None


async def react(actor: str, root_urn: str, reaction_type: str = "LIKE") -> dict[str, Any]:
    """Create a reaction on a share/post/comment.

    ``actor`` is ``"member"`` or ``"organization"`` — picks the token + actor
    URN. ``root`` is the unencoded share/activity/ugcPost URN.
    """
    if reaction_type not in REACTION_TYPES:
        return {"error": f"bad reactionType {reaction_type!r}", "allowed": sorted(REACTION_TYPES)}
    ctx = await _write_ctx(actor)
    if not ctx:
        return {"error": "no-linkedin-account"}
    token, actor_urn = ctx
    if not WRITES_ENABLED:
        return {"dry_run": True, "actor": actor_urn, "root": root_urn, "reactionType": reaction_type}
    async with await _client() as c:
        r = await c.post(
            f"{REST}/reactions",
            params={"actor": actor_urn},
            json={"root": root_urn, "reactionType": reaction_type},
            headers={"Authorization": f"Bearer {token}", **HDRS, "Content-Type": "application/json"},
        )
    out: dict[str, Any] = {"status": r.status_code, "ok": r.status_code in (200, 201)}
    if r.headers.get("x-restli-id"):
        out["id"] = r.headers["x-restli-id"]
    if not out["ok"]:
        out["error"] = r.text[:300]
    return out


async def comment(actor: str, object_urn: str, text: str, parent_comment: str | None = None) -> dict[str, Any]:
    """Comment on a share/post (or reply via ``parent_comment``).

    ``POST /rest/socialActions/{objectUrn}/comments`` — actor is the
    member or organization URN; ``object`` repeats the share URN.
    """
    text = (text or "").strip()
    if not text:
        return {"error": "empty comment text"}
    if len(text) > 1250:
        return {"error": "comment exceeds 1250 chars"}
    ctx = await _write_ctx(actor)
    if not ctx:
        return {"error": "no-linkedin-account"}
    token, actor_urn = ctx
    if not WRITES_ENABLED:
        return {"dry_run": True, "actor": actor_urn, "object": object_urn, "text": text[:80] + "…"}
    payload: dict[str, Any] = {
        "actor": actor_urn,
        "object": object_urn,
        "message": {"text": text},
    }
    if parent_comment:
        payload["parentComment"] = parent_comment
    async with await _client() as c:
        r = await c.post(
            f"{REST}/socialActions/{_enc(object_urn)}/comments",
            json=payload,
            headers={"Authorization": f"Bearer {token}", **HDRS, "Content-Type": "application/json"},
        )
    out: dict[str, Any] = {"status": r.status_code, "ok": r.status_code in (200, 201)}
    if r.headers.get("x-restli-id"):
        out["id"] = r.headers["x-restli-id"]
    try:
        body = r.json()
        if body.get("commentUrn"):
            out["commentUrn"] = body["commentUrn"]
    except ValueError:
        pass
    if not out["ok"]:
        out["error"] = r.text[:300]
    return out


async def delete_reaction(actor_urn: str, entity_urn: str) -> dict[str, Any]:
    """Remove a reaction — ``DELETE /rest/reactions/(actor:…,entity:…)``."""
    ctx = await org_ctx()
    if not ctx:
        return {"error": "no-linkedin-account"}
    token, _ = ctx
    key = f"(actor:{_enc(actor_urn)},entity:{_enc(entity_urn)})"
    async with await _client() as c:
        r = await c.delete(
            f"{REST}/reactions/{key}",
            headers={"Authorization": f"Bearer {token}", **HDRS},
        )
    return {"status": r.status_code, "ok": r.status_code in (200, 204)}
