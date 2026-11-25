"""Daily Matched Audiences sync — EspoCRM leads → LinkedIn DMP segment.

Builds/maintains the ``espo-crm-leads`` USER segment with SHA256-hashed
lead emails so Campaign Manager can retarget warm audiences. Matched
Audiences is LIVE (granted 2026-11-16); a 403 still logs
``pending-grant`` once per run — no alert spam. Segment URN is cached in
Redis; a 404 on upload triggers one recreate.

Privacy: only SHA256(lower(trim(email))) leaves this system — no raw PII.
"""

import hashlib
import logging

from app.worker._async import run_async
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

SEGMENT_NAME = "espo-crm-leads"
SEGMENT_REDIS_KEY = "linkedin:dmp:espo-crm-leads-urn"
UPLOAD_CHUNK = 5000  # LinkedIn DMP users API cap per call
PROFILE_CHUNK = 1000  # profile-field elements per batch call

# Synthetic addresses captured from messenger/webhook lanes must never be
# uploaded — they can't match a LinkedIn member and pollute the segment.
_JUNK_EMAIL_TLDS = (".local", ".dev", ".test", ".invalid", ".example")
_JUNK_NAMES = {"", "probe", "verify", "test", "testing", "e2e", "webhook", "demo", "unknown"}
_JUNK_TITLES = {"", "--", "-", "n/a", "na", "none", "null", "test"}


def _clean(v: object, max_len: int) -> str:
    return " ".join(str(v or "").split())[:max_len]


def _usable_email(raw: object) -> str | None:
    e = str(raw or "").strip().lower()
    if "@" not in e or e.endswith(_JUNK_EMAIL_TLDS):
        return None
    return e


def _profile_element(person: dict) -> dict | None:
    """One DMP element per person: firstName+lastName required, title/company
    optional (LinkedIn schema caps: 35/35/100/50 chars)."""
    first = _clean(person.get("firstName"), 35)
    last = _clean(person.get("lastName"), 35)
    if first.lower() in _JUNK_NAMES or last.lower() in _JUNK_NAMES or not first or not last:
        return None
    el = {"action": "ADD", "firstName": first, "lastName": last}
    title = _clean(person.get("title"), 100)
    if title.lower() not in _JUNK_TITLES:
        el["title"] = title
    company = _clean(person.get("accountName"), 50)
    if company:
        el["company"] = company
    return el


def _redis():
    try:
        import redis

        from app.core.config import get_settings

        return redis.Redis.from_url(get_settings().REDIS_URL, decode_responses=True)
    except Exception:  # noqa: BLE001
        return None


def _sha256(email: str) -> str | None:
    e = (email or "").strip().lower()
    if not e or "@" not in e:
        return None
    return hashlib.sha256(e.encode()).hexdigest()


async def _segment_urn(r) -> tuple[str | None, bool]:
    urn = r.get(SEGMENT_REDIS_KEY) if r else None
    if urn:
        return urn, False
    from app.services import linkedin_advanced

    created = await linkedin_advanced.create_dmp_segment(SEGMENT_NAME, "EspoCRM leads + clients — Matched Audiences retargeting")
    if created.get("urn"):
        if r:
            r.set(SEGMENT_REDIS_KEY, created["urn"])
        return created["urn"], True
    logger.info("dmp segment create: %s (pending grant?)", created)
    return None, False


async def _sync() -> dict:
    from app.services import espocrm_client, linkedin_advanced
    from app.services.slack_notifications import post_alert_to_slack

    ctx = await linkedin_advanced._ctx()
    if not ctx:
        return {"skip": "linkedin-advanced-disabled"}

    r = _redis()
    urn, fresh = await _segment_urn(r)
    if not urn:
        return {"skip": "matched-audiences-not-granted"}
    if fresh:
        # LinkedIn requires ~5s before a new segment accepts user uploads
        import asyncio

        await asyncio.sleep(5)

    # All non-Dead/non-Converted leads — most social-captured leads have no
    # email, so profile-field matching (first+last name) carries the audience.
    lead_select = ["id", "firstName", "lastName", "title", "accountName", "emailAddress"]
    lead_where = [
        {"type": "notEquals", "attribute": "status", "value": "Dead"},
        {"type": "notEquals", "attribute": "status", "value": "Converted"},
    ]
    leads = await espocrm_client.list_all("Lead", select=lead_select, where=lead_where)
    contacts = await espocrm_client.list_contacts()
    people = [*leads, *contacts]

    ids = [{"idType": "SHA256_EMAIL", "idValue": h} for h in (_sha256(_usable_email(p.get("emailAddress")) or "") for p in people) if h]
    elements = [el for el in (_profile_element(p) for p in people) if el]
    if not ids and not elements:
        return {"skip": "no-usable-identifiers", "segment": urn}

    uploaded_ids = 0
    last_status = None
    for i in range(0, len(ids), UPLOAD_CHUNK):
        chunk = ids[i : i + UPLOAD_CHUNK]
        # single element whose userIds list carries the whole chunk —
        # LinkedIn counts ids per call, not elements (cap 5000)
        out = await linkedin_advanced.upload_dmp_users(urn, [chunk])
        last_status = out.get("status")
        if out.get("ok"):
            uploaded_ids += len(chunk)
        else:
            logger.warning("dmp upload chunk %d failed: %s", i, out)
            break

    uploaded_profiles = 0
    for i in range(0, len(elements), PROFILE_CHUNK):
        chunk = elements[i : i + PROFILE_CHUNK]
        out = await linkedin_advanced.upload_dmp_elements(urn, chunk)
        last_status = out.get("status")
        if out.get("ok"):
            uploaded_profiles += len(chunk)
        else:
            logger.warning("dmp profile chunk %d failed: %s", i, out)
            break

    if last_status == 404 and r:
        r.delete(SEGMENT_REDIS_KEY)  # segment gone — recreate next run
    if (uploaded_ids and uploaded_ids == len(ids)) or (uploaded_profiles and uploaded_profiles == len(elements)):
        await post_alert_to_slack(
            f":link: LinkedIn Matched Audiences: {uploaded_ids} hashed emails + {uploaded_profiles} name profiles synced to segment {SEGMENT_NAME}"
        )
    return {
        "segment": urn,
        "leads": len(leads),
        "contacts": len(contacts),
        "emails": len(ids),
        "emails_uploaded": uploaded_ids,
        "profiles": len(elements),
        "profiles_uploaded": uploaded_profiles,
        "status": last_status,
    }


@celery_app.task(name="app.worker.tasks.linkedin_dmp_sync.sync_linkedin_dmp_audiences")
def sync_linkedin_dmp_audiences() -> dict:
    return run_async(_sync())
