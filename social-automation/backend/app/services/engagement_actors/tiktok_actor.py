"""TikTok engagement actor — punchy short comments via the dedicated
browser sidecar (tiktok-browser-sidecar:9224).

The shared engagement-browser bridge's TikTok session is logged out
(tiktok.com/login wall) — every like churned pending_actor→skip until
failed. The dedicated sidecar holds the live session (has_session) and
exposes /browse + /evaluate.

Comment drafts for TikTok get rewritten short (<10 words) by the 3C
variant prompt upstream. Viral candidates route to the gate as
duet/stitch suggestions (manual phone action — never automated).
"""

import asyncio
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)
SIDECAR = os.environ.get("TIKTOK_SIDECAR_URL", "http://tiktok-browser-sidecar:9224")


async def _eval(c: httpx.AsyncClient, expression: str):
    r = await c.post("/evaluate", json={"expression": expression})
    data = r.json()
    if data.get("status") != "ok":
        return data.get("error") or "eval failed"
    return data.get("result")


async def act(action, db) -> dict:
    if action.action in ("repost", "duet_flag"):
        return {"error": "tiktok: duet/stitch are manual — gate flagged", "skip": True}
    if action.action not in ("like", "comment"):
        return {"error": f"tt: {action.action} unsupported", "skip": True}
    async with httpx.AsyncClient(base_url=SIDECAR, timeout=90) as c:
        try:
            health = (await c.get("/health")).json()
            if not health.get("has_session"):
                return {"error": "no-session", "skip": True}
        except Exception:
            return {"error": "sidecar unreachable", "skip": True}
        url = f"https://www.tiktok.com/video/{action.target_post_id}"
        try:
            await c.post("/browse", json={"url": url})
        except Exception:
            return {"error": "browse failed", "skip": True}
        await asyncio.sleep(3)
        if action.action == "like":
            res = await _eval(
                c,
                "(() => {const b=document.querySelector('[data-e2e=like-icon],[data-e2e*=like] button');"
                "if(!b)return 'no-btn';b.click();return 'liked'})()",
            )
            return {"ok": True} if res == "liked" else {"error": str(res), "skip": True}
        if action.action == "comment":
            if not action.comment_draft:
                return {"error": "empty draft"}
            res = await _eval(
                c,
                f"""(async () => {{
                  const box = document.querySelector('div[contenteditable=true][data-e2e*=comment], div[contenteditable=true]');
                  if (!box) return 'no-comment-box';
                  box.focus();
                  document.execCommand('insertText', false, {json.dumps(action.comment_draft)});
                  const btn = [...document.querySelectorAll('[data-e2e*=comment-post],[role=button]')]
                    .find(b => /post|comment/i.test(b.innerText||b.getAttribute('aria-label')||''));
                  if (!btn) return 'no-submit';
                  btn.click();
                  return 'commented';
                }})()""",
            )
            if res == "commented":
                return {"ok": True}
            return {"error": str(res or "eval failed"), "skip": res == "no-comment-box"}
    return {"error": "unreachable"}
