"""Instagram engagement actor — like via in-page UI click on the feed card.

Finds the article whose link matches the target shortcode and clicks its
Like button (aria-label). Comments stay human-gated and are not auto-acted.
Skips cleanly when the shared browser is busy.
"""
import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)

BRIDGE = os.environ.get("ENGAGEMENT_BROWSER_URL", "http://engagement-browser:9223")

LIKE_JS = """(() => {
  const el = [...document.querySelectorAll('article')].find(a =>
    a.querySelector('a[href*="/p/{SC}"], a[href*="/reel/{SC}"]'));
  if (!el) return 'post-not-visible';
  const btn = el.querySelector('[aria-label="Like"]');
  if (!btn) return 'already-liked-or-no-btn';
  (btn.closest('button') || btn).click();
  return 'liked';
})()"""


REPOST_JS = """(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const share = document.querySelector('svg[aria-label*="Share"], [aria-label*="Share" i]');
  if (!share) return 'no-share-btn';
  (share.closest('[role=button]') || share).click();
  await sleep(1500);
  const story = [...document.querySelectorAll('[role=button], span, div')]
    .find(b => /add post to your story|add to.*story/i.test(b.innerText || ''));
  if (!story) return 'no-story-option';
  (story.closest('[role=button]') || story).click();
  await sleep(1200);
  const confirm = [...document.querySelectorAll('[role=button]')]
    .find(b => /^share|^done$/i.test((b.innerText || '').trim()));
  if (confirm) confirm.click();
  return 'shared-to-story';
})()"""


async def act(action, db) -> dict:
    if action.action not in ("like", "repost"):
        return {"error": f"ig: {action.action} not auto-acted", "skip": True}
    sc = action.target_post_id
    async with httpx.AsyncClient(base_url=BRIDGE, timeout=60) as c:
        try:
            await c.post("/session/start",
                         json={"platform": "instagram", "force": True})
        except Exception:
            return {"error": "browser busy", "skip": True}
        if action.action == "repost":
            await c.post("/session/navigate",
                         json={"url": f"https://www.instagram.com/p/{sc}/"})
            await asyncio.sleep(6)
            r = await c.post("/session/evaluate",
                             json={"expression": REPOST_JS})
            res = r.json().get("result", "")
            if res == "shared-to-story":
                return {"ok": True, "resp": {"via": "share-to-story"}}
            return {"error": res or "share sheet failed", "skip": True}
        r = await c.post("/session/evaluate", json={
            "expression": LIKE_JS.replace("{SC}", sc)})
        res = r.json().get("result", "")
    if res == "liked":
        return {"ok": True}
    if res == "already-liked-or-no-btn":
        return {"ok": True, "resp": {"note": res}}
    if not res or res == "post-not-visible":
        # empty eval = browser stolen mid-call; post off-screen = retry next pass
        return {"error": res or "browser busy", "skip": True}
    logger.warning("[ig-actor] like %s: %s", sc, res)
    return {"error": res}
