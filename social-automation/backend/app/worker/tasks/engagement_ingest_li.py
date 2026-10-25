"""LinkedIn feed ingestion via browser sidecar → engagement_candidates.

The LinkedIn API cannot read personal feeds — the linkedin-browser-sidecar
(logged-in session) scrapes /feed and extracts posts. Respects the
sidecar's own rate-limit cooldown (checks /health first).
"""
import logging

import httpx
from celery import shared_task

logger = logging.getLogger(__name__)

SIDECAR = "http://linkedin-browser-sidecar:9225"

# Extract up to N visible posts: urn, author, text, age label
EXTRACT_JS = """return (() => {
  const out = [];
  document.querySelectorAll('div.feed-shared-update-v2').forEach((el, i) => {
    if (i >= 15) return;
    const urn = el.getAttribute('data-urn') || '';
    const actor = el.querySelector('.update-components-actor__name')?.innerText?.trim() || '';
    const handle = el.querySelector('.update-components-actor__meta a')?.href || '';
    const txt = el.querySelector('.update-components-text, .feed-shared-inline-show-more-text')?.innerText?.trim() || '';
    const age = el.querySelector('.update-components-actor__sub-description')?.innerText?.trim() || '';
    if (txt.length > 30) out.push({urn, actor, handle, txt: txt.slice(0,1500), age});
  });
  return JSON.stringify(out);
})()"""


@shared_task(name="app.worker.tasks.engagement_ingest_li.ingest_linkedin_feed",
             bind=True, max_retries=0)
def ingest_linkedin_feed(self):
    from app.worker._async import run_async
    return run_async(_run())


async def _post(client, path, json=None):
    r = await client.post(path, json=json or {})
    return r


async def _run():
    import json as js
    import re as re_

    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.engagement import EngagementCandidate

    async with httpx.AsyncClient(base_url=SIDECAR, timeout=60) as c:
        h = (await c.get("/health")).json()
        if h.get("rate_limited"):
            return {"skipped": "rate_limited", "until": h.get("rate_limit_until")}
        if not h.get("has_session"):
            return {"skipped": "no_session"}

        await _post(c, "/debug/navigate", {"url": "https://www.linkedin.com/feed/"})
        import asyncio
        await asyncio.sleep(6)
        # scroll a few times to load posts
        for _ in range(4):
            await _post(c, "/debug/eval", {"expr": "window.scrollBy(0,1200); return 'ok'"})
            await asyncio.sleep(2)

        r = await _post(c, "/debug/eval", {"expr": EXTRACT_JS})
        raw = r.json().get("value") or r.json().get("result") or "[]"
        posts = js.loads(raw) if isinstance(raw, str) else raw

    def _age_seconds(label: str) -> float:
        m = re_.search(r"(\d+)\s*(m|h|d|w)", label)
        if not m:
            return 0.0
        n, u = int(m.group(1)), m.group(2)
        return n * {"m": 60, "h": 3600, "d": 86400, "w": 604800}[u]

    new = 0
    async with async_session_maker() as db:
        for p in posts:
            pid = p.get("urn") or p.get("handle") or p.get("txt", "")[:60]
            if not pid:
                continue
            if (await db.execute(select(EngagementCandidate).where(
                    EngagementCandidate.post_id == pid))).scalars().first():
                continue
            import datetime as dt
            age = _age_seconds(p.get("age", ""))
            db.add(EngagementCandidate(
                platform="linkedin",
                author_handle=p.get("handle", "")[:120],
                author_name=p.get("actor", "")[:120],
                post_id=pid[:120],
                post_text=p.get("txt", ""),
                posted_at=dt.datetime.now(dt.UTC) - dt.timedelta(seconds=age),
                is_connection=True, is_followed=True))
            new += 1
        await db.commit()
    logger.info("[li-ingest] %d new candidates", new)
    return {"new_candidates": new}
