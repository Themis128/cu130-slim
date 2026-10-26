"""Threads engagement actor — reply via Graph API (threads_content_publish).

No public like/repost endpoint exists on Threads API; replies are the
highest-value action anyway (5x weight). Like actions degrade to skip.
"""
import logging

import httpx
from sqlalchemy import select

logger = logging.getLogger(__name__)

API = "https://graph.threads.net/v1.0"


async def _token(db):
    from app.core.security import decrypt_token
    from app.models.social_account import SocialAccount
    acct = (await db.execute(select(SocialAccount).where(
        SocialAccount.platform == "threads",
        SocialAccount.status == "active"))).scalars().first()
    if not acct:
        return None, None
    return decrypt_token(acct.access_token_enc), acct.account_id


async def act(action, db) -> dict:
    token, uid = await _token(db)
    if not token:
        return {"error": "no active threads account"}
    if action.action in ("like", "repost"):
        return {"error": "threads api has no like/repost endpoint", "skip": True}
    text = action.comment_draft or ""
    if not text:
        return {"error": "empty comment draft"}
    async with httpx.AsyncClient(timeout=25) as c:
        r = await c.post(f"{API}/{uid}/threads", data={
            "access_token": token, "media_type": "TEXT",
            "text": text, "reply_to_id": action.target_post_id})
        if r.status_code not in (200, 201):
            logger.warning("[threads-actor] create failed %s %s",
                           r.status_code, r.text[:150])
            return {"error": f"{r.status_code}: {r.text[:120]}"}
        container = r.json().get("id")
        pub = await c.post(f"{API}/{uid}/threads_publish",
                           data={"access_token": token,
                                 "creation_id": container})
    if pub.status_code in (200, 201):
        return {"ok": True, "resp": pub.json()}
    return {"error": f"publish {pub.status_code}: {pub.text[:120]}"}
