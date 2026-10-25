"""X (Twitter) engagement actor — like / retweet / reply via REST v2."""
import logging

import httpx
from sqlalchemy import select

logger = logging.getLogger(__name__)

API = "https://api.twitter.com/2"


async def _token(db):
    from app.models.social_account import SocialAccount
    acct = (await db.execute(select(SocialAccount).where(
        SocialAccount.platform == "twitter",
        SocialAccount.status == "active"))).scalars().first()
    if not acct:
        return None, None
    from app.core.security import decrypt_token
    token = decrypt_token(acct.access_token_enc)
    if not token:
        return None, None
    return token, (acct.meta_data or {}).get("user_id")


async def act(action, db) -> dict:
    """Execute one EngagementAction on X. action.action: like|repost|comment."""
    token, uid = await _token(db)
    if not token:
        return {"error": "no active twitter account"}
    h = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20) as c:
        if action.action == "like":
            r = await c.post(f"{API}/users/{uid}/likes", headers=h,
                             json={"tweet_id": action.target_post_id})
        elif action.action == "repost":
            r = await c.post(f"{API}/users/{uid}/retweets", headers=h,
                             json={"tweet_id": action.target_post_id})
        elif action.action == "comment":
            text = action.comment_draft or ""
            if not text:
                return {"error": "empty comment draft"}
            r = await c.post(f"{API}/tweets", headers=h, json={
                "text": text,
                "reply": {"in_reply_to_tweet_id": action.target_post_id}})
        else:
            return {"error": f"unknown action {action.action}"}
    if r.status_code in (200, 201):
        return {"ok": True, "resp": r.json().get("data")}
    if r.status_code == 402:
        # free-tier credits exhausted — retry next cycle, don't fail the row
        return {"error": "x credits depleted", "skip": True}
    logger.warning("[x-actor] %s failed %s: %s",
                   action.action, r.status_code, r.text[:200])
    return {"error": f"{r.status_code}: {r.text[:150]}"}
