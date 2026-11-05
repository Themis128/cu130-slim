"""Extended Nextcloud sync surfaces — CRM→Tables, deploys→Talk/Announce,
scheduled posts→Calendar, Forms intake→EspoCRM leads.

Same contract as nextcloud_sync: idempotent, driven by real rows, all
failures non-fatal (Nextcloud is a reporting/intake surface, never on the
publish path). Small "last seen" cursors live in Redis so repeat runs are
cheap no-ops.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.content import Post, PostStatus
from app.models.social_account import SocialAccount

logger = logging.getLogger(__name__)

settings = get_settings()

_GH_COMMITS = [
    ("cloudless.gr", "https://api.github.com/repos/Themis128/cloudless.gr/commits?sha=main&per_page=5"),
    ("SocialAuto", "https://api.github.com/repos/Themis128/cu130-slim/commits?sha=master&per_page=5"),
]


def _enabled() -> bool:
    from app.services.nextcloud_sync import nextcloud_sync_enabled

    return nextcloud_sync_enabled()


def _base() -> str:
    from app.services.nextcloud_sync import _base_url

    return _base_url()


def _client() -> httpx.AsyncClient:
    from app.services.nextcloud_sync import _client as _c

    return _c()


async def _cursor(key: str) -> str | None:
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        v = await r.get(f"ncx:{key}")
        await r.aclose()
        return v
    except Exception as exc:  # noqa: BLE001
        logger.debug("ncx cursor read %s failed: %s", key, exc)
        return None


async def _set_cursor(key: str, value: str) -> None:
    try:
        import redis.asyncio as aioredis

        r = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await r.set(f"ncx:{key}", value, ex=30 * 86400)
        await r.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.debug("ncx cursor write %s failed: %s", key, exc)


# ── Tables: CRM pipeline ──────────────────────────────────────────────


async def _ensure_table(client: httpx.AsyncClient, title: str, columns: list[dict]) -> int | None:
    """Return table id for `title`, creating it (with columns) if missing."""
    r = await client.get(f"{_base()}/index.php/apps/tables/api/1/tables")
    r.raise_for_status()
    for t in r.json():
        if t.get("title") == title:
            tid = int(t["id"])
            existing = await _table_col_map(client, tid)
            for col in columns:
                if col["title"] not in existing:
                    cr = await client.post(
                        f"{_base()}/index.php/apps/tables/api/1/tables/{tid}/columns",
                        json=col,
                    )
                    cr.raise_for_status()
            return tid
    r = await client.post(
        f"{_base()}/index.php/apps/tables/api/1/tables",
        json={"title": title, "emoji": "📊"},
    )
    r.raise_for_status()
    table = r.json()
    tid = int(table["id"])
    for col in columns:
        cr = await client.post(
            f"{_base()}/index.php/apps/tables/api/1/tables/{tid}/columns",
            json=col,
        )
        cr.raise_for_status()
    return tid


async def _table_col_map(client: httpx.AsyncClient, tid: int) -> dict[str, int]:
    r = await client.get(f"{_base()}/index.php/apps/tables/api/1/tables/{tid}/columns")
    r.raise_for_status()
    return {c["title"]: int(c["id"]) for c in r.json()}


async def _existing_lead_ids(client: httpx.AsyncClient, tid: int, colmap: dict[str, int]) -> set[str]:
    out: set[str] = set()
    r = await client.get(f"{_base()}/index.php/apps/tables/api/1/tables/{tid}/rows")
    r.raise_for_status()
    lid = colmap.get("LeadId")
    if lid is None:
        return out
    for row in r.json():
        for cell in row.get("data") or []:
            if int(cell.get("columnId", -1)) == lid and cell.get("value"):
                out.add(str(cell["value"]))
    return out


async def sync_crm_pipeline(db: AsyncSession) -> dict:
    """Latest EspoCRM leads → 'CRM Pipeline' Tables table (dedupe by LeadId)."""
    from app.services import espocrm_client as espo

    if not espo.configured():
        return {"skipped": "espocrm not configured"}
    rows = await espo.list_all(
        "Lead",
        select=["id", "name", "emailAddress", "status", "source", "createdAt"],
        order_by="createdAt",
        order="desc",
        max_items=200,
    )
    if not rows:
        return {"leads": 0}
    try:
        async with _client() as client:
            tid = await _ensure_table(
                client,
                "CRM Pipeline",
                [
                    {"title": "LeadId", "type": "text", "subtype": "line", "mandatory": False, "description": ""},
                    {"title": "Name", "type": "text", "subtype": "line", "mandatory": False, "description": ""},
                    {"title": "Email", "type": "text", "subtype": "line", "mandatory": False, "description": ""},
                    {"title": "Status", "type": "text", "subtype": "line", "mandatory": False, "description": ""},
                    {"title": "Source", "type": "text", "subtype": "line", "mandatory": False, "description": ""},
                    {"title": "Created", "type": "datetime", "subtype": "date", "mandatory": False, "description": ""},
                ],
            )
            colmap = await _table_col_map(client, tid)
            seen = await _existing_lead_ids(client, tid, colmap)
            inserted = 0
            for lead in rows:
                lid = str(lead.get("id"))
                if lid in seen:
                    continue
                created = (lead.get("createdAt") or "")[:10] or None
                data = {
                    colmap["LeadId"]: lid,
                    colmap["Name"]: lead.get("name") or "",
                    colmap["Email"]: lead.get("emailAddress") or "",
                    colmap["Status"]: lead.get("status") or "",
                    colmap["Source"]: lead.get("source") or "",
                }
                if created and "Created" in colmap:
                    data[colmap["Created"]] = created
                rr = await client.post(
                    f"{_base()}/index.php/apps/tables/api/1/tables/{tid}/rows",
                    json={"data": data},
                )
                rr.raise_for_status()
                inserted += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("crm pipeline sync failed: %s", exc)
        return {"error": str(exc)[:200]}
    return {"inserted": inserted, "table_id": tid, "total_leads": len(rows)}


# ── Talk + AnnouncementCenter: platform deploy events ──────────────────


async def _announce(client: httpx.AsyncClient, subject: str, message: str) -> None:
    try:
        r = await client.post(
            f"{_base()}/ocs/v2.php/apps/announcementcenter/api/v1/announcements",
            json={"subject": subject, "message": message},
        )
        if r.status_code >= 300:
            logger.debug("announcement post HTTP %s", r.status_code)
    except Exception as exc:  # noqa: BLE001
        logger.debug("announcement failed: %s", exc)


async def sync_deploy_events() -> dict:
    """New commits on both repos → Talk ops room + AnnouncementCenter."""
    from app.services.nextcloud_talk import post_to_talk, talk_enabled

    posted: list[str] = []
    gh_headers = {"User-Agent": "socialauto-deploy-watch/1.0"}
    async with httpx.AsyncClient(timeout=20.0, headers=gh_headers) as gh, _client() as nc:
        for label, url in _GH_COMMITS:
            key = f"deploy:{label}"
            try:
                commits = (await gh.get(url)).json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("deploy watch %s fetch failed: %s", label, exc)
                continue
            if not isinstance(commits, list) or not commits:
                continue
            last = await _cursor(key)
            new = []
            for c in commits:
                sha = c.get("sha", "")
                if sha == last:
                    break
                new.append(c)
            if not new:
                continue
            if last is None:  # first run — seed cursor silently
                await _set_cursor(key, new[0]["sha"])
                continue
            for c in reversed(new):
                sha7 = c["sha"][:7]
                msg = (c.get("commit") or {}).get("message", "").splitlines()[0][:140]
                text = f"🚀 {label} deploy — {sha7} {msg}"
                if talk_enabled():
                    await post_to_talk(text, purpose="digest")
                await _announce(nc, f"{label} deployed", f"{sha7} — {msg}")
                posted.append(f"{label}:{sha7}")
            await _set_cursor(key, new[0]["sha"])
    return {"announced": posted}


# ── Talk: check-run failures on the newest commit ─────────────────────


async def sync_check_status() -> dict:
    """Alert the ops room when the newest master commit has failed checks."""
    from app.services.nextcloud_talk import post_to_talk, talk_enabled

    if not talk_enabled():
        return {"skipped": "talk disabled"}
    alerted: list[str] = []
    async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "socialauto-check-watch/1.0"}) as gh:
        for label, url in _GH_COMMITS:
            try:
                commits = (await gh.get(url)).json()
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(commits, list) or not commits:
                continue
            sha = commits[0]["sha"]
            repo = url.split("/repos/")[1].split("/")[0] + "/" + url.split("/repos/")[1].split("/")[1]
            key = f"check:{sha}"
            if await _cursor(key):
                continue
            try:
                r = await gh.get(f"https://api.github.com/repos/{repo}/commits/{sha}/check-runs?per_page=50")
                runs = r.json().get("check_runs", [])
            except Exception:
                continue
            pending = [c["name"] for c in runs if c.get("status") != "completed"]
            if pending:
                continue  # still running — re-check next pass (no cursor write)
            failed = [c["name"] for c in runs if c.get("conclusion") == "failure"]
            await _set_cursor(key, "seen")
            if failed:
                text = f"❌ {label} {sha[:7]} — checks failed: {', '.join(failed[:5])}"
                await post_to_talk(text, purpose="digest")
                alerted.append(f"{label}:{sha[:7]}")
    return {"check_alerts": alerted}


# ── Talk: failed publish alerts ────────────────────────────────────────


async def sync_failed_publishes(db: AsyncSession) -> dict:
    """Posts that newly entered FAILED → Talk alerts room (cursor on failed_at)."""
    from app.services.nextcloud_talk import post_to_talk, talk_enabled

    if not talk_enabled():
        return {"skipped": "talk disabled"}
    last = await _cursor("failed_at")
    q = select(Post).where(Post.status == PostStatus.FAILED, Post.failed_at.isnot(None)).order_by(Post.failed_at)
    rows = (await db.execute(q)).scalars().all()
    if not rows:
        return {"alerts": 0}
    newest = max(p.failed_at.isoformat() for p in rows)
    fresh = [p for p in rows if last is None or p.failed_at.isoformat() > last]
    await _set_cursor("failed_at", newest)
    if last is None:  # first run — seed, don't spam history
        return {"alerts": 0, "seeded": len(rows)}
    sent = 0
    for p in fresh[:10]:
        platform = ", ".join(sorted({t.social_account.platform for t in p.targets if t.social_account})) or "—"
        title = (p.content_text or "")[:80].replace("\n", " ")
        err = next((t.error_message for t in p.targets if t.error_message), "")[:120]
        if await post_to_talk(
            f"⚠️ Publish failed — [{platform}] {title}\n{err}",
            purpose="alerts",
        ):
            sent += 1
    return {"alerts": sent}


# ── Calendar: scheduled posts ──────────────────────────────────────────


async def _primary_calendar(client: httpx.AsyncClient, user: str) -> str | None:
    body = """<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
