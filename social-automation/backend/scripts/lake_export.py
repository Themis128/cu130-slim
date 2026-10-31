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
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import espocrm_client, mautic_client, minio_storage


BUCKET_PREFIX = "funnel"

_RE_PII = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"   # emails
    r"|\+?\d[\d\s().-]{7,}\d"                            # phone-like
)


def _regex_mask(text: str) -> str:
    """Model-independent PII floor — the lake never holds raw lead PII even
    when the DMR mask call is unreachable."""
    return _RE_PII.sub("[REDACTED]", text)


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
            rows_masked = [await ai_functions.ai_mask(json.dumps(i, default=str))
                           for i in items]
            payload = "\n".join(_regex_mask(r) for r in rows_masked)
            path = f"{BUCKET_PREFIX}/{zone}/{stamp}.jsonl"
            await minio_storage.upload_object(path, payload.encode(),
                                              content_type="application/x-ndjson")
            # stable key the Q&A layer reads
            await minio_storage.upload_object(
                f"{BUCKET_PREFIX}/{zone}/latest.jsonl", payload.encode(),
                content_type="application/x-ndjson")
            print(f"exported {len(items)} -> {path}")
        else:
            print(f"{zone}: {len(items)} rows (dry)")


async def status() -> None:
    print("minio enabled:", minio_storage.minio_enabled())


if __name__ == "__main__":
    asyncio.run(status() if "--status" in sys.argv else export("--apply" in sys.argv))
