"""LinkedIn newsletter subscribers -> EspoCRM sync.

Subscribers are scraped from the rendered subscribers dialog — LinkedIn's
voyager subscriber endpoint 400s on our call shapes, but the analytics UI
renders the same list server-side. Path: analytics page -> "See all" ->
scroll the dialog to stable height -> extract /in/ rows.

Dedupe: LI-URN marker in the Espo Lead description + Redis set.
Subscribers carry no email — nurture is LinkedIn-native (welcome alert
with a ready note -> owner connects/DMs).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_REDIS_KEY = "linkedin-newsletter:synced-urns"

_SCRAPE_EXPR = """const link=[...document.querySelectorAll('a')].find(a=>
  /^\\s*See all\\s*$/i.test(a.innerText||'') && /subscriber/i.test(a.href));
if(!link) return {err:'no See all link', url:location.href,
  text:(document.body.innerText||'').slice(0,300)};
link.click();
await new Promise(r=>setTimeout(r,5000));
for(let i=0;i<15;i++){
  if(document.querySelectorAll('a[href*="/in/"]').length>0) break;
  await new Promise(r=>setTimeout(r,1500));
}
let lastH=0,stable=0;
for(let i=0;i<60&&stable<3;i++){
  const dlg=[...document.querySelectorAll('*')].find(e=>
    e.scrollHeight>e.clientHeight&&e.clientHeight>300);
  if(dlg){dlg.scrollTop=dlg.scrollHeight;
    if(dlg.scrollHeight===lastH){stable++;}else{stable=0;lastH=dlg.scrollHeight;}}
  await new Promise(r=>setTimeout(r,700));
}
const rows=[...document.querySelectorAll('a[href*="/in/"]')];
const seen=new Set(); const subs=[];
for(const a of rows){
  const name=(a.innerText||'').split('\\n')[0].trim();
  const urn=(a.href.match(/\\/in\\/([\\w-]+)/)||[])[1]||'';
  if(!name||seen.has(urn)) continue; seen.add(urn);
  const parent=a.closest('li,div[class]');
  const txt=(parent?parent.innerText:'').split('\\n')
    .map(x=>x.trim()).filter(Boolean);
  const headline=txt.slice(1).join(' | ')
    .replace(/\\d+(st|nd|rd|th) degree connection \\| \\d+(st|nd|rd|th) \\| /,'')
    .replace(/\\| (Message|Follow)$/,'').slice(0,150);
  subs.push({name, urn, headline});
}
return {url:location.href, count:subs.length, subs:subs};"""


async def fetch_subscribers(series_urn: str) -> list[dict[str, str]]:
    """Scrape the subscribers dialog via the sidecar's /debug/eval."""
    base = os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL", "http://stack-ops:9225")
    series_id = series_urn.split(":")[-1]
    page_url = (f"https://www.linkedin.com/analytics/newsletter/"
                f"urn:li:fsd_contentSeries:{series_id}/")
    out: list[dict[str, str]] = []
    async with httpx.AsyncClient(base_url=base, timeout=180) as c:
        await c.post("/debug/navigate", json={"url": page_url})
        await asyncio.sleep(7)
        resp = await c.post("/debug/eval", json={"expr": _SCRAPE_EXPR})
        payload = resp.json().get("value") or {}
        if payload.get("err"):
            logger.warning("subscriber scrape failed: %s", payload["err"])
            return out
        for s in payload.get("subs") or []:
            out.append({
                "name": s["name"],
                "headline": s.get("headline") or "",
                "urn": f"urn:li:in:{s['urn']}",
                "profile_url": f"https://www.linkedin.com/in/{s['urn']}/",
            })
    return out


async def espo_upsert_subscriber(sub: dict[str, str]) -> bool:
    """Upsert into EspoCRM; dedupe on the LI-URN marker in description."""
    from app.services.espocrm_client import espo_get, espo_post
    found = await espo_get("/Lead", params={"searchParams": json.dumps({
        "maxSize": 1, "select": ["id"],
        "where": [{"type": "contains", "attribute": "description", "value": sub["urn"]}],
    })})
    if found.get("list"):
        return False
    first, _, last = sub["name"].partition(" ")
    await espo_post("/Lead", {
        "firstName": first,
        "lastName": last or "(LinkedIn)",
        "source": "Newsletter",
        "description": (f"LinkedIn newsletter subscriber\n{sub['headline']}\n"
                        f"{sub['profile_url']}\nLI-URN:{sub['urn']}"),
        "title": sub["headline"][:90],
    })
    return True


def _personalized_note(sub: dict[str, str]) -> str:
    name = sub["name"].split()[0]
    headline = sub.get("headline") or ""
    role = headline.split(" at ")[0].split("|")[0].split(",")[0].strip() if headline else ""
    hook = f"Saw you're {role}, " if role and len(role) <= 60 else ""
    intro = ("For context: I run Cloudless, we help founders, teams and "
             "companies move to cloud and serverless, and ship the "
             "dashboards that tell them it's working. ")
    return (f"Thanks for subscribing to The Cloudless Build Log, {name}. "
            f"{hook}Really glad to have you. {intro}"
            "If a future issue touches on something you're working "
            "through, my inbox is open. And if you'd like the free site "
            "audit or the benchmark report, just reply here with your "
            "email and I'll send it.")


async def _subscriber_alert(sub: dict[str, str]) -> None:
    from app.services.slack_notifications import post_alert_to_slack
    await post_alert_to_slack(
        f":bell: *New newsletter subscriber* {sub['name']}"
        + (f" | {sub['headline']}" if sub["headline"] else "")
        + f"\n{sub['profile_url'] or '(no profile link)'}\n"
        f"Suggested welcome note:\n> {_personalized_note(sub)}")


async def sync_newsletter_subscribers(series_urn: str) -> dict[str, Any]:
    """Full sync: DOM scrape -> dedupe -> EspoCRM + Slack welcome alert."""
    import redis.asyncio as aioredis

    from app.core.config import get_settings

    stats: dict[str, Any] = {"fetched": 0, "created": 0, "skipped": 0}
    if not series_urn:
        return {"skipped": "no LINKEDIN_NEWSLETTER_SERIES_URN configured"}

    subs = await fetch_subscribers(series_urn)
    stats["fetched"] = len(subs)
    if not subs:
        return stats

    seen: set[str] = set()
    r = None
    try:
        r = aioredis.from_url(get_settings().REDIS_URL)
        raw = await r.smembers(_REDIS_KEY)
        seen = {x.decode() if isinstance(x, bytes) else x for x in raw}
    except Exception as exc:  # noqa: BLE001 — dedupe is a cache; down Redis just
        logger.warning("subscriber dedupe set unavailable, proceeding without: %s", exc)
    new_urns = []
    for sub in subs:
        if sub["urn"] in seen:
            stats["skipped"] += 1
            continue
        try:
            if await espo_upsert_subscriber(sub):
                stats["created"] += 1
                await _subscriber_alert(sub)
            else:
                stats["skipped"] += 1
            new_urns.append(sub["urn"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("subscriber upsert failed for %s: %s", sub["urn"][:40], exc)
            stats["skipped"] += 1
    if new_urns and r is not None:
        try:
            await r.sadd(_REDIS_KEY, *new_urns)
        except Exception:  # noqa: BLE001
            pass
    return stats
