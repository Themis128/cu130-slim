"""LinkedIn feed ingestion via browser sidecar → engagement_candidates.

The LinkedIn API cannot read personal feeds — the linkedin-browser-sidecar
(logged-in session) calls the in-session Voyager feed API
(`/voyager/api/feed/updatesV2`) which returns structured JSON: post URN,
actor name, author line ("Name • 2nd" vs "N followers"), age label, and
commentary text. This replaced the old DOM scrape (2026-11-23): Linked's
new feed markup is obfuscated (cfsk* classes, no data-urn), so the scrape
silently produced zero candidates.

Respects the sidecar's own rate-limit cooldown (checks /health first).
"""

import logging
import re

import httpx
from celery import shared_task

logger = logging.getLogger(__name__)

SIDECAR = "http://linkedin-browser-sidecar:9225"

# Runs inside the logged-in linkedin.com page — voyager needs the session
# cookies + JSESSIONID csrf-token header.
FETCH_JS = """(async()=>{
  const csrf=(document.cookie.match(/JSESSIONID="?([^";]+)/)||[])[1]||"";
  const r=await fetch("/voyager/api/feed/updatesV2?count=25&q=chronFeed&start=0",
    {headers:{"csrf-token":csrf}});
  const d=await r.json();
  return JSON.stringify((d.elements||[]).map(el=>({
    urn: el.entityUrn||"",
    share: (el.updateMetadata&&el.updateMetadata.shareUrn)||"",
    actor: (el.actor&&el.actor.name&&el.actor.name.text)||"",
    who: (el.actor&&el.actor.description&&el.actor.description.text)||"",
    nav: (el.actor&&el.actor.navigationContext&&el.actor.navigationContext.accessibilityText)||"",
    age: (el.actor&&el.actor.subDescription&&el.actor.subDescription.text)||"",
    // Array.from slices by code point — a raw .slice() can split a UTF-16
    // surrogate pair and Postgres rejects the lone surrogate on insert.
    text: Array.from(((el.commentary&&el.commentary.text&&el.commentary.text.text)||"")).slice(0,1500).join("")
  })));
})()"""

_POST_URN_RE = re.compile(r"urn:li:(?:share|ugcPost|activity):\d+")
_AGE_RE = re.compile(r"(\d+)\s*([mhdw])")
_AGE_UNIT = {"m": 60, "h": 3600, "d": 86400, "w": 604800}

# Posts authored by us must never become engagement candidates.
_OWN_NAMES = ("themistoklis", "baltzakis", "cloudless")
_PROFILE_NAV_RE = re.compile(r"View (.+?)'s profile")


def _author(p: dict) -> str | None:
    """Display name of a *person* author, else None (company/group/sponsored).

    Voyager gives two author signals: `who` is the headline/description
    ("Harry Ratcliffe • 2nd" for connections, a job title otherwise) and `nav`
    is the navigation label ("View X's profile" for profile posts, "View
    company:/View group:" for org/group containers). Person-authored posts
    always resolve through one of those two; anything else is skipped.
    """
    nav = p.get("nav") or ""
    who = p.get("who") or ""
    m = _PROFILE_NAV_RE.search(nav)
    if m:
        return m.group(1).strip()
    if "•" in who or re.search(r"\b\d+(st|nd|rd|th)\b", who):
        return who.split("•")[0].strip()
    return None


def _post_id(p: dict) -> str | None:
    """Canonical post URN for dedup + engagement actions."""
    share = p.get("share") or ""
    if share.startswith("urn:li:share:") or share.startswith("urn:li:ugcPost:"):
        return share
    m = _POST_URN_RE.search(p.get("urn") or "")
    return m.group(0) if m else None


def _age_seconds(label: str) -> float:
    m = _AGE_RE.search(label or "")
    return int(m.group(1)) * _AGE_UNIT[m.group(2)] if m else 0.0


@shared_task(name="app.worker.tasks.engagement_ingest_li.ingest_linkedin_feed", bind=True, max_retries=0)
def ingest_linkedin_feed(self):
    from app.worker._async import run_async

    return run_async(_run())


async def _run():
    import json as js
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.models.engagement import EngagementCandidate
    from app.worker._async import task_session

    async with httpx.AsyncClient(base_url=SIDECAR, timeout=90) as c:
        h = (await c.get("/health")).json()
        if h.get("rate_limited"):
            return {"skipped": "rate_limited", "until": h.get("rate_limit_until")}
        if not h.get("has_session"):
            return {"skipped": "no_session"}

        r = await c.post("/debug/eval", json={"script": FETCH_JS})
        payload = r.json()
        raw = payload.get("value") or payload.get("result") or "[]"
        posts = js.loads(raw) if isinstance(raw, str) else raw

    new = 0
    seen_types = {"person": 0, "skipped": 0}
    async with task_session() as db:
        for p in posts:
            pid = _post_id(p)
            # Sponsored/promoted posts are ads — never engage them.
            if "sponsoredContentV2" in (p.get("urn") or "") or (p.get("age") or "").startswith("Promoted"):
                seen_types["skipped"] += 1
                continue
            author = _author(p)
            # Drop lone UTF-16 surrogates — asyncpg/Postgres rejects them.
            text = (p.get("text") or "").strip().encode("utf-8", "ignore").decode("utf-8")
            if author:
                author = author.encode("utf-8", "ignore").decode("utf-8")
            if not pid or len(text) < 30 or not author:
                seen_types["skipped"] += 1
                continue
            if any(n in author.lower() for n in _OWN_NAMES):
                seen_types["skipped"] += 1
                continue
            if (await db.execute(select(EngagementCandidate).where(EngagementCandidate.post_id == pid))).scalars().first():
                continue
            age = _age_seconds(p.get("age", ""))
            db.add(
                EngagementCandidate(
                    platform="linkedin",
                    author_handle=author[:120],
                    author_name=author[:120],
                    post_id=pid[:120],
                    post_text=text[:1500],
                    posted_at=datetime.now(UTC) - timedelta(seconds=age),
                    # Following feed = authors Themis follows; connection
                    # status isn't exposed in voyager — conservative flags.
                    is_connection=False,
                    is_followed=True,
                    meta_data={"source": "voyager-feed", "actor": p.get("actor", "")[:120]},
                )
            )
            new += 1
            seen_types["person"] += 1
        await db.commit()
    logger.info("[li-ingest] %d new candidates (%s)", new, seen_types)
    return {"new_candidates": new, **seen_types}
