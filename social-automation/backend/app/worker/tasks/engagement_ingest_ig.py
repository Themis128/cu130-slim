"""Instagram feed ingestion via shared browser bridge → candidates.

Uses browser-novnc :9223 (/session/*). Skips cleanly when the browser
is busy with platform sweeps or the session isn't logged in.
"""
import asyncio
import json as js
import logging

import httpx
from celery import shared_task

logger = logging.getLogger(__name__)

BRIDGE = "http://browser-novnc:9223"

EXTRACT_JS = """(() => {
  const out = [];
  document.querySelectorAll('article').forEach((el, i) => {
    if (i >= 12) return;
    const link = el.querySelector('a[href*="/p/"], a[href*="/reel/"]');
    const href = link ? link.getAttribute('href') : '';
    const author = el.querySelector('a[role=link] span, header a')?.innerText?.trim() || '';
    const txt = (el.querySelector('h1, div._a9zr, span')?.innerText || '').trim().slice(0,1200);
    const time = el.querySelector('time')?.getAttribute('datetime') || '';
    if (href && author) out.push({href, author, txt, time});
  });
  return JSON.stringify(out);
})()"""


@shared_task(name="app.worker.tasks.engagement_ingest_ig.ingest_ig_feed",
             bind=True, max_retries=0)
def ingest_ig_feed(self):
    from app.worker._async import run_async
    return run_async(_run())


async def _ev(c, expr):
    r = await c.post("/session/evaluate", json={"expression": expr})
    return r.json().get("result")


async def _run():
    import datetime as dt

    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.engagement import EngagementCandidate

    async with httpx.AsyncClient(base_url=BRIDGE, timeout=60) as c:
        try:
            await c.post("/session/start", json={
                "platform": "instagram", "force": True})
        except Exception as e:
            return {"skipped": f"browser busy: {type(e).__name__}"}
        await c.post("/session/navigate",
                     json={"url": "https://www.instagram.com/"})
        await asyncio.sleep(7)
        for _ in range(3):
            await _ev(c, "window.scrollBy(0,1200); 'ok'")
            await asyncio.sleep(2)
        raw = await _ev(c, EXTRACT_JS)
        posts = js.loads(raw or "[]")

    new = 0
    async with async_session_maker() as db:
        for p in posts:
            href = p.get("href", "")
            sc = href.strip("/").split("/")[-1]
            if not sc or len(sc) < 5:
                continue
            if (await db.execute(select(EngagementCandidate).where(
                    EngagementCandidate.post_id == sc))).scalars().first():
                continue
            try:
                ts = dt.datetime.fromisoformat(
                    (p.get("time") or "").replace("Z", "+00:00"))
            except Exception:
                ts = dt.datetime.now(dt.UTC)
            db.add(EngagementCandidate(
                platform="instagram", author_handle=p.get("author", "")[:120],
                author_name=p.get("author", "")[:120], post_id=sc,
                post_text=p.get("txt", "")[:1500], posted_at=ts,
                is_followed=True, is_connection=True))
            new += 1
        await db.commit()
    logger.info("[ig-ingest] %d new candidates", new)
    return {"new_candidates": new}