<d:prop><d:displayname/></d:prop></d:propfind>"""
    r = await client.request(
        "PROPFIND",
        f"{_base()}/remote.php/dav/calendars/{user}/",
        content=body,
        headers={"Depth": "1", "Content-Type": "application/xml"},
    )
    if r.status_code >= 300:
        return None
    import re

    names = re.findall(r"/dav/calendars/[^/]+/([^/</\s]+)/", r.text)
    names = [n for n in names if n not in ("inbox", "outbox")]
    return "personal" if "personal" in names else (names[0] if names else None)


def _ics_for_post(post: Post, platform: str) -> str:
    assert post.scheduled_at is not None
    start = post.scheduled_at.strftime("%Y%m%dT%H%M%SZ")
    uid = f"post-{post.id}@socialauto"
    title = (post.content_text or "")[:60].replace("\n", " ")
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//SocialAuto//EN\r\n"
        "BEGIN:VEVENT\r\n"
        f"UID:{uid}\r\nDTSTAMP:{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}\r\n"
        f"DTSTART:{start}\r\nDURATION:PT15M\r\n"
        f"SUMMARY:[{platform}] {title}\r\n"
        "END:VEVENT\r\nEND:VCALENDAR\r\n"
    )


async def sync_calendar_posts(db: AsyncSession) -> dict:
    """Upcoming scheduled posts → Nextcloud Calendar (idempotent by UID)."""
    from app.models.content import PostTarget

    now = datetime.now(UTC)
    posts = (
        (
            await db.execute(
                select(Post, SocialAccount.platform)
                .join(PostTarget, PostTarget.post_id == Post.id)
                .join(SocialAccount, SocialAccount.id == PostTarget.social_account_id)
                .where(Post.status == PostStatus.SCHEDULED, Post.scheduled_at > now)
                .order_by(Post.scheduled_at)
                .limit(60)
            )
        )
        .all()
    )
    if not posts:
        return {"events": 0}
    user = settings.NEXTCLOUD_USERNAME
    written = 0
    try:
        async with _client() as client:
            cal = await _primary_calendar(client, user)
            if not cal:
                return {"error": "no calendar found"}
            for post, platform in posts:
                ics = _ics_for_post(post, platform)
                r = await client.put(
                    f"{_base()}/remote.php/dav/calendars/{user}/{cal}/post-{post.id}.ics",
                    content=ics,
                    headers={"Content-Type": "text/calendar; charset=utf-8"},
                )
                if r.status_code < 300:
                    written += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("calendar sync failed: %s", exc)
        return {"error": str(exc)[:200]}
    return {"events": written, "calendar": cal}


# ── Forms intake → EspoCRM leads ───────────────────────────────────────


async def sync_forms_intake() -> dict:
    """New Nextcloud Forms submissions → EspoCRM leads (dedupe by submission id)."""
    from app.services import espocrm_client as espo

    if not espo.configured():
        return {"skipped": "espocrm not configured"}
    imported = 0
    try:
        async with _client() as client:
            r = await client.get(f"{_base()}/ocs/v2.php/apps/forms/api/v3/forms?format=json")
            if r.status_code >= 300:
                return {"error": f"forms list HTTP {r.status_code}"}
            forms = r.json().get("ocs", {}).get("data", [])
            for form in forms:
                fid = form["id"]
                last = await _cursor(f"form:{fid}")
                sr = await client.get(f"{_base()}/ocs/v2.php/apps/forms/api/v3/submission/{fid}?format=json")
                if sr.status_code >= 300:
                    continue
                subs = sr.json().get("ocs", {}).get("data", {}).get("submissions", [])
                if not subs and last is not None:
                    continue
                max_seen = last
                for sub in subs:
                    sid = str(sub.get("id"))
                    if last is not None and sid <= last:
                        continue
                    answers = {str(a.get("questionId")): a.get("text") for a in sub.get("answers", [])}
                    email = next((v for v in answers.values() if v and "@" in str(v)), None)
                    if not email:
                        continue
                    name = next((v for v in answers.values() if v and "@" not in str(v)), "")
                    lead_id = await espo.upsert_lead(
                        {
                            "name": name or email,
                            "emailAddress": email,
                            "source": f"nextcloud-form-{fid}",
                            "description": f"Form {fid} submission {sid}: {answers}",
                        }
                    )
                    if lead_id:
                        imported += 1
                    max_seen = sid if (max_seen is None or sid > max_seen) else max_seen
                if subs:
                    newest = max(str(s.get("id")) for s in subs)
                    await _set_cursor(f"form:{fid}", newest)
    except Exception as exc:  # noqa: BLE001
        logger.warning("forms intake failed: %s", exc)
        return {"error": str(exc)[:200]}
    return {"imported": imported}


async def sync_extras(db: AsyncSession) -> dict:
    if not _enabled():
        return {"skipped": "disabled"}
    result = {
        "crm": await sync_crm_pipeline(db),
        "deploys": await sync_deploy_events(),
        "checks": await sync_check_status(),
        "failed": await sync_failed_publishes(db),
        "calendar": await sync_calendar_posts(db),
        "forms": await sync_forms_intake(),
    }
    logger.info("nextcloud extras sync: %s", result)
    return result
