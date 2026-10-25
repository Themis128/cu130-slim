"""Facebook engagement actor — comment via in-page UI on group posts.

Navigates to the post permalink, focuses the comment box, types and
submits. Meta hard-cap: 5 comments/hour enforced at queue layer.
Likes via aria-label click. Reposts are never auto-acted.
"""
import asyncio
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)
BRIDGE = os.environ.get("ENGAGEMENT_BROWSER_URL", "http://engagement-browser:9223")

LIKE_JS = """(() => {
  const btn = document.querySelector('[aria-label="Like"], [aria-label*="like" i]');
  if (!btn) return 'no-btn';
  (btn.closest('[role=button]') || btn).click();
  return 'liked';
})()"""


async def act(action, db) -> dict:
    async with httpx.AsyncClient(base_url=BRIDGE, timeout=90) as c:
        try:
            await c.post("/session/start",
                         json={"platform": "facebook", "force": True})
        except Exception:
            return {"error": "browser busy", "skip": True}

        if action.action == "like":
            r = await c.post("/session/evaluate", json={"expression": LIKE_JS})
            res = r.json().get("result", "")
            return {"ok": True} if res == "liked" else {"error": res, "skip": True}
        if action.action == "comment":
            if not action.comment_draft:
                return {"error": "empty draft"}
            await c.post("/session/navigate",
                         json={"url": action.target_post_id})
            await asyncio.sleep(6)
            expr = f"""(async () => {{
              const sleep=ms=>new Promise(r=>setTimeout(r,ms));
              const box = document.querySelector('div[contenteditable=true][role=textbox], form[role=presentation] div[contenteditable=true]');
              if (!box) return 'no-comment-box';
              box.focus();
              const text = {json.dumps(action.comment_draft)};
              for (const ch of text) {{
                document.execCommand('insertText', false, ch);
                await new Promise(r => setTimeout(r, 30 + Math.random()*90));
              }}
              const btn = [...document.querySelectorAll('[role=button]')].find(b => /comment|post/i.test(b.getAttribute('aria-label')||''));
              if (!btn) return 'no-submit';
              btn.click();
              return 'commented';
            }})()"""
            r = await c.post("/session/evaluate", json={"expression": expr})
            res = r.json().get("result", "")
            if res == "commented":
                return {"ok": True}
            return {"error": res or "eval failed", "skip": res == "no-comment-box"}
        return {"error": f"fb: {action.action} not auto-acted", "skip": True}
