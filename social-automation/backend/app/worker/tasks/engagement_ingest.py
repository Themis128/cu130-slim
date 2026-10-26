"""Feed ingestion — pull fresh posts from followed accounts → candidates.

X: reverse-chronological timeline via REST v2 (API-clean path first).
"""
import datetime
import logging

import httpx
from celery import shared_task

logger = logging.getLogger(__name__)

@shared_task(name="app.worker.tasks.engagement_ingest.ingest_x_feed", bind=True)
def ingest_x_feed(self):
    from app.worker._async import run_async
    return run_async(_x())

async def _x():
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.engagement import EngagementCandidate
    from app.models.social_account import SocialAccount
    async with async_session_maker() as db:
        acct = (await db.execute(select(SocialAccount).where(
            SocialAccount.platform=="twitter", SocialAccount.status=="active"))
        ).scalars().first()
        if not acct:
            return {"error":"no active twitter account"}
        from app.core.security import decrypt_token
        token = decrypt_token(acct.access_token_enc)
        if not token:
            return {"error":"empty token"}
        uid = (acct.meta_data or {}).get("user_id")
        async with httpx.AsyncClient(timeout=20) as h:
            r = await h.get(
                f"https://api.twitter.com/2/users/{uid}/timelines/reverse_chronological",
                headers={"Authorization":f"Bearer {token}"},
                params={"max_results":20,"tweet.fields":"author_id,text,created_at"})
            if r.status_code != 200:
                return {"error":f"x timeline {r.status_code}"}
            tweets = r.json().get("data",[])
        new = 0
        for t in tweets:
            exists = (await db.execute(select(EngagementCandidate).where(
                EngagementCandidate.post_id==t["id"]))).scalars().first()
            if exists:
                continue
            db.add(EngagementCandidate(
                platform="twitter", author_handle=t.get("author_id",""),
                post_id=t["id"], post_text=t.get("text",""),
                posted_at=datetime.datetime.fromisoformat(
                    t["created_at"].replace("Z","+00:00"))))
            new += 1
        await db.commit()
        return {"new_candidates": new}
