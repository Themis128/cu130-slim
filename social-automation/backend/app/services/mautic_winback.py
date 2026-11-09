"""Mautic win-back sequence — cold-lead reactivation (Day 0 / +5 / +14).

Implements the researched re-engagement playbook (segment by silence,
specific value-drop first touch, single CTA per email, breakup close)
on top of the existing Mautic tier segments:

- Audience: EspoCRM leads whose lifecycle/status tier resolves to ``cold``
  (same resolution as ``scripts/mautic_sync.py`` — lifecycle band mirror →
  SocialAuto hot emails → Espo status).
- Sends go through Mautic ``POST /emails/{id}/contact/{cid}/send`` so opens,
  clicks and replies land in Mautic stats and ``lastActive`` — which the
  funnel-sync win-back bridge turns into Espo engagement notes.
- Cadence state lives in Redis (``wb:seq:{email}``): step index, next-send
  timestamp, terminal status. Engagement (Mautic ``lastActive`` newer than
  the last send) pauses the sequence — the lifecycle scorer owns hot leads.
- Terminal states: ``exhausted`` after the breakup email, ``engaged`` on
  detected activity. Both suppress re-sends for 180 days.

Email templates are created once by ``scripts/mautic_winback_setup.py``
(idempotent by name); their IDs are cached in Redis ``wb:email_ids``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.services import espocrm_client, mautic_client

logger = logging.getLogger(__name__)

# (day offset from sequence start, email slot)
_STEPS: tuple[tuple[int, int], ...] = ((0, 0), (5, 1), (14, 2))
_TERMINAL_TTL = 180 * 24 * 3600
_STATE_TTL = 60 * 24 * 3600
_EMAIL_NAMES = (
    "Win-back 1: the 30-minute audit",
    "Win-back 2: what the funnel data actually shows",
    "Win-back 3: should I close your file",
)
# Internal/test surfaces — never win-back these. ``@cloudless.gr`` is our own
# sending domain; the owner mailboxes must never enter production sequences
# (same protection as email_drip's _TEST_MARKERS).
_JUNK = (
    "example.com", "example.invalid", "test.dev", "test.local", ".local",
    "probe@", "platform-test", "ci-test", "@cloudless.gr",
    "themis.baltzakis", "baltzakis.themis", "tbaltzakis@",
)


def _redis():
    import redis.asyncio as aioredis

    url = os.environ.get("REDIS_URL") or get_settings().REDIS_URL
    if not url:
        return None
    return aioredis.from_url(url, decode_responses=True)


def _state_key(email: str) -> str:
    return f"wb:seq:{email}"


def _parse_ts(raw: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


async def _lifecycle_bands(lead_ids: list[str]) -> dict[str, str]:
    r = await _redis()
    if r is None or not lead_ids:
        return {}
    try:
        vals = await r.mget([f"espo:leadscore:{lid}" for lid in lead_ids])
        out = {}
        for lid, raw in zip(lead_ids, vals):
            if not raw:
                continue
            try:
                band = str(json.loads(raw).get("band") or "").lower()
            except Exception:
                continue
            if band in ("hot", "warm", "cold"):
                out[lid] = band
        return out
    finally:
        await r.aclose()


async def _socialauto_hot_emails() -> set[str]:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        return set()
    try:
        import asyncpg

        dsn = url.replace("postgresql+asyncpg://", "postgresql://")
        conn = await asyncpg.connect(dsn)
        try:
            rows = await conn.fetch("""
                SELECT email FROM leads
                WHERE interest = 'audit' OR thread_id IS NOT NULL
                   OR updated_at > now() - interval '7 days'""")
            return {(r["email"] or "").strip().lower() for r in rows if r["email"]}
        finally:
            await conn.close()
    except Exception:
        return set()


def _tier(lead: dict, email: str, hot_emails: set[str], bands: dict[str, str]) -> str:
    band = bands.get(str(lead.get("id") or ""))
    if band:
        return band
    if email in hot_emails:
        return "hot"
    return {"converted": "hot", "in process": "hot", "assigned": "warm", "new": "warm"}.get(
        (lead.get("status") or "").strip().lower(), "cold"
    )


async def _email_ids() -> dict[str, int]:
    """Map email-slot index → Mautic email ID. Redis-cached; self-heals by
    matching template names from ``GET /emails``."""
    r = await _redis()
    if r is None:
        return {}
    try:
        cached = await r.get("wb:email_ids")
        if cached:
            data = json.loads(cached)
            if all(str(i) in data for i in range(len(_EMAIL_NAMES))):
                return {k: int(v) for k, v in data.items()}
        emails = await mautic_client.mautic_get("/emails", params={"limit": 200})
        by_name = {}
        for e in (emails.get("emails") or {}).values() if isinstance(emails.get("emails"), dict) else emails.get("emails") or []:
            by_name[str(e.get("name") or "")] = int(e.get("id"))
        ids = {}
        for i, name in enumerate(_EMAIL_NAMES):
            # exact or prefix match (setup may suffix the name)
            eid = by_name.get(name)
            if eid is None:
                for n, e in by_name.items():
                    if n.startswith(name):
                        eid = e
                        break
            if eid:
                ids[str(i)] = eid
        if len(ids) == len(_EMAIL_NAMES):
            await r.set("wb:email_ids", json.dumps(ids))
        return ids
    finally:
        await r.aclose()


async def _mautic_contact_id(email: str) -> int | None:
    data = await mautic_client.mautic_get("/contacts", params={"search": f"email:{email}", "minimal": "true"})
    contacts = data.get("contacts") or {}
    items = list(contacts.values()) if isinstance(contacts, dict) else contacts
    for c in items:
        if str(c.get("email") or "").strip().lower() == email:
            return int(c.get("id"))
    return None


async def _mautic_last_active(contact_id: int) -> datetime | None:
    data = await mautic_client.mautic_get(f"/contacts/{contact_id}")
    contact = data.get("contact") or {}
    return _parse_ts(str(contact.get("lastActive") or ""))


async def run_mautic_winback_sweep(*, dry_run: bool = False, limit: int = 1000) -> dict[str, Any]:
    """Advance every cold lead's win-back sequence by at most one step."""
    stats: dict[str, Any] = {"scanned": 0, "sent": 0, "skipped_tier": 0, "skipped_state": 0,
                             "skipped_engaged": 0, "skipped_no_contact": 0, "errors": 0, "dry_run": dry_run}
    leads = await espocrm_client.list_all(
        "Lead",
        select=["id", "emailAddress", "status", "source", "firstName", "lastName"],
        max_items=limit,
    )
    leads = [lead for lead in leads if lead.get("emailAddress")]
    stats["scanned"] = len(leads)
    bands = await _lifecycle_bands([str(lead.get("id")) for lead in leads])
    hot_emails = await _socialauto_hot_emails()
    email_ids = await _email_ids()
    r = await _redis()
    if r is None:
        stats["errors"] += 1
        return stats
    try:
        now = datetime.now(UTC)
        for lead in leads:
            email = str(lead.get("emailAddress") or "").strip().lower()
            if not email or any(j in email for j in _JUNK):
                continue
            if _tier(lead, email, hot_emails, bands) != "cold":
                stats["skipped_tier"] += 1
                continue
            key = _state_key(email)
            raw = await r.get(key)
            state = json.loads(raw) if raw else {"step": -1, "next_at": None, "last_sent": None}
            if state.get("status") in ("exhausted", "engaged"):
                stats["skipped_state"] += 1
                continue
            # pick the next due step
            due = None
            for offset, slot in _STEPS:
                if slot <= state.get("step", -1):
                    continue
                if state.get("step", -1) == -1:
                    due = (offset, slot)
                    break
                next_at = _parse_ts(state.get("next_at") or "")
                started = _parse_ts(state.get("started_at") or "")
                if started and now >= started + timedelta(days=offset) and (next_at is None or now >= next_at):
                    due = (offset, slot)
                    break
            if due is None:
                stats["skipped_state"] += 1
                continue
            offset, slot = due
            eid = email_ids.get(str(slot))
            if not eid:
                stats["errors"] += 1
                continue
            try:
                cid = await _mautic_contact_id(email)
                if not cid:
                    stats["skipped_no_contact"] += 1
                    continue
                # engagement check: activity after the last send pauses the seq
                last_sent = _parse_ts(state.get("last_sent") or "")
                last_active = await _mautic_last_active(cid)
                if last_sent and last_active and last_active > last_sent:
                    await r.set(key, json.dumps({"status": "engaged", "at": now.isoformat()}), ex=_TERMINAL_TTL)
                    stats["skipped_engaged"] += 1
                    continue
                if dry_run:
                    stats["sent"] += 1
                    continue
                resp = await mautic_client.mautic_post(f"/emails/{eid}/contact/{cid}/send", {})
                if resp.get("success") or resp.get("sent"):
                    new_state = {
                        "step": slot,
                        "started_at": state.get("started_at") or now.isoformat(),
                        "last_sent": now.isoformat(),
                        "next_at": (now + timedelta(days=(_STEPS[slot + 1][0] - offset) if slot + 1 < len(_STEPS) else 0)).isoformat(),
                    }
                    if slot == len(_EMAIL_NAMES) - 1:
                        new_state["status"] = "exhausted"
                    await r.set(key, json.dumps(new_state), ex=_TERMINAL_TTL if new_state.get("status") else _STATE_TTL)
                    stats["sent"] += 1
                else:
                    stats["errors"] += 1
                    logger.warning("winback send failed for %s: %s", email, str(resp)[:200])
            except Exception:
                logger.exception("winback: lead %s failed", lead.get("id"))
                stats["errors"] += 1
        return stats
    finally:
        await r.aclose()
