#!/usr/bin/env python3
"""EspoCRM → Mautic contact sync + win-back engagement bridge.

Reads EspoCRM leads (the funnel's CRM of record) and upserts them into
Mautic contacts + tier segments (``crm-hot`` / ``crm-warm`` / ``crm-cold``),
so Mautic's campaigns own the nurture while EspoCRM stays the record layer.

Tier resolution order:
  1. Lifecycle band mirrored to Redis by ``crm_lifecycle`` (``espo:leadscore:*``)
  2. SocialAuto hot-lead emails (interest=audit / active thread / 7d touch)
  3. Espo status fallback (Converted/In Process → hot, Assigned/New → warm)

Tier churn is reconciled: when a contact's tier changes it is removed from
the stale tier segments (previously segments only ever grew).

Win-back bridge: contacts with recent Mautic ``lastActive`` get a
``Win-back engagement`` note on the matching Espo lead — the lifecycle
sweep treats that marker like a reply, re-scores, and converts if hot.

Usage (inside social-api container):
    python scripts/mautic_sync.py                # dry-run — shows what would sync
    python scripts/mautic_sync.py --apply        # performs the upserts
    python scripts/mautic_sync.py --status       # mautic funnel_state snapshot
"""

import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import espocrm_client, mautic_client
from app.services.crm_lifecycle import WINBACK_MARKER

_ESPO_TIER = {
    "converted": "hot",
    "in process": "hot",
    "assigned": "warm",
    "new": "warm",
}
_TIER_ORDER = ("hot", "warm", "cold")
_WINBACK_WINDOW_DAYS = 14
_WINBACK_NOTE_TTL = 30 * 24 * 3600


async def _redis():
    import redis.asyncio as aioredis

    from app.core.config import get_settings

    url = os.environ.get("REDIS_URL") or get_settings().REDIS_URL
    if not url:
        return None
    return aioredis.from_url(url, decode_responses=True)


async def _socialauto_hot_emails() -> set[str]:
    """Emails of SocialAuto leads scored hot (interest=audit, active thread,
    recent touch) — forces the Mautic tier up regardless of Espo status."""
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


async def _lifecycle_bands(lead_ids: list[str]) -> dict[str, str]:
    """lead_id → hot/warm/cold from the Redis score mirror (empty on miss)."""
    r = await _redis()
    if r is None or not lead_ids:
        return {}
    try:
        vals = await r.mget([f"espo:leadscore:{lid}" for lid in lead_ids])
        out: dict[str, str] = {}
        for lid, raw in zip(lead_ids, vals):
            if not raw:
                continue
            try:
                band = str(json.loads(raw).get("band") or "").lower()
            except Exception:
                continue
            if band in _TIER_ORDER:
                out[lid] = band
        return out
    finally:
        await r.aclose()


def _tier(lead: dict, email: str, hot_emails: set[str], bands: dict[str, str]) -> str:
    band = bands.get(str(lead.get("id") or ""))
    if band:
        return band
    if email in hot_emails:
        return "hot"
    return _ESPO_TIER.get((lead.get("status") or "").strip().lower(), "cold")


async def _reconcile_tier(
    r,
    contact_id: int,
    email: str,
    tier: str,
    tier_segs: dict[str, int],
    stats: dict[str, int],
) -> None:
    """Ensure the contact sits only in its current tier segment."""
    key = f"mautic:tier:{email}"
    last = await r.get(key) if r else None
    if last == tier:
        return
    for t, seg_id in tier_segs.items():
        if t == tier or not seg_id:
            continue
        try:
            await mautic_client.remove_from_segment(contact_id, seg_id)
            stats["tier_removed"] += 1
        except Exception:
            pass
    await r.set(key, tier)
    stats["tier_changed"] += 1


