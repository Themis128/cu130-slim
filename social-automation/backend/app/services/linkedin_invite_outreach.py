"""LinkedIn personal-profile invite outreach — daily small-batch sender.

Drives the linkedin-browser-sidecar (:9225) people-search + /connect
voyager endpoint to send a handful of connection invites per day to
profiles matching SocialAuto-relevant keywords (SMB owners, social
media marketers — the audience for the platform-guides content).

Safety contract (linkedin-session-discipline rule):
- Sidecar session only — never triggers a fresh credential login.
- Tiny daily volume (default 5) with randomized 30-90s pacing.
- Dedupe via Redis set so a profile is never re-invited by us.
- Every send goes through /connect which itself reports
  already_connected / PENDING / RATE_LIMITED — those are honored,
  never retried aggressively.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

SEARCH_KEYWORDS = [
    "social media manager",
    "small business owner marketing",
    "SMM specialist",
    "digital marketing agency founder",
    "ecommerce social media",
]

_INVITE_NOTE = "Hi — I run SocialAuto and write practical platform guides for SMB social media. Would love to connect and share what works."

_SENT_KEY = "li_outreach:sent"
_CURSOR_KEY = "li_outreach:kw_cursor"
_SENT_TTL = 60 * 60 * 24 * 180  # 180 days


def _base_url() -> str:
    return get_settings().LINKEDIN_BROWSER_SIDECAR_URL


async def _post(
    path: str,
    payload: dict[str, Any],
    timeout: float = 90.0,
    allow_error: bool = False,
) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=_base_url(), timeout=timeout) as c:
        r = await c.post(path, json=payload)
        try:
            body = r.json()
        except Exception:
            body = {"raw": r.text[:300]}
        if r.status_code >= 400 and not allow_error:
            raise RuntimeError(f"sidecar {path} -> {r.status_code}: {str(body)[:300]}")
        return body


async def _get(path: str, timeout: float = 30.0) -> dict[str, Any]:
    async with httpx.AsyncClient(base_url=_base_url(), timeout=timeout) as c:
        r = await c.get(path)
        r.raise_for_status()
        return r.json()


async def _redis():
    import redis.asyncio as aioredis

    return aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)


async def _session_ok() -> bool:
    try:
        s = await _get("/session")
        return bool(s.get("has_session") or s.get("logged_in"))
    except Exception:
        return False


async def _scrape_slugs(keyword: str, limit: int = 25) -> list[str]:
    """People-search scrape: navigate + extract /in/<slug>/ profile links."""
    url = f"https://www.linkedin.com/search/results/people/?keywords={quote_plus(keyword)}&origin=SWITCH_SEARCH_VERTICAL"
    await _post("/debug/navigate", {"url": url}, timeout=75.0)
    await asyncio.sleep(random.uniform(2.0, 4.0))
    # /debug/eval wraps expr as `(async () => { <expr> })()` — it must be a
    # function body ending in `return`, not a self-invoking expression.
    expr = r"""
    const seen = new Set(); const out = [];
    document.querySelectorAll('a[href*="/in/"]').forEach(a => {
      const m = a.href.match(/\/in\/([^\/?#]+)/);
      if (m && !seen.has(m[1])) { seen.add(m[1]); out.push(m[1]); }
    });
    return out.slice(0, LIMIT_PLACEHOLDER);
    """.replace("LIMIT_PLACEHOLDER", str(limit))
    res = await _post("/debug/eval", {"expr": expr}, timeout=45.0)
    val = res.get("value") or res.get("result") or []
    if isinstance(val, str):
        try:
            val = json.loads(val)
        except Exception:
            val = []
    return [s for s in val if isinstance(s, str) and s][:limit]


async def send_profile_invites(count: int = 5) -> dict[str, Any]:
    """Send up to `count` connection invites to scraped relevant profiles."""
    if not await _session_ok():
        return {"status": "skipped", "reason": "sidecar session not active"}

    r = await _redis()
    try:
        sent_set = await r.smembers(_SENT_KEY)
        cursor = int(await r.get(_CURSOR_KEY) or 0)
    finally:
        await r.aclose()

    sent: list[str] = []
    attempted = 0
    tried: set[str] = set()
    kw_i = cursor
    # Hard iteration cap — scrape failures don't increment `attempted`, so
    # without this a dead sidecar would loop forever.
    for _ in range(count * 8):
        if len(sent) >= count:
            break
        keyword = SEARCH_KEYWORDS[kw_i % len(SEARCH_KEYWORDS)]
        kw_i += 1
        try:
            slugs = await _scrape_slugs(keyword)
        except Exception as exc:
            logger.warning("invite scrape failed for %r: %s", keyword, exc)
            continue
        candidates = [s for s in slugs if s not in sent_set and s not in sent and s not in tried]
        if not candidates:
            continue
        slug = candidates[0]
        tried.add(slug)
        attempted += 1
        # /connect reports domain failures as HTTP error statuses with a
        # `code` field (PENDING / NO_RECIPIENT / RATE_LIMITED / NO_CSRF) —
        # allow_error keeps the body so we can honor each code.
        try:
            res = await _post(
                "/connect",
                {"vanity": slug, "note": _INVITE_NOTE, "visit": False},
                timeout=120.0,
                allow_error=True,
            )
        except Exception as exc:
            logger.info("invite %s transport error: %s", slug, str(exc)[:160])
            continue
        code = res.get("code") or ""
        if res.get("invited"):
            sent.append(slug)
            sent_set.add(slug)
            await asyncio.sleep(random.uniform(30, 90))
        elif code == "RATE_LIMITED":
            return {
                "status": "rate_limited",
                "sent": sent,
                "attempted": attempted,
            }
        elif res.get("already_connected") or code in ("PENDING", "NO_RECIPIENT"):
            sent_set.add(slug)  # don't retry dead ends
        else:
            logger.info("invite %s not sent: %s", slug, str(res)[:160])

    r = await _redis()
    try:
        if sent:
            await r.sadd(_SENT_KEY, *sent)
            await r.expire(_SENT_KEY, _SENT_TTL)
        await r.set(_CURSOR_KEY, kw_i % len(SEARCH_KEYWORDS))
    finally:
        await r.aclose()

    return {
        "status": "sent" if sent else "none",
        "sent": sent,
        "attempted": attempted,
        "keywords_used": kw_i - cursor,
    }
