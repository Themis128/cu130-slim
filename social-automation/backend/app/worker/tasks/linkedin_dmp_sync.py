"""Daily Matched Audiences sync — EspoCRM leads → LinkedIn DMP segment.

Builds/maintains the ``espo-crm-leads`` USER segment with SHA256-hashed
lead emails so Campaign Manager can retarget warm audiences. Fully
self-gating: until the Matched Audiences product grants ``rw_dmp_segments``
the create/upload calls return 403 and the task logs ``pending-grant``
once per run — no alert spam. Segment URN is cached in Redis; a 404 on
upload triggers one recreate.

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


async def _segment_urn(r) -> str | None:
    urn = r.get(SEGMENT_REDIS_KEY) if r else None
    if urn:
        return urn
    from app.services import linkedin_advanced

    created = await linkedin_advanced.create_dmp_segment(
        SEGMENT_NAME, "EspoCRM leads + clients — Matched Audiences retargeting"
    )
    if created.get("urn"):
        if r:
            r.set(SEGMENT_REDIS_KEY, created["urn"])
        return created["urn"]
    logger.info("dmp segment create: %s (pending grant?)", created)
    return None


async def _sync() -> dict:
    from app.services import espocrm_client, linkedin_advanced
    from app.services.slack_notifications import post_alert_to_slack

    ctx = await linkedin_advanced._ctx()
    if not ctx:
        return {"skip": "linkedin-advanced-disabled"}

    r = _redis()
    urn = await _segment_urn(r)
    if not urn:
        return {"skip": "matched-audiences-not-granted"}

    leads = await espocrm_client.list_leads_for_campaign()
    ids = [
        {"idType": "SHA256_EMAIL", "idValue": h}
        for h in (_sha256(str(lead.get("emailAddress") or "")) for lead in leads)
        if h
    ]
    if not ids:
        return {"skip": "no-lead-emails", "segment": urn}

    uploaded = 0
    last_status = None
    for i in range(0, len(ids), UPLOAD_CHUNK):
        chunk = ids[i : i + UPLOAD_CHUNK]
        # single element whose userIds list carries the whole chunk —
        # LinkedIn counts ids per call, not elements (cap 5000)
        out = await linkedin_advanced.upload_dmp_users(urn, [chunk])
        last_status = out.get("status")
        if out.get("ok"):
            uploaded += len(chunk)
        else:
            logger.warning("dmp upload chunk %d failed: %s", i, out)
            break
    if last_status == 404 and r:
        r.delete(SEGMENT_REDIS_KEY)  # segment gone — recreate next run
    if uploaded and uploaded == len(ids):
        await post_alert_to_slack(f":link: LinkedIn Matched Audiences: {uploaded} hashed lead emails synced to segment {SEGMENT_NAME}")
    return {"segment": urn, "leads": len(leads), "uploaded": uploaded, "status": last_status}


@celery_app.task(name="app.worker.tasks.linkedin_dmp_sync.sync_linkedin_dmp_audiences")
def sync_linkedin_dmp_audiences() -> dict:
    return run_async(_sync())