async def _winback_bridge(leads: list[dict], stats: dict[str, int]) -> None:
    """Mautic lastActive → Espo 'Win-back engagement' note → lifecycle rescore.

    Dedupe via Redis ``wb:noted:{email}`` so a contact can't re-trigger the
    note every hour; the note itself is what ``_needs_score`` keys on.
    """
    r = await _redis()
    if r is None:
        return
    try:
        cutoff = datetime.now(UTC) - timedelta(days=_WINBACK_WINDOW_DAYS)
        active = await mautic_client.list_recently_active_contacts(limit=100)
        by_email = {(lead.get("emailAddress") or "").strip().lower(): str(lead.get("id")) for lead in leads if lead.get("emailAddress")}
        noted = 0
        for c in active:
            email = str(c.get("email") or "").strip().lower()
            lid = by_email.get(email)
            if not lid or await r.exists(f"wb:noted:{email}"):
                continue
            last_active = str(c.get("lastActive") or "")
            try:
                ts = datetime.fromisoformat(last_active.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=UTC)
            except ValueError:
                continue
            if ts < cutoff:
                continue
            note = f"{WINBACK_MARKER}: Mautic activity {last_active} (campaign/segment engagement detected by funnel-sync)"
            try:
                from app.services import espocrm_client as espo

                await espo.add_lead_note(lid, note)
                await r.set(f"wb:noted:{email}", "1", ex=_WINBACK_NOTE_TTL)
                noted += 1
            except Exception:
                continue
        stats["winback_notes"] = noted
        if noted:
            try:
                from app.worker.tasks.crm_lifecycle import run_crm_lifecycle_sweep

                run_crm_lifecycle_sweep.delay()
            except Exception:
                pass
    finally:
        await r.aclose()


async def sync(apply: bool = False) -> None:
    if not mautic_client.configured():
        print("mautic not configured (MAUTIC_BASE_URL/MAUTIC_USERNAME/MAUTIC_PASSWORD)")
        return
    contacts = await espocrm_client.list_all(
        "Lead",
        select=["id", "emailAddress", "firstName", "lastName", "accountName", "title", "status"],
        max_items=1000,
    )
    print(f"espocrm leads: {len(contacts)}")
    if not apply:
        for c in contacts[:10]:
            print("  dry:", c.get("emailAddress") or c.get("name"))
        return
    synced = skipped = 0
    tiers: dict[str, int] = {"hot": 0, "warm": 0, "cold": 0}
    stats = {"tier_changed": 0, "tier_removed": 0, "winback_notes": 0}
    seg = await mautic_client.create_segment("CRM Sync", "crm-sync", "Contacts synced from EspoCRM")
    seg_id = (seg.get("list") or {}).get("id") or seg.get("id")
    tier_segs: dict[str, int] = {}
    for t in _TIER_ORDER:
        ts = await mautic_client.create_segment(f"CRM {t.title()}", f"crm-{t}", f"EspoCRM leads scored {t}")
        tid = (ts.get("list") or {}).get("id") or ts.get("id")
        if tid:
            tier_segs[t] = int(tid)
    hot_emails = await _socialauto_hot_emails()
    bands = await _lifecycle_bands([str(c.get("id")) for c in contacts])
    junk = (
        "example.com",
        "example.invalid",
        "test.dev",
        "test.local",
        ".local",
        "probe@",
        "platform-test",
        "ci-test",
        "@cloudless.gr",
        "themis.baltzakis",
        "baltzakis.themis",
        "tbaltzakis@",
    )
    r = await _redis()
    try:
        for c in contacts:
            email = (c.get("emailAddress") or "").strip().lower()
            if not email or any(j in email for j in junk):
                skipped += 1
                continue
            tier = _tier(c, email, hot_emails, bands)
            fields = {
                "firstname": c.get("firstName") or "",
                "lastname": c.get("lastName") or "",
                "company": c.get("accountName") or "",
                "title": c.get("title") or "",
                "tags": ["espo-sync", f"espo-status:{(c.get('status') or 'none').lower()}", f"tier:{tier}"],
            }
            resp = await mautic_client.upsert_contact(email, fields)
            contact_id = (resp.get("contact") or {}).get("id")
            if contact_id and seg_id:
                await mautic_client.add_to_segment(int(contact_id), int(seg_id))
                tid = tier_segs.get(tier)
                if tid:
                    await mautic_client.add_to_segment(int(contact_id), tid)
                    if r is not None:
                        await _reconcile_tier(r, int(contact_id), email, tier, tier_segs, stats)
            tiers[tier] += 1
            synced += 1
    finally:
        if r is not None:
            await r.aclose()
    await _winback_bridge(contacts, stats)
    print(
        f"synced={synced} skipped={skipped} segment=crm-sync({seg_id}) tiers={tiers} "
        f"tier_changed={stats['tier_changed']} tier_removed={stats['tier_removed']} "
        f"winback_notes={stats['winback_notes']}"
    )


async def status() -> None:
    print(await mautic_client.funnel_state())


if __name__ == "__main__":
    if "--status" in sys.argv:
        asyncio.run(status())
    else:
        asyncio.run(sync(apply="--apply" in sys.argv))
