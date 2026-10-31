#!/usr/bin/env python3
"""EspoCRM → Mautic contact sync.

Reads EspoCRM contacts (the funnel's CRM of record) and upserts them into
Mautic contacts + a ``crm-sync`` segment, so Mautic's campaigns/sequences/
lead-scoring own the nurture while EspoCRM stays the record layer.

Usage (inside social-api container):
    python scripts/mautic_sync.py                # dry-run — shows what would sync
    python scripts/mautic_sync.py --apply        # performs the upserts
    python scripts/mautic_sync.py --status       # mautic funnel_state snapshot
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import espocrm_client, mautic_client


async def sync(apply: bool = False) -> None:
    if not mautic_client.configured():
        print("mautic not configured (MAUTIC_BASE_URL/USERNAME/PASSWORD)")
        return
    data = await espocrm_client.espo_get("/Lead", params={"maxSize": 200})
    contacts = data.get("list") or []
    print(f"espocrm leads: {data.get('total')} (page {len(contacts)})")
    if not apply:
        for c in contacts[:10]:
            print("  dry:", c.get("emailAddress") or c.get("name"))
        return
    synced = skipped = 0
    seg = await mautic_client.create_segment(
        "CRM Sync", "crm-sync", "Contacts synced from EspoCRM"
    )
    seg_id = (seg.get("list") or {}).get("id") or seg.get("id")
    junk = ("example.com", "test.dev", "test.local", "probe@", "platform-test")
    for c in contacts:
        email = (c.get("emailAddress") or "").strip().lower()
        if not email or any(j in email for j in junk):
            skipped += 1
            continue
        fields = {
            "firstname": c.get("firstName") or "",
            "lastname": c.get("lastName") or "",
            "company": c.get("accountName") or "",
            "title": c.get("title") or "",
            "tags": ["espo-sync", f"espo-status:{(c.get('status') or 'none').lower()}"],
        }
        r = await mautic_client.upsert_contact(email, fields)
        contact_id = (r.get("contact") or {}).get("id")
        if contact_id and seg_id:
            await mautic_client.add_to_segment(int(contact_id), int(seg_id))
        synced += 1
    print(f"synced={synced} skipped={skipped} segment=crm-sync({seg_id})")


async def status() -> None:
    print(await mautic_client.funnel_state())


if __name__ == "__main__":
    if "--status" in sys.argv:
        asyncio.run(status())
    else:
        asyncio.run(sync(apply="--apply" in sys.argv))
