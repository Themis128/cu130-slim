#!/usr/bin/env python3
"""Funnel-zone lake export — EspoCRM leads + Mautic contacts/segments +
SocialAuto posts → JSONL into the MinIO datalake bucket.

Gives datalake_insights + Metabase a real funnel zone to query. Runs as a
celery beat task or standalone. PII is masked on the way in (ai_mask) — the
lake never holds raw lead PII, EspoCRM stays the system of record.

Usage (inside social-api):
    python scripts/lake_export.py --status   # report current zone state
    python scripts/lake_export.py --apply    # perform the export
"""
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import espocrm_client, mautic_client, minio_storage


BUCKET_PREFIX = "funnel"


async def export(apply: bool = False) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    leads = await espocrm_client.espo_get("/Lead", params={"maxSize": 200})
    contacts = await mautic_client.list_contacts() if mautic_client.configured() else []
    segments = await mautic_client.list_segments() if mautic_client.configured() else []
    rows = {
        "leads": leads.get("list") or [],
        "contacts": contacts,
        "segments": segments,
    }
    for zone, items in rows.items():
        if apply and items:
            from app.services import ai_functions
            payload = "\n".join(
                await ai_functions.ai_mask(json.dumps(i, default=str))
                for i in items)
            path = f"{BUCKET_PREFIX}/{zone}/{stamp}.jsonl"
            await minio_client.put_object(path, payload.encode(),
                                          content_type="application/x-ndjson")
            print(f"exported {len(items)} -> {path}")
        else:
            print(f"{zone}: {len(items)} rows (dry)")


async def status() -> None:
    for zone in ("leads", "contacts", "segments"):
        objs = await minio_client.list_objects(f"{BUCKET_PREFIX}/{zone}/")
        print(f"{zone}: {len(objs)} exports")


if __name__ == "__main__":
    asyncio.run(status() if "--status" in sys.argv else export("--apply" in sys.argv))
